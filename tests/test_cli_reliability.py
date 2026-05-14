"""Tests for CLI reliability features: socket timeouts and degraded-mode email.

Regression coverage for the May 5 / Apr 28 2026 hangs (root cause: missing
HTTP timeouts) and Joe's hard requirement that he never sees a day with no
email at all.
"""

import socket
import time
from unittest.mock import MagicMock, patch

import pytest

from market_mover import cli
from market_mover.models import RawArticle, SourceType


class TestGatherArticlesErrorHandling:
    """The gather step must capture per-source exceptions instead of propagating."""

    def test_socket_timeout_in_one_source_is_captured(self, mock_settings):
        with patch.object(
            cli, "fetch_newsapi_articles", side_effect=socket.timeout("read timed out")
        ), patch.object(cli, "fetch_finnhub_articles", return_value=[]), patch.object(
            cli, "fetch_rss_articles", return_value=[]
        ), patch.object(cli, "fetch_youtube_videos", return_value=[]):
            articles, errors = cli._gather_articles(mock_settings)

        assert articles == []
        assert "NewsAPI" in errors
        assert "timeout" in errors["NewsAPI"].lower()

    def test_one_failing_source_does_not_break_other_sources(self, mock_settings):
        good_article = RawArticle(
            title="Good",
            url="https://example.com/good",
            source_name="Test",
            source_type=SourceType.RSS,
        )
        with patch.object(
            cli, "fetch_newsapi_articles", side_effect=socket.timeout("boom")
        ), patch.object(cli, "fetch_finnhub_articles", return_value=[good_article]), patch.object(
            cli, "fetch_rss_articles", return_value=[]
        ), patch.object(cli, "fetch_youtube_videos", return_value=[]):
            articles, errors = cli._gather_articles(mock_settings)

        assert len(articles) == 1
        assert articles[0].title == "Good"
        assert "NewsAPI" in errors
        assert "Finnhub" not in errors

    def test_no_errors_when_all_sources_succeed(self, mock_settings):
        with patch.object(cli, "fetch_newsapi_articles", return_value=[]), patch.object(
            cli, "fetch_finnhub_articles", return_value=[]
        ), patch.object(cli, "fetch_rss_articles", return_value=[]), patch.object(
            cli, "fetch_youtube_videos", return_value=[]
        ):
            articles, errors = cli._gather_articles(mock_settings)

        assert articles == []
        assert errors == {}


class TestDegradedModeEmail:
    """When zero articles are gathered, send a degraded-mode email — never exit silently."""

    @patch("market_mover.cli.send_email")
    def test_degraded_email_sent_when_zero_articles(self, mock_send_email, mock_settings):
        mock_send_email.return_value = True
        with patch.object(cli, "fetch_newsapi_articles", return_value=[]), patch.object(
            cli, "fetch_finnhub_articles", return_value=[]
        ), patch.object(cli, "fetch_rss_articles", return_value=[]), patch.object(
            cli, "fetch_youtube_videos", return_value=[]
        ), patch("market_mover.cli.MarketMoverSettings", return_value=mock_settings):
            # Should not raise SystemExit since degraded email succeeded
            cli.run_pipeline()

        assert mock_send_email.called
        call_kwargs = mock_send_email.call_args.kwargs
        assert "DEGRADED" in call_kwargs["subject"]
        # The degraded marker should appear in both bodies for downstream filtering
        assert "DEGRADED" in call_kwargs["plain_text"]
        assert "DEGRADED" in call_kwargs["html_body"]

    @patch("market_mover.cli.send_email")
    def test_degraded_email_lists_source_errors(self, mock_send_email, mock_settings):
        mock_send_email.return_value = True
        with patch.object(
            cli, "fetch_newsapi_articles", side_effect=socket.timeout("NewsAPI hung")
        ), patch.object(
            cli, "fetch_finnhub_articles", side_effect=RuntimeError("Finnhub 503")
        ), patch.object(cli, "fetch_rss_articles", return_value=[]), patch.object(
            cli, "fetch_youtube_videos", return_value=[]
        ), patch("market_mover.cli.MarketMoverSettings", return_value=mock_settings):
            cli.run_pipeline()

        call_kwargs = mock_send_email.call_args.kwargs
        plain_text = call_kwargs["plain_text"]
        assert "NewsAPI" in plain_text
        assert "Finnhub" in plain_text
        assert "NewsAPI hung" in plain_text or "timeout" in plain_text.lower()
        assert "Finnhub 503" in plain_text

    @patch("market_mover.cli.send_email")
    def test_exits_only_when_degraded_email_also_fails(self, mock_send_email, mock_settings):
        mock_send_email.return_value = False
        with patch.object(cli, "fetch_newsapi_articles", return_value=[]), patch.object(
            cli, "fetch_finnhub_articles", return_value=[]
        ), patch.object(cli, "fetch_rss_articles", return_value=[]), patch.object(
            cli, "fetch_youtube_videos", return_value=[]
        ), patch("market_mover.cli.MarketMoverSettings", return_value=mock_settings):
            with pytest.raises(SystemExit):
                cli.run_pipeline()


class TestParallelGather:
    """The gather step fans the 4 sources out in parallel via ThreadPoolExecutor."""

    def test_sources_are_fetched_in_parallel(self, mock_settings):
        """Four sources that each sleep 1s should finish in < 1.5s when run
        in parallel (vs ~4s sequential). Proves the executor is wired up."""

        def slow_fetcher(*_args, **_kwargs):
            time.sleep(1.0)
            return []

        with patch.object(cli, "fetch_newsapi_articles", side_effect=slow_fetcher), patch.object(
            cli, "fetch_finnhub_articles", side_effect=slow_fetcher
        ), patch.object(cli, "fetch_rss_articles", side_effect=slow_fetcher), patch.object(
            cli, "fetch_youtube_videos", side_effect=slow_fetcher
        ):
            start = time.monotonic()
            articles, errors = cli._gather_articles(mock_settings)
            elapsed = time.monotonic() - start

        assert articles == []
        assert errors == {}
        assert elapsed < 1.5, (
            f"_gather_articles took {elapsed:.2f}s — sources are not running in parallel"
        )


class TestSocketDefaultTimeout:
    """Module-level socket.setdefaulttimeout backstop must be active after import."""

    def test_socket_default_timeout_is_set(self):
        # cli.py sets socket.setdefaulttimeout(30) at import time
        assert socket.getdefaulttimeout() is not None
        assert socket.getdefaulttimeout() <= 30


class TestEmailSenderTimeout:
    """smtplib.SMTP must be invoked with an explicit timeout."""

    @patch("market_mover.email_sender.smtplib.SMTP")
    def test_smtp_called_with_timeout(self, mock_smtp_class, mock_settings):
        mock_settings.smtp_username = "u@gmail.com"
        mock_settings.smtp_app_password = "pw"

        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        from market_mover.email_sender import send_email

        send_email(
            subject="t",
            html_body="<p>t</p>",
            plain_text="t",
            recipients=["x@example.com"],
            settings=mock_settings,
        )

        # Confirm the SMTP constructor received a finite timeout
        args, kwargs = mock_smtp_class.call_args
        timeout_val = kwargs.get("timeout")
        if timeout_val is None and len(args) >= 3:
            timeout_val = args[2]
        assert timeout_val is not None
        assert timeout_val <= 30

"""Tests for news source fetchers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


from market_mover.models import SourceType
from market_mover.sources.newsapi_source import fetch_newsapi_articles
from market_mover.sources.finnhub_source import fetch_finnhub_articles
from market_mover.sources.rss_source import fetch_rss_articles, _parse_published_date
from market_mover.sources.youtube_source import fetch_youtube_videos


class TestNewsAPISource:
    def test_returns_empty_without_api_key(self):
        result = fetch_newsapi_articles("")
        assert result == []

    def test_fetches_articles(self):
        mock_api = MagicMock()
        mock_api.get_everything.return_value = {
            "articles": [
                {
                    "title": "Test Article",
                    "url": "https://example.com/test",
                    "source": {"name": "TestSource"},
                    "publishedAt": "2026-04-01T10:00:00Z",
                    "description": "Test description",
                },
            ]
        }
        mock_newsapi_module = MagicMock()
        mock_newsapi_module.NewsApiClient.return_value = mock_api

        with patch.dict("sys.modules", {"newsapi": mock_newsapi_module}):
            result = fetch_newsapi_articles("test-key")

        assert len(result) == 1
        assert result[0].title == "Test Article"
        assert result[0].source_type == SourceType.NEWSAPI
        assert result[0].is_video is False

    def test_handles_api_failure(self):
        mock_newsapi_module = MagicMock()
        mock_newsapi_module.NewsApiClient.return_value.get_everything.side_effect = Exception("API error")

        with patch.dict("sys.modules", {"newsapi": mock_newsapi_module}):
            result = fetch_newsapi_articles("test-key")

        assert result == []


class TestFinnhubSource:
    def test_returns_empty_without_api_key(self):
        result = fetch_finnhub_articles("")
        assert result == []

    def test_fetches_articles(self):
        now_ts = int(datetime.now(timezone.utc).timestamp())
        mock_client = MagicMock()
        mock_client.general_news.return_value = [
            {
                "headline": "Market Rally Continues",
                "url": "https://example.com/rally",
                "source": "MarketWatch",
                "summary": "Stocks rise for third day",
                "datetime": now_ts,
            },
        ]
        mock_finnhub_module = MagicMock()
        mock_finnhub_module.Client.return_value = mock_client

        with patch.dict("sys.modules", {"finnhub": mock_finnhub_module}):
            result = fetch_finnhub_articles("test-key")

        assert len(result) == 1
        assert result[0].title == "Market Rally Continues"
        assert result[0].source_type == SourceType.FINNHUB

    def test_skips_old_articles(self):
        old_ts = int(datetime.now(timezone.utc).timestamp()) - 100000
        mock_client = MagicMock()
        mock_client.general_news.return_value = [
            {
                "headline": "Old News",
                "url": "https://example.com/old",
                "source": "OldSource",
                "datetime": old_ts,
            },
        ]
        mock_finnhub_module = MagicMock()
        mock_finnhub_module.Client.return_value = mock_client

        with patch.dict("sys.modules", {"finnhub": mock_finnhub_module}):
            result = fetch_finnhub_articles("test-key")

        assert result == []


class TestRSSSource:
    def test_returns_empty_without_urls(self):
        result = fetch_rss_articles([])
        assert result == []

    def test_fetches_articles(self):
        now_struct = datetime.now(timezone.utc).timetuple()

        mock_feed = MagicMock()
        mock_feed.feed = MagicMock()
        mock_feed.feed.get.return_value = "Test Feed"
        mock_entry = MagicMock()
        mock_entry.get = lambda key, default="": {
            "title": "Breaking Market News",
            "link": "https://example.com/breaking",
            "summary": "Markets react to news",
            "published_parsed": now_struct,
        }.get(key, default)
        mock_entry.__getitem__ = lambda self_, key: {
            "title": "Breaking Market News",
            "link": "https://example.com/breaking",
            "summary": "Markets react to news",
            "published_parsed": now_struct,
        }[key]
        mock_entry.__contains__ = lambda self_, key: key in {
            "title", "link", "summary", "published_parsed"
        }
        mock_feed.entries = [mock_entry]

        mock_feedparser = MagicMock()
        mock_feedparser.parse.return_value = mock_feed

        with patch.dict("sys.modules", {"feedparser": mock_feedparser}):
            result = fetch_rss_articles(["https://example.com/rss"])

        assert len(result) == 1
        assert result[0].title == "Breaking Market News"
        assert result[0].source_type == SourceType.RSS

    def test_parse_published_date_with_struct_time(self):
        now = datetime.now(timezone.utc)
        entry = {"published_parsed": now.timetuple()}
        result = _parse_published_date(entry)
        assert result is not None
        assert abs((result - now).total_seconds()) < 2

    def test_parse_published_date_returns_none_for_empty(self):
        result = _parse_published_date({})
        assert result is None


class TestYouTubeSource:
    def test_returns_empty_without_api_key(self):
        result = fetch_youtube_videos("", ["channel1"])
        assert result == []

    def test_returns_empty_without_channels(self):
        result = fetch_youtube_videos("test-key", [])
        assert result == []

    def test_fetches_videos(self):
        mock_request = MagicMock()
        mock_request.execute.return_value = {
            "items": [
                {
                    "id": {"videoId": "abc123"},
                    "snippet": {
                        "title": "Market Crash Analysis",
                        "channelTitle": "CNBC",
                        "publishedAt": "2026-04-01T10:00:00Z",
                        "description": "Today's market analysis",
                    },
                },
            ]
        }
        mock_search = MagicMock()
        mock_search.list.return_value = mock_request
        mock_youtube = MagicMock()
        mock_youtube.search.return_value = mock_search

        mock_build = MagicMock(return_value=mock_youtube)

        with patch("googleapiclient.discovery.build", mock_build):
            result = fetch_youtube_videos("test-key", ["UCtest"])

        assert len(result) == 1
        assert result[0].title == "Market Crash Analysis"
        assert result[0].is_video is True
        assert result[0].url == "https://www.youtube.com/watch?v=abc123"
        assert result[0].source_type == SourceType.YOUTUBE

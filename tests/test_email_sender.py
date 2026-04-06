"""Tests for the SMTP email sender."""

from unittest.mock import MagicMock, patch

from market_mover.email_sender import send_email


class TestEmailSender:
    def test_returns_false_without_smtp_credentials(self, mock_settings):
        mock_settings.smtp_username = ""
        mock_settings.smtp_app_password = ""
        result = send_email(
            subject="Test",
            html_body="<p>test</p>",
            plain_text="test",
            recipients=["test@example.com"],
            settings=mock_settings,
        )
        assert result is False

    def test_returns_false_without_recipients(self, mock_settings):
        mock_settings.smtp_username = "user@gmail.com"
        mock_settings.smtp_app_password = "app-password"
        result = send_email(
            subject="Test",
            html_body="<p>test</p>",
            plain_text="test",
            recipients=[],
            settings=mock_settings,
        )
        assert result is False

    @patch("market_mover.email_sender.smtplib.SMTP")
    def test_sends_email_successfully(self, mock_smtp_class, mock_settings):
        mock_settings.smtp_username = "user@gmail.com"
        mock_settings.smtp_app_password = "app-password"

        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = send_email(
            subject="[Market Mover] Test",
            html_body="<p>Top 3 articles</p>",
            plain_text="Top 3 articles",
            recipients=["joe@example.com", "jared@example.com"],
            settings=mock_settings,
        )

        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user@gmail.com", "app-password")
        mock_server.sendmail.assert_called_once()

    @patch("market_mover.email_sender.smtplib.SMTP")
    def test_returns_false_on_smtp_error(self, mock_smtp_class, mock_settings):
        mock_settings.smtp_username = "user@gmail.com"
        mock_settings.smtp_app_password = "app-password"

        mock_smtp_class.return_value.__enter__ = MagicMock(
            side_effect=Exception("Connection refused")
        )
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = send_email(
            subject="Test",
            html_body="<p>test</p>",
            plain_text="test",
            recipients=["test@example.com"],
            settings=mock_settings,
        )

        assert result is False

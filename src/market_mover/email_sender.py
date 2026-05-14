"""SMTP email sender for Market Mover briefings."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import MarketMoverSettings

logger = logging.getLogger("market_mover.email_sender")


def send_email(
    subject: str,
    html_body: str,
    plain_text: str,
    recipients: list[str],
    settings: MarketMoverSettings | None = None,
) -> bool:
    """Send an email via Gmail SMTP using app password.

    Args:
        subject: Email subject line.
        html_body: HTML email body.
        plain_text: Plain text fallback.
        recipients: List of email addresses.
        settings: Optional settings (loads from env if not provided).

    Returns:
        True if sent successfully, False otherwise.
    """
    if settings is None:
        settings = MarketMoverSettings()

    if not settings.smtp_username or not settings.smtp_app_password:
        logger.error("SMTP credentials not configured (SMTP_USERNAME / SMTP_APP_PASSWORD)")
        return False

    if not recipients:
        logger.error("No recipients configured")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_username
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_username, settings.smtp_app_password)
            server.sendmail(settings.smtp_username, recipients, msg.as_string())

        logger.info(f"Email sent to {', '.join(recipients)}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False

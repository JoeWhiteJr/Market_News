"""Shared test fixtures for Market Mover tests."""

import pytest

from market_mover.config import MarketMoverSettings
from market_mover.models import RankedArticle, RawArticle, SourceType


@pytest.fixture
def mock_settings(monkeypatch):
    """Settings with test API keys."""
    monkeypatch.setenv("CLAUDE_API_KEY_1", "test-claude-key")
    monkeypatch.setenv("GEMINI_API_KEY_1", "test-gemini-key")
    monkeypatch.setenv("NEWSAPI_API_KEY", "test-newsapi-key")
    monkeypatch.setenv("FINNHUB_API_KEY", "test-finnhub-key")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-youtube-key")
    monkeypatch.setenv("EMAIL_RECIPIENTS", "joe@example.com,jared@example.com")
    return MarketMoverSettings()


@pytest.fixture
def sample_raw_articles() -> list[RawArticle]:
    """Sample raw articles for testing."""
    return [
        RawArticle(
            title="Fed Raises Interest Rates by 25 Basis Points",
            url="https://example.com/fed-rates",
            source_name="Reuters",
            source_type=SourceType.RSS,
            summary="The Federal Reserve raised rates citing persistent inflation.",
        ),
        RawArticle(
            title="NVIDIA Reports Record Quarterly Revenue",
            url="https://example.com/nvidia-earnings",
            source_name="CNBC",
            source_type=SourceType.NEWSAPI,
            summary="NVIDIA beat analyst expectations with $30B quarterly revenue.",
        ),
        RawArticle(
            title="Oil Prices Surge After OPEC Cuts Production",
            url="https://example.com/opec-cuts",
            source_name="Bloomberg",
            source_type=SourceType.RSS,
            summary="OPEC announced surprise production cuts sending oil above $90.",
        ),
        RawArticle(
            title="China's Economy Shows Signs of Recovery",
            url="https://example.com/china-recovery",
            source_name="Finnhub",
            source_type=SourceType.FINNHUB,
            summary="Manufacturing PMI rises above 50 for the first time in months.",
        ),
        RawArticle(
            title="Market Analysis: What the Fed Decision Means",
            url="https://youtube.com/watch?v=abc123",
            source_name="CNBC",
            source_type=SourceType.YOUTUBE,
            summary="Analysis of today's Fed rate decision and market outlook.",
            is_video=True,
        ),
    ]


@pytest.fixture
def sample_ranked_articles() -> list[RankedArticle]:
    """Sample ranked articles for testing."""
    return [
        RankedArticle(
            rank=1,
            title="Fed Raises Interest Rates by 25 Basis Points",
            url="https://example.com/fed-rates",
            source_name="Reuters",
            market_impact_summary="Rate hikes affect all sectors. Higher borrowing costs pressure growth stocks and housing. Bond yields rise, strengthening the dollar.",
            impact_score=9.5,
        ),
        RankedArticle(
            rank=2,
            title="NVIDIA Reports Record Quarterly Revenue",
            url="https://example.com/nvidia-earnings",
            source_name="CNBC",
            market_impact_summary="NVIDIA's results signal continued AI spending momentum. Semiconductor and tech sectors benefit from the AI infrastructure buildout.",
            impact_score=8.7,
        ),
        RankedArticle(
            rank=3,
            title="Oil Prices Surge After OPEC Cuts Production",
            url="https://example.com/opec-cuts",
            source_name="Bloomberg",
            market_impact_summary="Higher oil prices increase inflation pressure and input costs across all sectors. Energy stocks benefit while consumer discretionary suffers.",
            impact_score=8.2,
        ),
    ]

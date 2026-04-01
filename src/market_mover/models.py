"""Pydantic models for Market Mover data structures."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class SourceType(str, Enum):
    NEWSAPI = "newsapi"
    FINNHUB = "finnhub"
    RSS = "rss"
    YOUTUBE = "youtube"


class RawArticle(BaseModel):
    """A raw article/video fetched from a news source."""

    title: str
    url: str
    source_name: str
    source_type: SourceType
    published_at: datetime | None = None
    summary: str = ""
    is_video: bool = False


class RankedArticle(BaseModel):
    """An article ranked by market impact after LLM analysis."""

    rank: int
    title: str
    url: str
    source_name: str
    market_impact_summary: str
    impact_score: float
    is_video: bool = False

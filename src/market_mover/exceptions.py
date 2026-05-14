"""Custom exceptions for Market Mover MCP server."""


class MarketMoverError(Exception):
    """Base exception for Market Mover module."""
    pass


class NewsSourceError(MarketMoverError):
    """Raised when a news source fetch fails."""
    pass


class LLMError(MarketMoverError):
    """Raised when both primary and fallback LLM calls fail."""
    pass


class EmptyLLMResponse(LLMError):
    """Raised when an LLM returns a response with no usable text content.

    Treated the same as a transient network failure by ``analyze_articles``
    so the caller falls through to the next provider (e.g. Gemini) instead
    of aborting the briefing.
    """
    pass


class AnalysisParsingError(MarketMoverError):
    """Raised when LLM response cannot be parsed into ranked articles."""
    pass


class EmailFormatError(MarketMoverError):
    """Raised when email template rendering fails."""
    pass

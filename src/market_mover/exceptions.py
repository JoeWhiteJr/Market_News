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


class AnalysisParsingError(MarketMoverError):
    """Raised when LLM response cannot be parsed into ranked articles."""
    pass


class EmailFormatError(MarketMoverError):
    """Raised when email template rendering fails."""
    pass

# CLAUDE.md — Market Mover MCP Server

## Project
MCP server that gathers market-moving news from 4 sources (NewsAPI, Finnhub, RSS, YouTube), analyzes with Claude/Gemini, and formats a top-3 email briefing.

## Stack
- Python 3.10+, FastMCP (stdio transport)
- Claude primary + Gemini fallback LLM client
- pydantic-settings for config, pydantic models for data

## Running
```bash
# Install
pip install -e ".[dev]"

# Run MCP server directly (for testing)
python -m market_mover.server

# Tests
pytest

# Lint
ruff check . --select=E,F,W --ignore=E501
```

## Key Rules
- Never commit API keys — all from .env
- LLM client: Claude primary, Gemini fallback (same pattern as Wasden Watch)
- Source fetchers fail gracefully — return empty list, never crash the pipeline
- Email sending is NOT in this server — Claude Code calls Gmail MCP separately

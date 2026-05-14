"""Dual-LLM client with Claude primary, Gemini fallback, and round-robin key rotation.

Adapted from Wasden Watch's llm_client.py for article ranking instead of verdict generation.
"""

import itertools
import json
import logging
import re
from urllib.parse import urlparse

from .config import MarketMoverSettings
from .exceptions import AnalysisParsingError, LLMError
from .models import RankedArticle, RawArticle

logger = logging.getLogger("market_mover.llm_client")

# Sentinel returned by _extract_text_from_anthropic_message when the response
# has no usable text content (empty completion or only thinking/tool blocks).
# Falls through to AnalysisParsingError so the caller can fall back to Gemini.
NO_TEXT_SENTINEL = "LLM produced no text"


def _extract_text_from_anthropic_message(message: object) -> str:
    """Return the first text block's ``.text`` from an Anthropic message.

    Defends against:
    - Empty ``content`` list (``IndexError`` from ``content[0]``)
    - Non-text first block such as ``ThinkingBlock`` / ``ToolUseBlock``
      (``AttributeError`` from ``.text``)
    """
    content = getattr(message, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if block_type == "text" and isinstance(text, str):
            return text
    # Fallback: some SDK versions don't set .type but do expose .text on text blocks.
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            return text
    logger.warning("Anthropic response had no text blocks; returning sentinel")
    return NO_TEXT_SENTINEL


RANKING_SYSTEM_PROMPT = """You are a financial markets analyst. Your job is to evaluate news articles \
and rank them by their potential impact on the stock market.

For each article, consider:
- How many sectors or stocks does this affect?
- How likely is this to cause significant price movement?
- How time-sensitive is this information?
- Is this a leading indicator or breaking news vs routine reporting?

You will receive a JSON array of articles. Analyze them and return the top 3 most \
market-moving articles, ranked by impact.

Return ONLY a JSON object in this exact format:
{
  "top_3": [
    {
      "rank": 1,
      "title": "exact article title",
      "url": "exact article url",
      "market_impact_summary": "2-3 sentences explaining WHY this moves markets and what sectors/stocks are affected",
      "impact_score": 9.2,
      "is_video": false
    },
    ...
  ]
}

Impact scores should be on a 0-10 scale. Be selective — only truly market-moving news should score above 7.
Do NOT invent or include a "source_name" field — the source is derived from the URL downstream."""


class LLMClient:
    """LLM client with Claude primary and Gemini fallback, using key rotation."""

    def __init__(self, settings: MarketMoverSettings):
        self._settings = settings
        self._claude_key_cycle = (
            itertools.cycle(settings.claude_api_keys) if settings.claude_api_keys else None
        )
        self._gemini_key_cycle = (
            itertools.cycle(settings.gemini_api_keys) if settings.gemini_api_keys else None
        )

    def analyze_articles(self, articles: list[RawArticle]) -> tuple[list[RankedArticle], str]:
        """Analyze and rank articles by market impact using Claude with Gemini fallback.

        Args:
            articles: List of raw articles to analyze.

        Returns:
            Tuple of (list of top 3 RankedArticle, model name used).

        Raises:
            LLMError: If both Claude and Gemini fail.
            AnalysisParsingError: If response cannot be parsed.
        """
        articles_json = json.dumps(
            [a.model_dump(mode="json") for a in articles],
            indent=2,
        )
        user_prompt = f"Analyze these {len(articles)} articles and return the top 3 by market impact:\n\n{articles_json}"

        # Try Claude first
        if self._claude_key_cycle is not None:
            try:
                raw_response = self._call_claude(RANKING_SYSTEM_PROMPT, user_prompt)
                ranked = self._parse_response(raw_response)
                logger.info(f"Analysis completed via Claude ({self._settings.claude_model})")
                return ranked, self._settings.claude_model
            except AnalysisParsingError:
                raise
            except Exception as e:
                logger.warning(f"Claude call failed: {e}, falling back to Gemini")

        # Fallback to Gemini
        if self._gemini_key_cycle is not None:
            try:
                raw_response = self._call_gemini(RANKING_SYSTEM_PROMPT, user_prompt)
                ranked = self._parse_response(raw_response)
                logger.info(f"Analysis completed via Gemini fallback ({self._settings.gemini_model})")
                return ranked, self._settings.gemini_model
            except AnalysisParsingError:
                raise
            except Exception as e:
                logger.warning(f"Gemini call also failed: {e}")
                raise LLMError(f"Both Claude and Gemini failed. Last error: {e}")

        raise LLMError("No API keys configured for either Claude or Gemini")

    def _call_claude(self, system_prompt: str, user_prompt: str) -> str:
        """Call Claude API with round-robin key rotation."""
        import anthropic

        key = next(self._claude_key_cycle)
        client = anthropic.Anthropic(api_key=key, timeout=45)

        message = client.messages.create(
            model=self._settings.claude_model,
            max_tokens=self._settings.max_tokens,
            temperature=self._settings.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=45,
        )

        return _extract_text_from_anthropic_message(message)

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        """Call Gemini API with round-robin key rotation."""
        import google.generativeai as genai

        key = next(self._gemini_key_cycle)
        genai.configure(api_key=key)

        model = genai.GenerativeModel(
            model_name=self._settings.gemini_model,
            system_instruction=system_prompt,
        )

        response = model.generate_content(
            user_prompt,
            generation_config=genai.GenerationConfig(
                temperature=self._settings.temperature,
                max_output_tokens=self._settings.max_tokens,
            ),
            request_options={"timeout": 45},
        )

        return response.text

    def _parse_response(self, raw: str) -> list[RankedArticle]:
        """Parse LLM response into ranked articles.

        Tries 3 strategies: direct JSON, markdown code blocks, brace extraction.

        Raises:
            AnalysisParsingError: If response cannot be parsed.
        """
        text = raw.strip()
        parsed = None

        # Strategy 1: Direct JSON parsing
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract from markdown code blocks
        if parsed is None:
            json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(1).strip())
                except json.JSONDecodeError:
                    pass

        # Strategy 3: Find JSON object in text
        if parsed is None:
            brace_match = re.search(r"\{.*\}", text, re.DOTALL)
            if brace_match:
                try:
                    parsed = json.loads(brace_match.group(0))
                except json.JSONDecodeError:
                    pass

        if parsed is None:
            raise AnalysisParsingError(
                f"Could not parse LLM response as JSON. Raw response: {text[:500]}"
            )

        # Extract top_3 array
        top_3_data = parsed.get("top_3", parsed) if isinstance(parsed, dict) else parsed
        if not isinstance(top_3_data, list):
            raise AnalysisParsingError(
                f"Expected a list of ranked articles, got: {type(top_3_data)}"
            )

        ranked = []
        for item in top_3_data[:3]:
            url = item.get("url", "")
            # Source is derived from the URL downstream in the template; we keep
            # a copy on the model for plain-text rendering / debugging. If the
            # LLM still returned a source_name we ignore it to avoid the
            # "Motley Fool / Cleveland Fed" co-attribution failure mode.
            ranked.append(
                RankedArticle(
                    rank=item.get("rank", len(ranked) + 1),
                    title=item.get("title", ""),
                    url=url,
                    source_name=_source_name_from_url(url),
                    market_impact_summary=item.get("market_impact_summary", ""),
                    impact_score=float(item.get("impact_score", 0.0)),
                    is_video=item.get("is_video", False),
                )
            )

        return ranked


def _source_name_from_url(url: str) -> str:
    """Derive a short source label from a URL's netloc (strip leading ``www.``)."""
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc

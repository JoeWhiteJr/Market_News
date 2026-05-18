"""Dual-LLM client with Claude primary, Gemini fallback, and round-robin key rotation.

Adapted from Wasden Watch's llm_client.py for article ranking instead of verdict generation.
"""

import itertools
import json
import logging
import re
from urllib.parse import urlparse

from .config import MarketMoverSettings
from .exceptions import AnalysisParsingError, EmptyLLMResponse, LLMError
from .models import ContrarianCoda, RankedArticle, RawArticle
from .voices import (
    NEUTRAL_VOICE,
    VoiceSpec,
    contains_profanity,
    get_voice,
    strip_profanity,
)

logger = logging.getLogger("market_mover.llm_client")


def _extract_text_from_anthropic_message(message: object) -> str:
    """Return the first text block's ``.text`` from an Anthropic message.

    Defends against:
    - Empty ``content`` list (``IndexError`` from ``content[0]``)
    - Non-text first block such as ``ThinkingBlock`` / ``ToolUseBlock``
      (``AttributeError`` from ``.text``)

    Raises:
        EmptyLLMResponse: When the message has no usable text content.
            Caught by ``analyze_articles`` so the caller falls through to Gemini.
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
    raise EmptyLLMResponse(
        "Anthropic response had no text blocks (empty content or only "
        "thinking/tool blocks)"
    )


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
      "is_video": false,
      "primary_ticker": "SPY",
      "category": "macro"
    },
    ...
  ]
}

Impact scores should be on a 0-10 scale. Be selective — only truly market-moving news should score above 7.
Do NOT invent or include a "source_name" field — the source is derived from the URL downstream.

For "primary_ticker": pick the single most relevant exchange-traded ticker (e.g.,
"TSLA" for a Tesla-specific story, "SPY" for broad-market macro, "USO" for oil,
"BTC-USD" for crypto). Use null when no ticker is a clean proxy.
For "category": pick exactly one of "macro" (Fed, CPI, jobs), "single_name"
(one company), "commodity" (oil/gold/ag), "crypto", "geopolitical" (war,
sanctions, election), or "other". These fields feed a future scorecard that
grades yesterday's picks against actual price action — be honest, not generous."""


CONTRARIAN_SYSTEM_PROMPT = """You are a contrarian markets analyst. Your job is to steel-man \
the strongest counter-argument to the day's #1 story — the "bear case" or \
"what everyone's missing" angle.

You will receive:
1. The #1 story (title + summary).
2. A list of OTHER real article URLs from today's news pool.

You MUST pick ONE source URL from the provided list to cite. Do NOT invent or \
hallucinate URLs — if no article in the pool supports a contrarian angle, pick \
the closest tangentially-related one. The URL you return MUST appear verbatim \
in the provided list.

Return ONLY a JSON object in this exact format:
{
  "headline": "short headline — e.g. 'But: 10-year yields tell a different story'",
  "argument": "2-3 sentences explaining the counter-argument and why it matters",
  "source_url": "exact URL from the provided list"
}

Keep the argument honest and non-conspiratorial. Never recommend trades."""


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

    def analyze_articles(
        self,
        articles: list[RawArticle],
        voice: VoiceSpec | None = None,
    ) -> tuple[list[RankedArticle], str, VoiceSpec]:
        """Analyze and rank articles by market impact using Claude with Gemini fallback.

        Args:
            articles: List of raw articles to analyze.
            voice: Optional voice persona spec. If ``None``, the persona from
                ``settings.briefing_voice`` is loaded. The persona's
                ``system_prompt_suffix`` is appended to the ranking prompt;
                the JSON output contract is unchanged.

        Returns:
            Tuple of (list of top 3 RankedArticle, model name used, effective voice).
            The effective voice may differ from the input if the profanity
            guardrail tripped and the override-to-neutral toggle is on.

        Raises:
            LLMError: If both Claude and Gemini fail.
            AnalysisParsingError: If response cannot be parsed.
        """
        active_voice: VoiceSpec = voice if voice is not None else get_voice(self._settings.briefing_voice)
        system_prompt = _build_system_prompt(RANKING_SYSTEM_PROMPT, active_voice)

        articles_json = json.dumps(
            [a.model_dump(mode="json") for a in articles],
            indent=2,
        )
        user_prompt = f"Analyze these {len(articles)} articles and return the top 3 by market impact:\n\n{articles_json}"

        # Try Claude first
        if self._claude_key_cycle is not None:
            try:
                raw_response = self._call_claude(system_prompt, user_prompt)
                ranked = self._parse_response(raw_response)
                logger.info(f"Analysis completed via Claude ({self._settings.claude_model})")
                ranked, effective_voice = self._enforce_profanity_guardrail(ranked, active_voice)
                return ranked, self._settings.claude_model, effective_voice
            except AnalysisParsingError:
                raise
            except Exception as e:
                logger.warning(f"Claude call failed: {e}, falling back to Gemini")

        # Fallback to Gemini
        if self._gemini_key_cycle is not None:
            try:
                raw_response = self._call_gemini(system_prompt, user_prompt)
                ranked = self._parse_response(raw_response)
                logger.info(f"Analysis completed via Gemini fallback ({self._settings.gemini_model})")
                ranked, effective_voice = self._enforce_profanity_guardrail(ranked, active_voice)
                return ranked, self._settings.gemini_model, effective_voice
            except AnalysisParsingError:
                raise
            except Exception as e:
                logger.warning(f"Gemini call also failed: {e}")
                raise LLMError(f"Both Claude and Gemini failed. Last error: {e}")

        raise LLMError("No API keys configured for either Claude or Gemini")

    def _enforce_profanity_guardrail(
        self,
        ranked: list[RankedArticle],
        active_voice: VoiceSpec,
    ) -> tuple[list[RankedArticle], VoiceSpec]:
        """Strip any obvious profanity from summaries and, if detected, fall back to neutral.

        Returns the (possibly-scrubbed) ranked list and the effective voice.
        """
        detected = any(contains_profanity(a.market_impact_summary) for a in ranked)
        # Always strip, even if we don't fall back (defense in depth).
        scrubbed: list[RankedArticle] = []
        for a in ranked:
            scrubbed.append(
                a.model_copy(update={"market_impact_summary": strip_profanity(a.market_impact_summary)})
            )

        if detected and self._settings.briefing_voice_override_to_neutral_on_detect:
            logger.warning(
                "Profanity detected in LLM output — falling back to neutral voice for today's send "
                "(was: %r)",
                active_voice.get("name"),
            )
            return scrubbed, get_voice(NEUTRAL_VOICE)
        if detected:
            logger.warning(
                "Profanity detected in LLM output — stripped, but voice override disabled; "
                "keeping voice %r",
                active_voice.get("name"),
            )
        return scrubbed, active_voice

    def generate_contrarian_coda(
        self,
        top_story: RankedArticle,
        all_articles: list[RawArticle],
    ) -> ContrarianCoda | None:
        """Generate a steel-manned counter-argument to ``top_story``.

        Uses the same Claude→Gemini fallback chain as ``analyze_articles``.
        The ``source_url`` returned by the LLM is validated against the pool
        of real article URLs — hallucinated URLs return ``None`` (skip the
        coda for today's send).

        Any failure (LLM error, parse error, validation failure) returns
        ``None`` rather than raising — the daily send must not break because
        the optional coda failed.

        Args:
            top_story: The #1 ranked story to argue against.
            all_articles: The full pool of raw articles (URLs sourced from here).

        Returns:
            A ``ContrarianCoda`` instance, or ``None`` on any failure.
        """
        # Build the pool of allowed source URLs. We dedupe + cap to keep prompt small.
        url_pool: list[str] = []
        seen: set[str] = set()
        for a in all_articles:
            if a.url and a.url not in seen and a.url != top_story.url:
                url_pool.append(a.url)
                seen.add(a.url)
        if not url_pool:
            logger.info("Contrarian coda skipped: no eligible source URLs in pool")
            return None

        # Cap at 50 URLs to keep the prompt reasonable.
        capped_pool = url_pool[:50]

        user_prompt = (
            f"#1 STORY:\n"
            f"Title: {top_story.title}\n"
            f"Summary: {top_story.market_impact_summary}\n\n"
            f"OTHER ARTICLE URLS (pick exactly one for source_url):\n"
            + "\n".join(f"- {u}" for u in capped_pool)
            + "\n\nReturn the JSON object as specified."
        )

        raw_response: str | None = None

        if self._claude_key_cycle is not None:
            try:
                raw_response = self._call_claude(CONTRARIAN_SYSTEM_PROMPT, user_prompt)
            except Exception as e:
                logger.warning(f"Contrarian coda Claude call failed: {e}, trying Gemini")

        if raw_response is None and self._gemini_key_cycle is not None:
            try:
                raw_response = self._call_gemini(CONTRARIAN_SYSTEM_PROMPT, user_prompt)
            except Exception as e:
                logger.warning(f"Contrarian coda Gemini call also failed: {e}")
                return None

        if raw_response is None:
            return None

        try:
            parsed = _parse_json_loose(raw_response)
        except ValueError as e:
            logger.warning(f"Contrarian coda parse failed: {e}")
            return None

        if not isinstance(parsed, dict):
            logger.warning("Contrarian coda parse returned non-dict; skipping")
            return None

        headline = str(parsed.get("headline", "")).strip()
        argument = str(parsed.get("argument", "")).strip()
        source_url = str(parsed.get("source_url", "")).strip()

        if not (headline and argument and source_url):
            logger.warning("Contrarian coda missing required fields; skipping")
            return None

        # Validate the URL against the real pool (this is the anti-hallucination check).
        allowed = set(capped_pool)
        if source_url not in allowed:
            logger.warning(
                "Contrarian coda source_url %r not in article pool; skipping render",
                source_url,
            )
            return None

        # Profanity strip (best-effort — doesn't trigger neutral fallback for the coda).
        headline = strip_profanity(headline)
        argument = strip_profanity(argument)

        return ContrarianCoda(
            headline=headline,
            argument=argument,
            source_url=source_url,
            source_name=_source_name_from_url(source_url),
        )

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
                    primary_ticker=_normalize_primary_ticker(item.get("primary_ticker")),
                    category=_normalize_category(item.get("category")),
                )
            )

        return ranked


def _build_system_prompt(base_prompt: str, voice: VoiceSpec) -> str:
    """Append a voice persona suffix to the base ranking prompt.

    The output JSON contract from ``base_prompt`` always wins — the voice
    only flavors the prose inside ``market_impact_summary``.
    """
    suffix = voice.get("system_prompt_suffix", "")
    if not suffix:
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        "VOICE / TONE:\n"
        f"{suffix}\n\n"
        "Important: the JSON structure above is required. The voice ONLY influences "
        "the prose inside each story's market_impact_summary — not the field names, "
        "not the schema, not the count of stories."
    )


def _parse_json_loose(raw: str) -> object:
    """Parse a JSON object from an LLM response, tolerating markdown wrappers.

    Mirrors the 3-strategy approach in ``LLMClient._parse_response`` but
    returns the raw parsed object so callers can pull whatever fields they need.
    """
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    code_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from response: {text[:300]}")


_ALLOWED_CATEGORIES = frozenset(
    {"macro", "single_name", "commodity", "crypto", "geopolitical", "other"}
)


def _normalize_primary_ticker(raw: object) -> str | None:
    """Coerce the LLM's primary_ticker field to ``str | None``.

    The LLM sometimes returns ``"null"`` or an empty string for "no clean ticker."
    We treat any of those as ``None`` so the persisted JSONL stays clean.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned or cleaned.lower() in {"null", "none", "n/a", "na"}:
        return None
    # Tickers are short — cap defensively so a hallucinated paragraph can't
    # smuggle prose into a ticker field.
    return cleaned[:20].upper()


def _normalize_category(raw: object) -> str:
    """Coerce the LLM's category field to one of the allowed values.

    Falls back to ``"other"`` for anything unrecognized.
    """
    if not isinstance(raw, str):
        return "other"
    key = raw.strip().lower()
    if key in _ALLOWED_CATEGORIES:
        return key
    return "other"


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

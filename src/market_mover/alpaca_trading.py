"""Alpaca **paper** trading client (Cycle 6 / ADR 0003).

A thin, defensive wrapper over the Alpaca paper Trading API. Every method
returns ``None``/``[]`` on failure and never raises — a broker hiccup must
never crash the daily pipeline (the email has priority).

SAFETY: the base URL is taken from ``settings.alpaca_paper_base_url`` which is
hard-coded to ``https://paper-api.alpaca.markets``. There is no live-trading
host anywhere in this codebase.
"""

import logging

from .config import MarketMoverSettings

logger = logging.getLogger("market_mover.alpaca_trading")

_HTTP_TIMEOUT_SECS = 15


class AlpacaTradingClient:
    """Paper-only Alpaca Trading API client."""

    def __init__(self, settings: MarketMoverSettings) -> None:
        self._base = settings.alpaca_paper_base_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": settings.alpaca_api_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_api_secret_key,
        }

    # -- internal ----------------------------------------------------------

    def _request(self, method: str, path: str, **kw):
        """Make one HTTP call. Returns parsed JSON, or ``None`` on any failure."""
        try:
            import requests
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(f"Alpaca trading unavailable (requests import failed): {e}")
            return None
        try:
            resp = requests.request(
                method,
                f"{self._base}{path}",
                headers=self._headers,
                timeout=_HTTP_TIMEOUT_SECS,
                **kw,
            )
            resp.raise_for_status()
            # DELETE /positions/{sym} returns the closing order; 207s etc. still parse.
            return resp.json()
        except Exception as e:
            logger.warning(f"Alpaca {method} {path} failed: {e}")
            return None

    # -- read --------------------------------------------------------------

    def get_account(self) -> dict | None:
        """Return the paper account dict (``equity``, ``cash``, …) or ``None``."""
        out = self._request("GET", "/v2/account")
        return out if isinstance(out, dict) else None

    def list_positions(self) -> list[dict]:
        """Return open paper positions (possibly empty)."""
        out = self._request("GET", "/v2/positions")
        return out if isinstance(out, list) else []

    # -- write (paper) -----------------------------------------------------

    def submit_notional_order(
        self, symbol: str, notional: float, side: str = "buy"
    ) -> dict | None:
        """Submit a market order for ``notional`` dollars of ``symbol``.

        Uses Alpaca's ``notional`` field (fractional shares) with
        ``time_in_force=day`` — submitted pre-market, it queues for the open.
        Returns the order dict, or ``None`` on failure.
        """
        body = {
            "symbol": symbol.strip().upper(),
            "notional": round(float(notional), 2),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        return self._request("POST", "/v2/orders", json=body)

    def close_position(self, symbol: str) -> dict | None:
        """Liquidate the entire paper position in ``symbol`` (market order).

        Returns the closing order dict, or ``None`` if there was nothing to
        close / the call failed.
        """
        return self._request("DELETE", f"/v2/positions/{symbol.strip().upper()}")

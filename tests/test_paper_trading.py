"""Tests for the paper-trading engine (Cycle 6 / ADR 0003)."""

from datetime import date

from market_mover.models import RankedArticle
from market_mover.paper_trading import (
    PaperCycleRecord,
    compute_paper_stats,
    eligible_picks,
    load_cycles,
    run_paper_cycle,
)


def _pick(rank, ticker, category="single_name"):
    return RankedArticle(
        rank=rank,
        title=f"Story {rank}",
        url=f"https://example.com/{rank}",
        source_name="example.com",
        market_impact_summary="summary",
        impact_score=8.0,
        primary_ticker=ticker,
        category=category,
    )


class _FakeClient:
    """Records orders/closes; serves canned positions + account."""

    def __init__(self, positions=None, equity=100000.0):
        self._positions = positions or []
        self._equity = equity
        self.opened: list[tuple] = []
        self.closed: list[str] = []

    def list_positions(self):
        return self._positions

    def submit_notional_order(self, symbol, notional, side="buy"):
        self.opened.append((symbol, notional, side))
        return {"id": f"order-{symbol}"}

    def close_position(self, symbol):
        self.closed.append(symbol)
        return {"id": f"close-{symbol}"}

    def get_account(self):
        return {"equity": self._equity}


class _Settings:
    paper_trading_enabled = True
    alpaca_api_key_id = "k"
    alpaca_api_secret_key = "s"
    paper_notional_per_position = 1000.0

    def __init__(self, path):
        self._path = path

    @property
    def has_alpaca_creds(self):
        return True

    @property
    def paper_trades_jsonl_full_path(self):
        return self._path


class TestEligiblePicks:
    def test_keeps_clean_tickers(self):
        picks = [_pick(1, "NVDA"), _pick(2, "AVGO")]
        assert [p.primary_ticker for p in eligible_picks(picks)] == ["NVDA", "AVGO"]

    def test_drops_tickerless_macro(self):
        picks = [_pick(1, None, "macro"), _pick(2, "TSLA")]
        assert [p.primary_ticker for p in eligible_picks(picks)] == ["TSLA"]

    def test_drops_crypto(self):
        picks = [_pick(1, "BTCUSD", "crypto"), _pick(2, "SPY", "single_name")]
        assert [p.primary_ticker for p in eligible_picks(picks)] == ["SPY"]

    def test_caps_at_three(self):
        picks = [_pick(i, f"T{i}") for i in range(1, 6)]
        assert len(eligible_picks(picks)) == 3


class TestRunPaperCycle:
    def test_opens_eligible_picks(self, tmp_path):
        ledger = tmp_path / "paper.jsonl"
        client = _FakeClient()
        picks = [_pick(1, "NVDA"), _pick(2, None, "macro"), _pick(3, "AVGO")]
        rec = run_paper_cycle(picks, _Settings(ledger), date(2026, 6, 9), client=client)
        assert rec is not None
        # macro/no-ticker skipped; two orders placed.
        assert sorted(s for s, _, _ in client.opened) == ["AVGO", "NVDA"]
        assert {o.ticker for o in rec.opened} == {"NVDA", "AVGO"}
        assert rec.equity == 100000.0

    def test_closes_prior_positions_and_records_pnl(self, tmp_path):
        ledger = tmp_path / "paper.jsonl"
        positions = [
            {
                "symbol": "NVDA", "qty": "2", "avg_entry_price": "100",
                "current_price": "110", "unrealized_pl": "20",
                "unrealized_plpc": "0.10",
            }
        ]
        client = _FakeClient(positions=positions)
        rec = run_paper_cycle([_pick(1, "TSLA")], _Settings(ledger), date(2026, 6, 9), client=client)
        assert client.closed == ["NVDA"]
        assert len(rec.closed) == 1
        t = rec.closed[0]
        assert t.ticker == "NVDA"
        assert t.pnl_abs == 20.0
        assert abs(t.pnl_pct - 10.0) < 1e-6

    def test_idempotent_same_day(self, tmp_path):
        ledger = tmp_path / "paper.jsonl"
        s = _Settings(ledger)
        c1 = _FakeClient()
        run_paper_cycle([_pick(1, "NVDA")], s, date(2026, 6, 9), client=c1)
        # Second run same day: no new orders.
        c2 = _FakeClient()
        rec = run_paper_cycle([_pick(1, "NVDA")], s, date(2026, 6, 9), client=c2)
        assert c2.opened == []
        assert c2.closed == []
        assert rec.cycle_date == "2026-06-09"
        assert len(load_cycles(ledger)) == 1  # only one record written

    def test_disabled_returns_none(self, tmp_path):
        s = _Settings(tmp_path / "p.jsonl")
        s.paper_trading_enabled = False
        assert run_paper_cycle([_pick(1, "NVDA")], s, date(2026, 6, 9), client=_FakeClient()) is None

    def test_ledger_roundtrip(self, tmp_path):
        ledger = tmp_path / "paper.jsonl"
        run_paper_cycle([_pick(1, "NVDA")], _Settings(ledger), date(2026, 6, 9), client=_FakeClient())
        cycles = load_cycles(ledger)
        assert len(cycles) == 1
        assert cycles[0].opened[0].ticker == "NVDA"


class TestPaperBlockRender:
    def test_block_wrapped_in_table_row(self):
        # Regression: the Paper Portfolio block sits inside the card <table>,
        # so it must be a <tr><td> row (not a bare <table>) for Outlook etc.
        from market_mover.email_template import _render_paper_block_html
        out = _render_paper_block_html(
            {"equity": 100000.0, "n_trades": 3, "wins": 1, "win_rate": 33.3, "total_pnl": -27.0}
        ).strip()
        assert out.startswith("<tr>")
        assert out.endswith("</tr>")

    def test_empty_block_when_no_equity(self):
        from market_mover.email_template import _render_paper_block_html
        assert _render_paper_block_html(None) == ""
        assert _render_paper_block_html({"equity": None}) == ""


class TestCategoryPersistence:
    def test_opened_positions_carry_category(self, tmp_path):
        ledger = tmp_path / "paper.jsonl"
        client = _FakeClient()
        picks = [_pick(1, "NVDA", "single_name"), _pick(2, "USO", "commodity")]
        rec = run_paper_cycle(picks, _Settings(ledger), date(2026, 6, 18), client=client)
        by_ticker = {o.ticker: o.category for o in rec.opened}
        assert by_ticker == {"NVDA": "single_name", "USO": "commodity"}

    def test_closed_trades_inherit_prior_category(self, tmp_path):
        ledger = tmp_path / "paper.jsonl"
        s = _Settings(ledger)
        # Cycle 1: open NVDA (single_name).
        run_paper_cycle([_pick(1, "NVDA", "single_name")], s, date(2026, 6, 18),
                        client=_FakeClient())
        # Cycle 2: NVDA is now an open position to close; category comes from
        # the prior cycle's opened record.
        positions = [{
            "symbol": "NVDA", "qty": "2", "avg_entry_price": "100",
            "current_price": "110", "unrealized_pl": "20", "unrealized_plpc": "0.10",
        }]
        rec2 = run_paper_cycle([_pick(1, "TSLA", "single_name")], s, date(2026, 6, 19),
                               client=_FakeClient(positions=positions))
        assert len(rec2.closed) == 1
        assert rec2.closed[0].ticker == "NVDA"
        assert rec2.closed[0].category == "single_name"


class TestComputePaperStats:
    def test_aggregates_wins_and_pnl(self):
        cycles = [
            PaperCycleRecord(cycle_date="2026-06-08", equity=100100.0, closed=[
                {"ticker": "A", "qty": 1, "entry_price": 10, "exit_price": 11, "pnl_abs": 1.0, "pnl_pct": 10.0},
                {"ticker": "B", "qty": 1, "entry_price": 10, "exit_price": 9, "pnl_abs": -1.0, "pnl_pct": -10.0},
            ]),
            PaperCycleRecord(cycle_date="2026-06-09", equity=100250.0, closed=[
                {"ticker": "C", "qty": 1, "entry_price": 10, "exit_price": 12, "pnl_abs": 2.0, "pnl_pct": 20.0},
            ]),
        ]
        stats = compute_paper_stats(cycles)
        assert stats["n_trades"] == 3
        assert stats["wins"] == 2
        assert abs(stats["win_rate"] - (2 / 3 * 100)) < 1e-6
        assert stats["total_pnl"] == 2.0
        assert stats["equity"] == 100250.0  # most recent

    def test_empty_ledger(self):
        stats = compute_paper_stats([])
        assert stats["n_trades"] == 0
        assert stats["win_rate"] is None
        assert stats["equity"] is None

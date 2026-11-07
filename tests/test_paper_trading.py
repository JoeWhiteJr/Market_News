"""Tests for the paper-trading engine (Cycle 6 / ADR 0003)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

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


class TestLedgerCategoryIntegrity:
    """Guard against the 'unmapped' bucket regression (MM-T015).

    Every closed paper trade must carry a category so the dashboard's
    per-category P&L attribution stays truthful. A null category means a
    pick was opened without one (the bug that hid the SPCX/ROKU losers in
    an 'unmapped' bucket and overstated single_name). The legacy pre-2026-06-23
    trades were backfilled from briefings.jsonl via
    scripts/backfill_legacy_categories.py.
    """

    def _ledger_path(self):
        return Path(__file__).resolve().parents[1] / "data" / "paper_trades.jsonl"

    def test_no_closed_trade_missing_category(self):
        path = self._ledger_path()
        if not path.exists():
            return  # ledger only present in the app repo, not in packaged installs
        offenders = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for trade in row.get("closed", []):
                if not trade.get("category"):
                    offenders.append((row.get("cycle_date"), trade.get("ticker")))
        assert not offenders, f"closed trades missing category: {offenders}"


class _WashRejectingClient(_FakeClient):
    """Mimics the broker: a buy is refused while a sell on that ticker is pending."""

    def submit_notional_order(self, symbol, notional, side="buy"):
        if side == "buy" and symbol in self.closed:
            self.opened.append((symbol, notional, "rejected"))
            return None
        return super().submit_notional_order(symbol, notional, side)


def _position(symbol, qty=10.0, entry=100.0, price=100.0):
    return {
        "symbol": symbol,
        "qty": str(qty),
        "avg_entry_price": str(entry),
        "current_price": str(price),
        "market_value": str(qty * price),
        "unrealized_pl": str((price - entry) * qty),
        "unrealized_plpc": str((price - entry) / entry),
    }


class TestRepeatPicks:
    def test_repick_of_held_ticker_is_carried_not_closed_and_rebought(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        client = _WashRejectingClient(positions=[_position("SPY")])
        record = run_paper_cycle(
            [_pick(1, "SPY", "macro")], _Settings(path), date(2026, 11, 6), client=client
        )
        assert client.closed == []
        assert client.opened == []
        assert record.closed == []
        assert [(o.ticker, o.status) for o in record.opened] == [("SPY", "carried")]

    def test_unpicked_ticker_still_closes_while_repick_carries(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        client = _WashRejectingClient(positions=[_position("SPY"), _position("USO")])
        record = run_paper_cycle(
            [_pick(1, "SPY", "macro")], _Settings(path), date(2026, 11, 6), client=client
        )
        assert client.closed == ["USO"]
        assert [t.ticker for t in record.closed] == ["USO"]

    def test_carried_position_tops_up_when_under_target(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        client = _WashRejectingClient(positions=[_position("SPY", qty=8.0)])  # $800
        record = run_paper_cycle(
            [_pick(1, "SPY", "macro")], _Settings(path), date(2026, 11, 6), client=client
        )
        assert client.opened == [("SPY", 200.0, "buy")]
        assert record.opened[0].status == "carried"
        assert record.opened[0].order_id == "order-SPY"

    def test_carried_position_trims_and_logs_realized_pnl(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        # Worth $2,000 (two picks yesterday), one pick today: sell half at a +10% gain.
        client = _WashRejectingClient(
            positions=[_position("SPY", qty=20.0, entry=90.909090, price=100.0)]
        )
        record = run_paper_cycle(
            [_pick(1, "SPY", "macro")], _Settings(path), date(2026, 11, 6), client=client
        )
        assert client.closed == []
        assert client.opened == [("SPY", 1000.0, "sell")]
        assert len(record.closed) == 1
        assert abs(record.closed[0].qty - 10.0) < 1e-6
        assert abs(record.closed[0].pnl_abs - 90.9091) < 1e-3

    def test_small_drift_does_not_trade(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        client = _WashRejectingClient(positions=[_position("SPY", price=105.0)])  # $1,050
        run_paper_cycle(
            [_pick(1, "SPY", "macro")], _Settings(path), date(2026, 11, 6), client=client
        )
        assert client.opened == []

    def test_duplicate_picks_of_new_ticker_each_buy(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        client = _WashRejectingClient()
        record = run_paper_cycle(
            [_pick(1, "SPY", "macro"), _pick(2, "SPY", "macro")],
            _Settings(path), date(2026, 11, 6), client=client,
        )
        assert client.opened == [("SPY", 1000.0, "buy"), ("SPY", 1000.0, "buy")]
        assert [o.status for o in record.opened] == ["submitted", "submitted"]


class TestOrderFailures:
    def test_rejected_buy_is_recorded_as_failed_and_warned(self, tmp_path, caplog):
        class _Rejecting(_FakeClient):
            def submit_notional_order(self, symbol, notional, side="buy"):
                return None

        path = tmp_path / "paper.jsonl"
        record = run_paper_cycle(
            [_pick(1, "NVDA")], _Settings(path), date(2026, 11, 6), client=_Rejecting()
        )
        assert record.opened[0].status == "failed"
        assert record.opened[0].order_id is None
        assert "NVDA" in caplog.text

    def test_failed_close_is_not_logged_as_a_closed_trade(self, tmp_path, caplog):
        class _CloseFails(_FakeClient):
            def close_position(self, symbol):
                return None

        path = tmp_path / "paper.jsonl"
        client = _CloseFails(positions=[_position("USO")])
        record = run_paper_cycle([], _Settings(path), date(2026, 11, 6), client=client)
        assert record.closed == []
        assert "USO" in caplog.text

    def test_legacy_ledger_rows_still_load(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        path.write_text(
            '{"cycle_date":"2026-11-05","equity":1.0,"opened":[{"ticker":"SPY","rank":1,'
            '"notional":15000.0,"order_id":null,"category":"macro"}],"closed":[]}\n'
        )
        assert load_cycles(path)[0].opened[0].status is None


class TestCategoryLookback:
    def test_position_older_than_prior_cycle_keeps_its_category(self, tmp_path):
        """A close delayed past one cycle (e.g. a market holiday) still gets a category."""
        path = tmp_path / "paper.jsonl"
        run_paper_cycle([_pick(1, "EWG", "geopolitical")], _Settings(path), date(2026, 9, 7),
                        client=_FakeClient())
        run_paper_cycle([_pick(1, "NVDA")], _Settings(path), date(2026, 9, 8),
                        client=_FakeClient())
        record = run_paper_cycle([], _Settings(path), date(2026, 9, 9),
                                 client=_FakeClient(positions=[_position("EWG")]))
        assert record.closed[0].category == "geopolitical"


class TestLateEntryFlag:
    def _run(self, tmp_path, now):
        return run_paper_cycle(
            [_pick(1, "NVDA")], _Settings(tmp_path / "paper.jsonl"), date(2026, 11, 9),
            client=_FakeClient(), now=now,
        )

    def test_pre_market_run_is_not_late(self, tmp_path):
        record = self._run(tmp_path, datetime(2026, 11, 9, 13, 2, tzinfo=timezone.utc))  # 8:02 ET
        assert record.late_entry is False
        assert record.run_started_at == "2026-11-09T13:02:00+00:00"

    def test_run_after_the_open_trades_and_is_flagged(self, tmp_path):
        client = _FakeClient()
        record = run_paper_cycle(
            [_pick(1, "NVDA")], _Settings(tmp_path / "paper.jsonl"), date(2026, 11, 9),
            client=client, now=datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc),  # 9:30 ET
        )
        assert record.late_entry is True
        assert [o[0] for o in client.opened] == ["NVDA"]

    def test_open_boundary_follows_daylight_saving(self, tmp_path):
        # 13:45 UTC is 9:45 ET in summer but 8:45 ET in winter.
        summer = self._run(tmp_path, datetime(2026, 7, 9, 13, 45, tzinfo=timezone.utc))
        assert summer.late_entry is True
        winter = run_paper_cycle(
            [_pick(1, "NVDA")], _Settings(tmp_path / "winter.jsonl"), date(2026, 12, 9),
            client=_FakeClient(), now=datetime(2026, 12, 9, 13, 45, tzinfo=timezone.utc),
        )
        assert winter.late_entry is False

    def test_legacy_rows_have_no_flag(self, tmp_path):
        path = tmp_path / "paper.jsonl"
        path.write_text('{"cycle_date":"2026-11-05","equity":1.0,"opened":[],"closed":[]}\n')
        assert load_cycles(path)[0].late_entry is None

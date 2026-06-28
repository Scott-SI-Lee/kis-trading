import sys
import unittest
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from us_close_analysis import (
    KST,
    MarketMove,
    _data_quality,
    _is_scheduled_run_on_trading_session,
    _is_significant_driver_move,
)


def _move(
    key: str,
    change_pct: Optional[float],
    change_bp: Optional[float] = None,
    group: str = "macro",
    regular_market_time: Optional[str] = None,
) -> MarketMove:
    return MarketMove(
        key=key,
        label=key,
        symbol=key,
        group=group,
        price=100.0,
        prev_close=100.0,
        change_pct=change_pct,
        fetched_at="2026-06-27T00:00:00+00:00",
        change_bp=change_bp,
        regular_market_time=regular_market_time,
    )


class SignificantDriverMoveTest(unittest.TestCase):
    def test_us10y_uses_basis_point_threshold(self) -> None:
        move = _move("us10y", change_pct=0.1, change_bp=5.0)

        self.assertTrue(_is_significant_driver_move("us10y", move))

    def test_us10y_ignores_pct_threshold_for_significance(self) -> None:
        move = _move("us10y", change_pct=1.0, change_bp=4.9)

        self.assertFalse(_is_significant_driver_move("us10y", move))

    def test_non_yield_drivers_keep_pct_threshold(self) -> None:
        self.assertTrue(_is_significant_driver_move("nasdaq", _move("nasdaq", 0.5)))
        self.assertFalse(_is_significant_driver_move("nasdaq", _move("nasdaq", 0.49)))


class MarketFreshnessTest(unittest.TestCase):
    def test_data_quality_accepts_latest_completed_us_session(self) -> None:
        now = datetime(2026, 6, 27, 6, 0, tzinfo=KST)
        move = _move(
            "nasdaq",
            change_pct=0.7,
            group="major_index",
            regular_market_time="2026-06-26T20:00:00+00:00",
        )

        quality = _data_quality([move], [], {}, now=now)

        self.assertFalse(quality["stale_data"])
        self.assertFalse(quality["low_confidence"])
        self.assertEqual(quality["expected_us_session_date"], "2026-06-26")

    def test_data_quality_marks_stale_regular_session_data_low_confidence(self) -> None:
        now = datetime(2026, 6, 27, 6, 0, tzinfo=KST)
        stale_move = _move(
            "nasdaq",
            change_pct=0.7,
            group="major_index",
            regular_market_time="2026-06-25T20:00:00+00:00",
        )

        quality = _data_quality([stale_move], [], {}, now=now)

        self.assertTrue(quality["stale_data"])
        self.assertTrue(quality["low_confidence"])
        self.assertEqual(quality["low_confidence_reason"], "market_data_stale")
        self.assertEqual(quality["stale_market_symbols"][0]["symbol"], "nasdaq")

    def test_scheduler_skips_weekends_and_us_holidays(self) -> None:
        ny = ZoneInfo("America/New_York")

        self.assertTrue(
            _is_scheduled_run_on_trading_session(
                datetime(2026, 6, 26, 17, 0, tzinfo=ny).astimezone(KST)
            )
        )
        self.assertFalse(
            _is_scheduled_run_on_trading_session(
                datetime(2026, 6, 27, 17, 0, tzinfo=ny).astimezone(KST)
            )
        )
        self.assertFalse(
            _is_scheduled_run_on_trading_session(
                datetime(2026, 7, 3, 17, 0, tzinfo=ny).astimezone(KST)
            )
        )


if __name__ == "__main__":
    unittest.main()

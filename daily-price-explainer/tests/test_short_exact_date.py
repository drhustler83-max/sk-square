"""Network-free regressions for true-date shorting collection."""
import csv
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools import factor_logger, short


def frame(date, **values):
    return pd.DataFrame([values], index=pd.to_datetime([date]))


class ShortExactDateTests(unittest.TestCase):
    def collect(self, balance, volume, previous=None, date="20260929", prior="20260928"):
        with patch.object(short.stock, "get_shorting_balance_by_date",
                          side_effect=[balance, previous]) as bal, \
                patch.object(short.stock, "get_shorting_volume_by_date", return_value=volume) as vol, \
                patch("tools.market._prev_trading_day", return_value=prior):
            result = short.get_shorting_data("402340", date)
        return result, bal, vol

    def test_requested_day_balance_volume_and_true_previous_day_change(self):
        result, bal, vol = self.collect(
            frame("20260929", 공매도잔고=60000, 비중=1.234),
            frame("20260929", 공매도=1200, 비중=0.456),
            frame("20260928", 공매도잔고=50000))
        self.assertEqual(result["balance_date"], "20260929")
        self.assertEqual(result["volume_date"], "20260929")
        self.assertEqual(result["shorting_balance"], 60000)
        self.assertEqual(result["shorting_balance_ratio"], 1.23)
        self.assertEqual(result["shorting_volume_ratio"], 0.46)
        self.assertEqual(result["balance_change"], 10000)
        self.assertEqual(result["signal"], "압력")
        self.assertEqual([c.args for c in bal.call_args_list],
                         [("20260929", "20260929", "402340"), ("20260928", "20260928", "402340")])
        vol.assert_called_once_with("20260929", "20260929", "402340")

    def test_unpublished_balance_keeps_current_day_volume_and_never_searches_back(self):
        result, bal, vol = self.collect(pd.DataFrame(), frame("20260929", 공매도=1200, 비중=0.45))
        self.assertIsNone(result["shorting_balance"])
        self.assertIsNone(result["balance_change"])
        self.assertIsNone(result["signal"])
        self.assertEqual(result["shorting_volume_ratio"], 0.45)
        bal.assert_called_once_with("20260929", "20260929", "402340")

    def test_stale_response_dates_are_rejected(self):
        result, bal, vol = self.collect(frame("20260928", 공매도잔고=60000, 비중=1.0),
                                        frame("20260928", 공매도=1200, 비중=0.45))
        self.assertIsNone(result["shorting_balance"])
        self.assertIsNone(result["shorting_volume_ratio"])
        self.assertEqual(set(result["errors"]), {"balance", "volume"})
        self.assertEqual(bal.call_count, 1)

    def test_holiday_gap_uses_previous_trading_day_not_previous_calendar_day(self):
        result, bal, vol = self.collect(frame("20260928", 공매도잔고=60000, 비중=1.0),
                                        frame("20260928", 공매도=1200, 비중=0.45),
                                        frame("20260923", 공매도잔고=55000),
                                        date="20260928", prior="20260923")
        self.assertEqual(result["balance_change"], 5000)
        self.assertEqual(result["prev_balance_date"], "20260923")
        self.assertEqual(bal.call_args.args, ("20260923", "20260923", "402340"))

    def test_missing_previous_source_does_not_manufacture_a_zero_change(self):
        result, bal, vol = self.collect(frame("20260929", 공매도잔고=60000, 비중=1.0),
                                        pd.DataFrame(), pd.DataFrame())
        self.assertEqual(result["shorting_balance"], 60000)
        self.assertIsNone(result["balance_change"])
        self.assertIsNone(result["signal"])

    def test_zero_values_are_available_data(self):
        result, bal, vol = self.collect(frame("20260929", 공매도잔고=0, 비중=0.0),
                                        frame("20260929", 공매도=0, 비중=0.0),
                                        frame("20260928", 공매도잔고=0))
        self.assertEqual(result["shorting_balance"], 0)
        self.assertEqual(result["shorting_volume_ratio"], 0.0)
        self.assertEqual(result["balance_change"], 0)
        self.assertEqual(result["signal"], "중립")

    def test_balance_amount_is_never_used_as_quantity(self):
        result, bal, vol = self.collect(frame("20260929", 잔고금액=1000000, 비중=1.0), pd.DataFrame())
        self.assertIsNone(result["shorting_balance"])
        self.assertIn("balance", result["errors"])

    def test_balance_failure_does_not_prevent_volume_collection(self):
        with patch.object(short.stock, "get_shorting_balance_by_date", side_effect=RuntimeError("source failure")), \
                patch.object(short.stock, "get_shorting_volume_by_date",
                             return_value=frame("20260929", 공매도=0, 비중=0.0)):
            result = short.get_shorting_data("402340", "20260929")
        self.assertIsNone(result["shorting_balance"])
        self.assertEqual(result["shorting_volume_ratio"], 0.0)
        self.assertEqual(result["errors"]["balance"], "source failure")

    def test_logger_rejects_stale_provider_dates_and_preserves_existing_f4(self):
        real = Path(__file__).resolve().parent.parent / "data/factor_log.csv"
        with real.open(encoding="utf-8-sig", newline="") as file:
            before = {r["date"]: r for r in csv.DictReader(file)}["20260928"]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            copy = Path(directory) / "factor_log.csv"
            copy.write_bytes(real.read_bytes())
            stack.enter_context(patch.object(factor_logger, "LOG_PATH", copy))
            stack.enter_context(patch("tools.nav_v2.get_live_nav_v2", return_value={}))
            stack.enter_context(patch("tools.investor_flow.get_daily_flow", return_value={
                "foreign_net": 0, "institution_net": 0, "individual_net": 0, "foreign_own_pct": 0}))
            stack.enter_context(patch("tools.regular_macro.get_daily_macro", return_value={}))
            stack.enter_context(patch("tools.futures.get_futures_data", return_value={"listed": False}))
            stack.enter_context(patch.object(short, "get_shorting_data", return_value={
                "balance_date": "20260923", "volume_date": "20260923",
                "shorting_balance": 999999, "shorting_volume_ratio": 99}))
            factor_logger.collect_and_log("20260928")
            with copy.open(encoding="utf-8-sig", newline="") as file:
                after = {r["date"]: r for r in csv.DictReader(file)}["20260928"]
            for key in ("shorting_balance", "shorting_balance_ratio",
                        "shorting_volume_ratio", "shorting_balance_change"):
                self.assertEqual(after[key], before[key])


if __name__ == "__main__":
    unittest.main()

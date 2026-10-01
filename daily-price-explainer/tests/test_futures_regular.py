"""Regression checks for known spread-contract mistakes in SK Square futures."""

import csv
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import futures


BASE = Path(__file__).resolve().parent.parent


class KRXOutrightSelectionTests(unittest.TestCase):
    def test_six_spread_dates_select_outright_with_saved_basis(self):
        source = json.loads((BASE / "data/snapshots/20260930/krx_futures_six.json")
                            .read_text(encoding="utf-8"))["dates"]
        with (BASE / "data/factor_log.csv").open(encoding="utf-8-sig", newline="") as file:
            factors = {row["date"]: row for row in csv.DictReader(file)}
        self.assertEqual(len(source), 6)
        for date, captured in source.items():
            with self.subTest(date=date), patch.object(
                    futures, "market_rows", return_value=captured["contracts"]):
                near, contracts = futures._skq_near_month(date)
                self.assertIsNotNone(near)
                self.assertTrue(re.search(r"\bF\s+\d{6}\b", near["futures_ticker"]))
                self.assertEqual(near["volume"], max(row["volume"] for row in contracts
                                                     if row["volume"] > 0))
                spot = int(captured["spot"]["TDD_CLSPRC"].replace(",", ""))
                self.assertEqual(near["close"] - spot,
                                 float(factors[date]["fut_basis"]))


if __name__ == "__main__":
    unittest.main()

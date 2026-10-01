"""Regression checks for KRX/NXT separation and native foreign categories."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import investor_flow
from tools.import_naver_krx_quantity import plan
from tools.repair_krx_factors import digest

BASE = Path(__file__).resolve().parent.parent
SOURCE = BASE / "data/snapshots/20261001/f3_source_scope/naver_pc_krx_0_1200.json"


class InvestorScopeTests(unittest.TestCase):
    def test_full_history_includes_missing_2022_and_pre_hts_dates(self):
        rows = investor_flow.parse_pc_trend_rows(json.loads(SOURCE.read_text(encoding="utf-8")), "402340")
        self.assertEqual(len(rows), 1183)
        self.assertEqual(min(rows), "20211129")
        self.assertEqual(rows["20221109"], {
            "foreign_net": 89885, "institution_net": 11670,
            "individual_net": -100641, "foreign_own_pct": 42.11})

    def test_krx_foreign_category_and_market_differ_from_hts(self):
        rows = investor_flow.parse_pc_trend_rows(json.loads(SOURCE.read_text(encoding="utf-8")), "402340")
        self.assertEqual([rows["20260929"][f] for f in
                          ("foreign_net", "institution_net", "individual_net")], [11629, 1698, -14216])
        nxt = investor_flow.parse_pc_trend_rows(json.loads(
            (SOURCE.parent / "naver_pc_nxt_0_60.json").read_text(encoding="utf-8")), "402340")
        self.assertEqual(rows["20260929"]["foreign_net"] + nxt["20260929"]["foreign_net"], 7544 + 298)

    def test_live_fetch_explicitly_requests_krx_market(self):
        row = json.loads(SOURCE.read_text(encoding="utf-8"))[0]
        response = Mock()
        response.json.return_value = [row]
        with patch("requests.get", return_value=response) as get, patch("truststore.inject_into_ssl"):
            flow = investor_flow.get_daily_flow("402340", "20260930")
            self.assertEqual(flow["foreign_net"], 29397)
            self.assertEqual(get.call_args.kwargs["params"]["tradeType"], "KRX")
        with patch.object(investor_flow, "fetch_trend", return_value={"20260930": flow}):
            with self.assertRaisesRegex(ValueError, "no investor quantities"):
                investor_flow.get_daily_flow("402340", "20261001")

    def test_migration_rejects_nxt_or_tampered_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / SOURCE.name
            path.write_bytes(SOURCE.read_bytes())
            manifest = [{"file": path.name, "sha256": digest(path.read_bytes()),
                         "url": "https://stock.naver.com/api/domestic/detail/402340/trend?tradeType=NXT"}]
            (path.parent / "capture_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicit Naver KRX"):
                plan(BASE / "data/factor_log.csv", path)
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                plan(BASE / "data/factor_log.csv", path)

    def test_missing_quantity_is_rejected_instead_of_becoming_zero(self):
        row = json.loads(SOURCE.read_text(encoding="utf-8"))[0]
        row["foreignerPureBuyQuant"] = ""
        with self.assertRaises(ValueError):
            investor_flow.parse_pc_trend_rows([row], "402340")

    def test_migration_preserves_the_user_deferred_37_day_gap(self):
        import csv
        import io
        patched, report = plan(BASE / "data/factor_log.csv", SOURCE)
        rows = {r["date"]: r for r in csv.DictReader(io.StringIO(patched.decode("utf-8-sig")))}
        self.assertEqual(len(report["flow_gap_preserved_by_user"]), 37)
        for date in report["flow_gap_preserved_by_user"]:
            self.assertEqual([rows[date][f] for f in
                              ("foreign_net", "institution_net", "individual_net")], ["", "", ""])
        self.assertEqual(rows["20211129"]["foreign_net"], "-434698")


if __name__ == "__main__":
    unittest.main()

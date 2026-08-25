"""Guards on the transform stage and the mabl payload contract.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sgpipe import config, sources, transform  # noqa: E402

CFG = config.load_config()
SEED = json.load(open(os.path.join(config.ROOT, "fixtures", "seed_rows.json")))


def build(run_id="test-run"):
    raw = []
    for row in SEED:
        raw += sources.fetch_rates(row, run_id,
                                   CFG["mock"]["price_jitter_pct"],
                                   CFG["mock"]["duplicate_options"])
    return raw


class TestFanOut(unittest.TestCase):
    def test_options_per_seed_row(self):
        opts = sources.fetch_rates(SEED[0], "r", 6, 0)
        self.assertEqual(len(opts), 18, "one seed row returns 18 rate options")

    def test_duplicates_are_emitted(self):
        n = CFG["mock"]["duplicate_options"]
        self.assertEqual(len(sources.fetch_rates(SEED[0], "r", 6, n)), 18 + n)

    def test_vin_drives_the_vehicle_columns(self):
        rec = sources.fetch_rates(SEED[0], "r", 6, 0)[0]
        self.assertEqual((rec["make"], rec["model"]), ("MINI", "COOPER"))


class TestTransform(unittest.TestCase):
    def setUp(self):
        self.raw = build()
        self.dealers = sources.dealer_lookup()

    def test_filter_excludes_unlisted_product_codes(self):
        kept = transform.filter_products(self.raw, ["BMMC"])
        self.assertTrue(kept)
        self.assertEqual({r["productCode"] for r in kept}, {"BMMC"})

    def test_dedupe_collapses_only_the_duplicates(self):
        deduped = transform.dedupe(self.raw, CFG["filter"]["unique_on"])
        expected = len(SEED) * 18
        self.assertEqual(len(deduped), expected)

    def test_dedupe_without_vin_would_collapse_every_seed_row(self):
        """Why vin is required in unique_on. Regression guard."""
        bad = transform.dedupe(self.raw, ["productCode", "coverageCode",
                                          "termMonthsMin", "termMonthsMax"])
        self.assertEqual(len(bad), 18)
        self.assertLess(len(bad), len(SEED) * 18)

    def test_merge_joins_static_attributes(self):
        merged = transform.merge_static(self.raw, self.dealers)
        self.assertTrue(all(r["dealerName"] for r in merged))

    def test_merge_miss_leaves_empty_not_none(self):
        merged = transform.merge_static([{"companyId": "NOPE"}], self.dealers)
        self.assertEqual(merged[0]["dealerName"], "")

    def test_validate_quarantines_instead_of_raising(self):
        good, rejects = transform.validate([{"vin": "", "productCode": "BMMC",
                                             "sellerCost": 1, "transactionId": "x"}])
        self.assertEqual(good, [])
        self.assertEqual(rejects[0]["missing"], ["vin"])

    def test_prepare_is_deterministic_for_a_run_id(self):
        a = transform.prepare(build("same"), CFG["filter"], self.dealers)["rows"]
        b = transform.prepare(build("same"), CFG["filter"], self.dealers)["rows"]
        self.assertEqual(a, b)

    def test_prices_move_between_runs(self):
        a = transform.prepare(build("run-a"), CFG["filter"], self.dealers)["rows"]
        b = transform.prepare(build("run-b"), CFG["filter"], self.dealers)["rows"]
        self.assertNotEqual([r["sellerCost"] for r in a],
                            [r["sellerCost"] for r in b])


class TestScenarioPayload(unittest.TestCase):
    """The mabl DataTable contract."""

    def setUp(self):
        rows = transform.prepare(build(), CFG["filter"],
                                 sources.dealer_lookup())["rows"]
        self.scenarios = transform.to_scenarios(rows, transform.API_COLUMNS)

    def test_every_value_is_a_string(self):
        for s in self.scenarios:
            for v in s["variables"]:
                self.assertIsInstance(v["value"], str)

    def test_none_becomes_empty_string_not_the_word_none(self):
        self.assertEqual(transform.cell(None), "")
        self.assertNotIn("None", [v["value"] for s in self.scenarios
                                  for v in s["variables"]])

    def test_every_row_carries_every_column(self):
        for s in self.scenarios:
            self.assertEqual([v["name"] for v in s["variables"]],
                             transform.API_COLUMNS)

    def test_within_mabl_ceilings(self):
        self.assertLessEqual(len(self.scenarios), 1000)
        self.assertLessEqual(len(transform.API_COLUMNS), 500)

    def test_row_names_follow_the_workspace_convention(self):
        self.assertEqual(self.scenarios[0]["name"], "Rate_1")

    def test_target_names_carry_no_timestamp(self):
        """A timestamp in the name is what produced duplicate tables."""
        for key in ("api_table_name", "ui_table_name"):
            name = CFG["targets"][key]
            self.assertNotRegex(name, r"\d{4}-\d{2}-\d{2}")


if __name__ == "__main__":
    unittest.main()

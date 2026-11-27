from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.documents import DocumentCache, DocumentError
from au_ree_v3.semantics import ProcessContext, SemanticContext, assess_semantic_use, compare_semantic_use
from au_ree_v3.technical_processing import _direct_value, _locate, _mass_balance, run_technical_pilot

ROOT = Path(__file__).resolve().parents[1]


class TechnicalEvidenceTests(unittest.TestCase):
    def test_exact_reported_zero_remains_numeric(self):
        zero = {"raw_value": "0.0", "raw_unit": "%"}
        self.assertEqual(_direct_value(zero, {"quote": "Removed fraction was 0.0%."}, []), Decimal("0.0"))
        with self.assertRaisesRegex(DocumentError, "UNIT_UNBOUND"):
            _direct_value(zero, {"quote": "Removed fraction was not reported."}, [])
        for state in ("NOT_DISCLOSED", "UNKNOWN", "NOT_APPLICABLE"):
            with self.subTest(state=state):
                missing = {"raw_value": None, "raw_unit": None, "value_state": state}
                self.assertIsNone(_direct_value(missing, {"quote": "No metric available."}, []))
                with self.assertRaisesRegex(ValueError, "cannot contain a number"):
                    _direct_value({**zero, "value_state": state}, {"quote": "0.0%"}, [])

    def test_reagent_table_units_must_be_visible(self):
        acid = {"raw_value": "24.8", "raw_unit": "kg/t", "table": "Table 4", "column": "Acid"}
        row = {"quote": "AVERAGE 1.8 32.3% 84.9% 1486 30.6% 24.8"}
        with self.assertRaisesRegex(DocumentError, "TECHNICAL_TABLE_UNIT_UNBOUND"):
            _direct_value(acid, row, [{"quote": "EXTRACTED TREO (ppm)"}])
        self.assertEqual(_direct_value(acid, row, [{"quote": "CONSUMPTION (kg HCl/t of leach feed)"}]), Decimal("24.8"))

    def test_mass_balance_rejects_report_even_at_favourable_rounding_limits(self):
        binding = {
            "quantity_kind":"PROCESS_YIELD", "raw_unit":"%",
            "material_balance": {"head_grade_pct":"0.14", "product_grade_pct":"1.3", "analyte":"TREO"},
        }
        data = _mass_balance(binding, Decimal("51"), "0.14% feed produced a 1.3% concentrate at 51% mass recovery")
        self.assertEqual(data["status"], "CONFLICT")
        self.assertGreater(Decimal(data["conservative_lower_pct"]), Decimal(435))
        self.assertEqual(data["formula"], "mass_yield_pct * product_grade_pct / feed_grade_pct")

    def test_like_for_like_experimental_use_passes_but_endpoints_and_forecasts_do_not(self):
        process = ProcessContext(
            experiment_id="synthetic", test_date="2026-05-01", feed="CRUSHED_ORE", product="LEACH_LIQUOR",
            step="LEACH", start="ORE_FEED", end="LEACH_LIQUOR", scale="BENCH", target_species=("Nd2O3",),
            procedure="BOTTLE_ROLL_LEACH", conditions=(("duration", "6 h"),),
            sample_representativeness="DOCUMENTED_COMPOSITE",
        )
        common = SemanticContext(
            quantity_kind="PROCESS_EXTRACTION", project_id="synthetic-first", scope_id="synthetic-scope",
            material="WHOLE_ORE", denominator_basis="FEED_ND2O3", chemical_species=("Nd2O3",), unit="%",
            temporal_status="ELIGIBLE", temporal_decision_id="f"*64, research_cutoff="2026-11-28",
            process=process,
        )

        def measured(context):
            return assess_semantic_use(
                context, purpose="T1A", observation_ids=(context.project_id,), source_sha256="a"*64,
                evidence_anchor_ids=(context.project_id + "-anchor",),
            )

        first = measured(common)
        second = replace(common, project_id="synthetic-second")
        self.assertEqual(first.status, "COMPARABLE_DIRECT")
        self.assertEqual(compare_semantic_use(first, measured(second)).status, "COMPARABLE_DIRECT")
        full_flowsheet = measured(replace(second, process=replace(
            process, step="FULL_FLOWSHEET", end="FINAL_SEPARATED_OXIDE_PRODUCT",
        )))
        self.assertEqual(compare_semantic_use(first, full_flowsheet).status, "NOT_COMPARABLE")
        pilot = measured(replace(second, process=replace(process, scale="PILOT")))
        self.assertIn("process.scale", compare_semantic_use(first, pilot).incompatible_dimensions)
        model = measured(replace(second, observation_role="FORECAST"))
        self.assertEqual(compare_semantic_use(first, model).status, "NOT_COMPARABLE")

    def test_independent_synthetic_oracle_matches_distinct_project_use(self):
        path = ROOT/"tests/fixtures/technical_semantic_synthetic_oracle_v1.json"
        oracle = json.loads(path.read_bytes())
        self.assertIs(oracle["synthetic"], True)
        seen = {}
        for row in oracle["cases"]:
            seen[row["id"]] = assess_semantic_use(
                SemanticContext(**row["context"]), purpose=oracle["purpose"], observation_ids=(row["id"],),
                source_sha256="a"*64, evidence_anchor_ids=(row["id"] + "-synthetic-anchor",),
            )
        for row in oracle["pairs"]:
            with self.subTest(left=row["left"], right=row["right"]):
                result = compare_semantic_use(seen[row["left"]], seen[row["right"]])
                self.assertEqual(result.status, row["expected"])
                self.assertTrue(set(row.get("incompatible_dimensions", ())) <= set(result.incompatible_dimensions))


class TechnicalNativePageTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AU_REE_TECHNICAL_NATIVE_PDF"), "exact Wolverine source PDF is required")
    def test_wolverine_recovery_and_conditions_are_located_in_the_original(self):
        from hashlib import sha256

        plan = json.loads((ROOT / "config/research_profiles/technical_holdout_bindings_v1.json").read_bytes())
        binding = next(row for row in plan["bindings"] if row["id"] == "holdout-wolverine-variability-treo-recovery")
        payload = Path(os.environ["AU_REE_TECHNICAL_NATIVE_PDF"]).read_bytes()
        digest = sha256(payload).hexdigest()
        self.assertEqual(digest, binding["source_sha256"])
        source = DocumentCache().pdf(payload, digest)
        location = _locate(source, binding["source_span"])
        contexts = [_locate(source, span) for span in binding["context_spans"]]
        self.assertEqual(location["page"], 175)
        self.assertEqual(_direct_value(binding, location, contexts), Decimal("84"))


@unittest.skipUnless(os.environ.get("AU_REE_TECHNICAL_MANIFEST") and os.environ.get("AU_REE_TECHNICAL_CAS"),
                     "the fixed 144-SHA manifest and content store must be supplied")
class TechnicalNativeCorpusTest(unittest.TestCase):
    def test_original_corpus_pilot_is_repeatable_and_keeps_its_results_shadow(self):
        paths = (
            ROOT/"config/research_profiles/au_ree_2026-11-28.json",
            Path(os.environ["AU_REE_TECHNICAL_MANIFEST"]),
            Path(os.environ["AU_REE_TECHNICAL_CAS"]),
            ROOT/"config/research_profiles/technical_bindings_v1.json",
        )
        expected = ROOT/"tests/fixtures/technical_processing_oracle_v1.json"
        with TemporaryDirectory() as folder:
            first = run_technical_pilot(*paths, Path(folder)/"first", oracle=expected)
            repeat = run_technical_pilot(*paths, Path(folder)/"repeat", oracle=expected)
            self.assertEqual((first["status"], first["oracle_status"], first["source_objects_read_back"]),
                             ("PASS", "PASS", 144))
            self.assertEqual(first["source_anchored_observations"], 15)
            self.assertEqual((first["accepted_count"], first["ranked_panels"]), (0, 0))
            self.assertEqual(first["rankings_status"], "INSUFFICIENT_COHORT")
            for filename in ("technical_observations.jsonl", "technical_pairs.json", "source_readback.json"):
                self.assertEqual((Path(folder)/"first"/filename).read_bytes(),
                                 (Path(folder)/"repeat"/filename).read_bytes())
            self.assertEqual(first["observation_artifact_sha256"], repeat["observation_artifact_sha256"])
            comparisons = json.loads((Path(folder)/"first/technical_oracle.json").read_bytes())
            self.assertEqual(comparisons["reviewed_pair_confusion"]["FP"], 0)

    def test_original_heldout_company_families_cannot_be_mixed(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)
            receipt = run_technical_pilot(
                ROOT/"config/research_profiles/au_ree_2026-11-28.json",
                Path(os.environ["AU_REE_TECHNICAL_MANIFEST"]), Path(os.environ["AU_REE_TECHNICAL_CAS"]),
                ROOT/"config/research_profiles/technical_holdout_bindings_v1.json", output,
                oracle=ROOT/"tests/fixtures/technical_holdout_oracle_v1.json",
            )
            self.assertEqual((receipt["O3"], receipt["source_anchored_observations"]), ("PASS", 2))
            self.assertEqual(receipt["cross_project_compatible_pairs"], 0)
            comparisons = json.loads((output/"technical_oracle.json").read_bytes())
            self.assertEqual(comparisons["reviewed_pair_confusion"], {"TP":0, "FP":0, "TN":1, "FN":0})
            self.assertEqual((comparisons["holdout_precision"], comparisons["holdout_recall"]),
                             ("UNMEASURED", "UNMEASURED"))

"""Check the recovered oxide table against independently transcribed source cells."""

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import unittest

from au_ree_v3.composition_extraction import classified_total_oxides
from au_ree_v3.documents import DocumentError, pdf_pages
from au_ree_v3.resource_extraction import classified_sections, narrative_table


BINDINGS = Path(__file__).resolve().parents[1] / "config/research_profiles/resource_bindings_v1.json"
ORACLE = Path(__file__).with_name("fixtures") / "wolverine_constituent_oracle_v1.json"
WOLVERINE = "e29ab1305c8edb5bd1074806d5cf31c323c82cfd68c5c5b2e9b545137f09ccb0"
MT_WELD = "fe26ac29c4773a5af95e909a1cd95ca1e9ad37c34560dfa72c1e9674e4c8a503"


@unittest.skipUnless(os.environ.get("AU_REE_FRESH_ASSETS") and shutil.which("pdftotext"),
                     "exact issuer PDF bytes and pdftotext required")
class CompositionEvidenceTests(unittest.TestCase):
    def pages(self, expected_hash, last_page):
        assets = Path(os.environ["AU_REE_FRESH_ASSETS"])
        candidates = (assets / expected_hash, assets / f"{expected_hash}.pdf")
        confirmed = next((path for path in candidates if path.exists()), None)
        self.assertIsNotNone(confirmed, f"missing exact evidence {expected_hash}")
        content = confirmed.read_bytes()
        self.assertEqual(sha256(content).hexdigest(), expected_hash)
        return pdf_pages(content, last_page)

    def test_wolverine_constituents_anchor_to_matching_total_table(self):
        bindings = json.loads(BINDINGS.read_bytes())["bindings"]
        binding = next(item for item in bindings if item["case_id"] == "wolverine-deposit-total")
        pages = self.pages(WOLVERINE, 6)
        primary, = (item for item in classified_sections(pages, binding, binding["publication_date"])
                    if item.primary)
        binding = {**binding, "composition_table": {"page": 6, "number": 5}}
        cells = classified_total_oxides(pages, binding, primary)
        actual = [(item.chemical_basis, item.token, item.raw_unit, item.page, item.table, item.row)
                  for item in cells]
        self.assertEqual(actual, [
            ("Pr6O11", "60", "ppm", 6, "Table 5", "Total"),
            ("Nd2O3", "280", "ppm", 6, "Table 5", "Total"),
            ("Dy2O3", "840", "ppm", 6, "Table 5", "Total"),
            ("Tb4O7", "120", "ppm", 6, "Table 5", "Total"),
        ])
        self.assertEqual(primary.measurements[1].token, "0.96")
        self.assertEqual(primary.cutoff, "0.15")
        self.assertEqual(primary.effective_date, "2025-01-15")

        binding["composition_table"] = {"page": 6, "number": 4}
        with self.assertRaisesRegex(DocumentError, "OXIDE_SCOPE_DATE_OR_CUTOFF_MISMATCH"):
            classified_total_oxides(pages, binding, primary)

    def test_mt_weld_reserve_table_does_not_supply_resource_constituents(self):
        bindings = json.loads(BINDINGS.read_bytes())["bindings"]
        binding = next(item for item in bindings if item["case_id"] == "mt-weld-resource")
        pages = self.pages(MT_WELD, 23)
        resource, = narrative_table(pages, binding, binding["publication_date"])
        binding = {**binding, "section": "Total", "composition_table": {"page": 23, "number": 7}}
        with self.assertRaisesRegex(DocumentError, "OXIDE_SCOPE_DATE_OR_CUTOFF_MISMATCH"):
            classified_total_oxides(pages, binding, resource)

    def test_reviewed_additional_wolverine_oxides_and_yttria_hold(self):
        oracle = json.loads(ORACLE.read_bytes())
        binding_path = BINDINGS.with_name("resource_bindings_v5.json")
        binding = next(item for item in json.loads(binding_path.read_bytes())["bindings"]
                       if item["case_id"] == "wolverine-deposit-total")
        pages = self.pages(oracle["source_sha256"], 7)
        display_table, reference_table = oracle["table"], oracle["reference"]
        for evidence in (display_table, reference_table):
            self.assertIn(evidence["quote"], " ".join(pages[evidence["page"] - 1].split()))
        self.assertIn(reference_table["chemistry_quote"], " ".join(pages[reference_table["page"] - 1].split()))
        resource, = (item for item in classified_sections(pages, binding, oracle["publication_date"]) if item.primary)
        self.assertEqual((resource.scope, resource.effective_date, resource.category),
                         (oracle["scope"], oracle["effective_date"], oracle["classification"]))
        self.assertEqual([resource.cutoff, resource.cutoff_unit, resource.cutoff_analyte], oracle["cutoff"])
        rows = classified_total_oxides(pages, binding, resource, source_sha256=oracle["source_sha256"])
        observed = {cell.chemical_basis: cell for cell in rows}
        for species, value in {**oracle["legacy_selected"], **oracle["new_selected"]}.items():
            cell = observed[species]
            self.assertEqual((cell.token, cell.raw_unit, cell.page, cell.table, cell.row, cell.column, cell.conflict),
                             (value, "ppm", 6, "Table 5", "Total", species, None))
        self.assertEqual(set(observed), oracle["legacy_selected"].keys() | oracle["new_selected"].keys() | {"Y2O3"})
        yttria = observed["Y2O3"]
        expected_yttria = oracle["held"]["Y2O3"]
        companion = yttria.evidence_context["same_statement_precision_conflict"]
        self.assertEqual((yttria.conflict, yttria.token, companion["token"], companion["row_quote"]),
                         (expected_yttria["reason"], expected_yttria["table5_token"], expected_yttria["table2_token"],
                          reference_table["quote"]))
        for key, expected in (("companion_interval_ppm", "table2_nominal_bounds_ppm"),
                              ("printed_interval_ppm", "table5_nominal_bounds_ppm")):
            self.assertEqual(list(map(Decimal, companion[key])), list(map(Decimal, expected_yttria[expected])))
        closure = observed["La2O3"].evidence_context["reviewed_oxide_table"]["nominal_precision"]
        self.assertEqual(closure["printed_sum_ppm"], oracle["printed_closure_ppm"]["table5_sum"])
        self.assertEqual(closure["use"], "TABLE_CHECK_ONLY")

        # A different source edition at the same URL cannot activate new selections.
        unmatched = classified_total_oxides(pages, binding, resource, source_sha256="a" * 64)
        self.assertEqual({cell.chemical_basis for cell in unmatched}, oracle["legacy_selected"].keys())
        for page, number in ((5, 3), (6, 4), (7, 6)):
            scope = {**binding, "composition_table": {**binding["composition_table"], "page": page, "number": number}}
            with self.assertRaisesRegex(DocumentError, "OXIDE_SCOPE_DATE_OR_CUTOFF_MISMATCH"):
                classified_total_oxides(pages, scope, resource, source_sha256=oracle["source_sha256"])
        repeated_member = replace(resource, context=tuple((page, quote.replace("La2O3,", "La2O3, La2O3,"))
                                                          for page, quote in resource.context))
        with self.assertRaisesRegex(DocumentError, "COMPANION_OXIDE_DENOMINATOR_UNBOUND"):
            classified_total_oxides(pages, binding, repeated_member, source_sha256=oracle["source_sha256"])
        binding["composition_table"]["reviewed_nonmagnetic_species"]["species"].append("Y2O3")
        with self.assertRaisesRegex(DocumentError, "UNAPPROVED_COMPANION_OXIDE_MEMBERS"):
            classified_total_oxides(pages, binding, resource, source_sha256=oracle["source_sha256"])


@unittest.skipUnless(all(os.environ.get(key) for key in ("AU_REE_STAGE5_BASELINE_BUILD", "AU_REE_STAGE5_CANDIDATE_BUILD")),
                     "exact before and after scientific builds required")
class ConstituentAdmissionIntegrationTests(unittest.TestCase):
    def test_only_independent_constituents_change_formal_selection(self):
        baseline = Path(os.environ["AU_REE_STAGE5_BASELINE_BUILD"])
        candidate = Path(os.environ["AU_REE_STAGE5_CANDIDATE_BUILD"])
        oracle = json.loads(ORACLE.read_bytes())

        def rows(root):
            with (root / "observations/observations.jsonl").open(encoding="utf-8") as stream:
                records = map(json.loads, stream)
                return {row["observation_id"]: row for row in records}

        old, new = rows(baseline), rows(candidate)
        self.assertEqual(old, {key: new[key] for key in old})
        added = [new[key] for key in new.keys() - old.keys()]
        self.assertEqual(len(added), len(oracle["new_selected"]) + 1)
        for row in added:
            self.assertEqual((row["source_sha256"], row["project_id"], row["effective_date"], row["publication_date"]),
                             (oracle["source_sha256"], oracle["project_id"], oracle["effective_date"], oracle["publication_date"]))
            self.assertEqual((row["material_basis"], row["table"], row["row"]), ("WHOLE_ORE", "Table 5", "Total"))
        selected = {row["chemical_basis"]: row["raw_value"] for row in added if row["selection_status"] == "SELECTED"}
        self.assertEqual(selected, oracle["new_selected"])
        refused, = (row for row in added if row["selection_status"] == "UNVALIDATED")
        self.assertEqual((refused["chemical_basis"], refused["outcome"], refused["reason"]),
                         ("Y2O3", "SCIENTIFIC_CONFLICT", oracle["held"]["Y2O3"]["reason"]))
        self.assertEqual(refused["same_statement_precision_conflict"]["row_quote"], oracle["reference"]["quote"])
        with (candidate / "observations/scientific_conflicts.jsonl").open(encoding="utf-8") as stream:
            self.assertIn(refused, list(map(json.loads, stream)))
        self.assertEqual((baseline / "census/census.csv").read_bytes(), (candidate / "census/census.csv").read_bytes())

        def panel(path, name, key):
            return json.loads((path / "decision" / name).read_bytes())[key]

        before = panel(baseline, "mcda_inputs.json", "criteria_by_project")
        after = panel(candidate, "mcda_inputs.json", "criteria_by_project")
        self.assertEqual(comparable_values(before), comparable_values(after))
        for name in ("mcda_inputs.json", "mcda_results.json"):
            self.assertEqual(panel(baseline, name, "panels"), panel(candidate, name, "panels"))


def comparable_values(projects):
    return {(project, code): (cell["accepted_value"], cell["utility"], cell["derivation"])
            for project, criteria in projects.items() for code, cell in criteria.items()
            if cell["state"] == "COMPARABLE"}

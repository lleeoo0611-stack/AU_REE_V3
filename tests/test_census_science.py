"""Verify exact scope, membership, printed precision and current-source shadow values."""

from collections import defaultdict
import csv
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.census_metadata import load_projection_policy
from au_ree_v3.census_science import _derive_components, census_science_views, validate_resource_presence
from au_ree_v3.comparability import composition_comparison, resource_bundle
from au_ree_v3.contracts import AcceptedResearchValue, Entity, EvidenceAnchor, ResearchRelease, ResourceScope
from au_ree_v3.documents import DocumentCache
from au_ree_v3.fresh_decision import load_policy
from au_ree_v3.fresh_research import build_research
from au_ree_v3.registry import load_census_columns, load_profile
from au_ree_v3.research_delta import ResearchBuild
from au_ree_v3.research_inputs import ResearchInput
from au_ree_v3.sources import ContentStore
from au_ree_v3.store import Store

from test_fresh_decision import PROJECT_IDS, _observation, _source


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/research_profiles/au_ree_2026-11-28.json"
COLUMNS = tuple(load_census_columns(load_profile(PROFILE).census_columns_path))
RULE = load_policy()["s1_admission"]
# Independent transcription of the PDF's Table 5 total row, used here as
# a synthetic selection bundle. The live release selects only four columns.
TABLE_5_PPM = {
    "La2O3": "170", "CeO2": "420", "Pr6O11": "60", "Nd2O3": "280", "Sm2O3": "200",
    "Eu2O3": "40", "Gd2O3": "560", "Tb4O7": "120", "Dy2O3": "840", "Ho2O3": "170",
    "Er2O3": "490", "Tm2O3": "70", "Yb2O3": "380", "Lu2O3": "50", "Y2O3": "5700",
}


def _input(variable, token, unit, chemical, *, oxide=False):
    pid = PROJECT_IDS[0]
    source, _, proof = _source(pid)
    project = Entity(entity_id=pid, name="Synthetic project", native_namespace="fixture", native_id=pid, kind="PROJECT")
    scope = ResourceScope(scope_id=pid + ":scope", project_id=pid, name="Synthetic aggregate Mineral Resource")
    anchor = EvidenceAnchor(
        anchor_id=variable + ":anchor", source_key=source.source_key, source_sha=source.sha256,
        page=2, locator="physical page 2, Table 1", table="Table 1", row="Total",
        column=chemical if oxide else variable, quote=f"{chemical} {token} {unit}",
    )
    raw, candidate, validation = _observation(project, scope, source, anchor, proof, variable, token, unit, chemical,
                                               shift_composition=False)
    if oxide:
        raw = replace(raw, denominator_basis="WHOLE_ORE")
        candidate = replace(candidate, denominator_basis="WHOLE_ORE")
    accepted = AcceptedResearchValue(acceptance_id=variable + ":acceptance", release_id="fixture-release",
                                     selection_key=variable, validation_id=validation.validation_id,
                                     reason="synthetic only")
    trace = {"status": "ELIGIBLE", "decision_id": validation.temporal_decision_id, "cutoff": validation.cutoff}
    return ResearchInput(accepted, validation, candidate, raw, source, scope, anchor, trace)


def _bundle(*, omit=(), replace_grade=None):
    core = [
        _input("ore_mt", "7.3", "Mt", "TREO"), _input("grade", "0.96", "%", "TREO"),
        _input("contained_treo_kt", "70.500", "kt TREO", "TREO"),
    ]
    if replace_grade is not None:
        core[1] = replace_grade(core[1])
    rows = core + [_input(species.lower() + "_ppm", token, "ppm", species, oxide=True)
                   for species, token in TABLE_5_PPM.items() if species not in omit]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.raw.variable].append(row)
    return grouped


def _evaluate(grouped):
    resource = resource_bundle(grouped)
    assert resource.comparable
    compare = composition_comparison(grouped, resource, RULE)
    rows, refusals = _derive_components(grouped, resource, compare, COLUMNS, RULE)
    return {row["key"]: row for row in rows}, {row["key"]: row for row in refusals}


class CompositionProjectionTests(unittest.TestCase):
    def test_complete_formula_and_missing_members(self):
        full, _ = _evaluate(_bundle())
        self.assertEqual(Decimal(full["complete_oxide_sum_kt"]["value"]), Decimal("69.715"))
        self.assertEqual(Decimal(full["hree8_kt"]["value"]), Decimal("57.086"))
        self.assertEqual(Decimal(full["mreo_kt"]["value"]), Decimal("9.490"))
        self.assertIn("complete_sum_ratio", full)
        self.assertEqual(full["complete_oxide_sum_kt"]["rounding_interval"], ["69.183125", "70.247625"])
        for row in full.values():
            self.assertFalse(row["formal_admission"])
            self.assertEqual(len(row["formula"]["sha256"]), 64)
            self.assertTrue(all(item["physical_page"] and item["observation_id"] for item in row["inputs"]))
        partial, refusals = _evaluate(_bundle(omit=("Tb4O7",)))
        self.assertTrue({"Pr6O11_kt", "ndpr_kt"} <= partial.keys())
        for name in ("hree8_kt", "magnet_recalculated_kt", "complete_oxide_sum_kt", "complete_sum_ratio", "mreo_kt"):
            self.assertNotIn(name, partial)
        self.assertIn("Tb4O7", refusals["hree8_kt"]["required_members"])

    def test_foreign_editions_statements_dates_and_materials_never_mix(self):
        variations = (
            lambda item: replace(item, source=replace(item.source, source_version="other")),
            lambda item: replace(item, raw=replace(item.raw, statement_id="other")),
            lambda item: replace(item, raw=replace(item.raw, effective_date="2026-06-29")),
            lambda item: replace(item, raw=replace(item.raw, category="ORE_RESERVE")),
            lambda item: replace(item, raw=replace(item.raw, category="EXPLORATION_TARGET")),
            lambda item: replace(item, raw=replace(item.raw, material_basis="HEAVY_MINERAL")),
            lambda item: replace(item, raw=replace(item.raw, original_payload={
                **json.loads(item.raw.original_payload), "cutoff_grade": "0.20",
            })),
        )
        for change in variations:
            with self.subTest(change=change):
                rows = _bundle()
                rows["tb4o7_ppm"][0] = change(rows["tb4o7_ppm"][0])
                measures, refusals = _evaluate(rows)
                self.assertNotIn("Tb4O7_kt", measures)
                self.assertNotIn("mreo_kt", measures)
                self.assertEqual(refusals["tb4o7_ppm"]["status"], "NOT_COMPARABLE")
                self.assertIn("Pr6O11_kt", measures)

    def test_changed_treo_species_and_complete_sum_conflict(self):
        def change_definition(item, qualifier, label, replacement):
            payload = json.loads(item.raw.original_payload)
            context = payload["statement_context"]
            quote = context[0]["quote"].replace("TREO =", f"{qualifier} =")
            quote = quote.replace(*replacement)
            context[0]["quote"] = quote
            payload["reported_chemical_label"] = label
            return replace(item, raw=replace(item.raw, original_payload=payload))

        variations = (
            ("TREO + Sc2O3", "TREO + Sc2O3", ("Y2O3)", "Sc2O3)"), "Y2O3_kt"),
            ("TREO - CeO2", "TREO - CeO2", ("CeO2, ", ""), "CeO2_kt"),
        )
        for qualifier, label, substitution, blocked in variations:
            with self.subTest(label=label):
                measures, _ = _evaluate(_bundle(replace_grade=lambda item: change_definition(
                    item, qualifier, label, substitution,
                )))
                self.assertNotIn(blocked, measures)
                self.assertNotIn("complete_oxide_sum_kt", measures)
        rows = _bundle()
        original = rows["y2o3_ppm"][0]
        rows["y2o3_ppm"][0] = replace(original, raw=replace(original.raw, raw_value="4700"),
                                      candidate=replace(original.candidate, value="4700"))
        measures, refused = _evaluate(rows)
        self.assertNotIn("complete_oxide_sum_kt", measures)
        self.assertEqual(refused["complete_oxide_sum_kt"]["status"], "SCIENTIFIC_CONFLICT")

    def test_reported_ndpr_group_preserves_members_and_refuses_inconsistency(self):
        group = _input("ndpr_ppm", "340", "ppm", "NdPr_OXIDES", oxide=True)
        payload = {**json.loads(group.raw.original_payload),
                   "oxide_definition": {"page":2, "quote":"NdPr = Nd2O3 + Pr6O11"},
                   "unit_evidence": {"page":2, "quote":"NdPr (ppm)"}}
        group = replace(group, raw=replace(group.raw, original_payload=payload),
                        anchor=replace(group.anchor, column="NdPr"))
        rows = _bundle(omit=("Nd2O3", "Pr6O11"))
        rows["ndpr_ppm"].append(group)
        candidate, _ = _evaluate(rows)
        self.assertEqual(Decimal(candidate["ndpr_kt"]["value"]), Decimal("2.482"))
        self.assertEqual(candidate["ndpr_kt"]["formula"]["chemical_species"], ["Nd2O3", "Pr6O11"])
        self.assertNotIn("Nd2O3_kt", candidate)
        self.assertNotIn("Pr6O11_kt", candidate)
        rows = _bundle()
        inconsistent = replace(group, raw=replace(group.raw, raw_value="500"),
                               candidate=replace(group.candidate, value="500"))
        rows["ndpr_ppm"].append(inconsistent)
        candidate, refusals = _evaluate(rows)
        self.assertNotIn("ndpr_kt", candidate)
        self.assertEqual(refusals["ndpr_kt"]["status"], "SCIENTIFIC_CONFLICT")


@unittest.skipUnless(os.environ.get("AU_REE_CENSUS_SOURCE_MANIFEST") and os.environ.get("AU_REE_CENSUS_CAS"),
                     "the exact 144-original manifest and CAS are needed")
class NativeCensusScienceTest(unittest.TestCase):
    def test_live_frozen_sources_leave_formal_science_unchanged(self):
        sources = Path(os.environ["AU_REE_CENSUS_SOURCE_MANIFEST"])
        assets = Path(os.environ["AU_REE_CENSUS_CAS"])
        composition_sha = "e29ab1305c8edb5bd1074806d5cf31c323c82cfd68c5c5b2e9b545137f09ccb0"
        view = DocumentCache().pdf(ContentStore(assets).read(composition_sha), composition_sha)
        table = view.pages[5].split("Table 5: Wolverine Total Mineral Resource estimate at 15 January 2025", 1)[1]
        self.assertIn("0.15 % TREO cut-off grade", table)
        native_total, = (line.split()[2:] for line in table.split("Notes:", 1)[0].splitlines()
                         if line.strip().startswith("Total "))
        self.assertEqual([token.replace(",", "") for token in native_total], list(TABLE_5_PPM.values()))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            receipt = build_research(profile=PROFILE, bindings=ROOT/"config/research_profiles/resource_bindings_v3.json",
                                     sources=sources, assets=assets, out=root,
                                     code_commit="40309ea006d2eaace3fe4bbf33beccca492261de")
            self.assertEqual(receipt["verified_source_count"], 144)
            self.assertEqual(receipt["scientific_output_count"], 12)
            self.assertEqual(receipt["comparable_project_criteria"], 6)
            obs = root / "observations"
            extraction = json.loads((obs / "extraction.json").read_bytes())
            database = obs / "research.sqlite"
            before = sha256(database.read_bytes()).hexdigest()
            with Store(database, trusted_temporal_evidence_ids=extraction["trusted_temporal_evidence_ids"]) as store:
                release = store.get(ResearchRelease, extraction["release_id"])
                report = census_science_views(store, release, COLUMNS)
                self.assertEqual(census_science_views(store, release, COLUMNS), report)
            self.assertEqual(sha256(database.read_bytes()).hexdigest(), before)
            self.assertEqual((report["formal_selected_inputs"], report["new_formal_scientific_cells"]), (17, 0))
            self.assertEqual(len(report["verified_resource_presence"]), 4)
            self.assertEqual(len(report["approved_mcda_s1_references"]), 2)
            census = json.loads((root / "census/census_receipt.json").read_bytes())
            self.assertEqual(census["science_views"], report)
            with (root / "census/cell_status.csv").open(newline="", encoding="utf-8") as stream:
                cells = list(csv.DictReader(stream))
            presence = [row for row in cells if row["column"] == "有TREO資源估計"]
            self.assertEqual(len(presence), 93)
            present = [row for row in presence if row["value_state"] == "PRESENT"]
            self.assertEqual(len(present), 4)
            self.assertTrue(all(row["reason"] == "VERIFIED_TREO_RESOURCE_STATEMENT" and
                                row["value_origin"] == "EVIDENCE_REFERENCE" and
                                len(json.loads(row["input_observation_ids"])) == 3 for row in present))
            self.assertTrue(all(row["value_state"] == "UNKNOWN" for row in presence if row not in present))
            audited = ResearchBuild.load(root)
            evidence_cell = present[0]
            identifiers = json.loads(evidence_cell["input_observation_ids"])
            invalid = {**evidence_cell, "input_observation_ids": json.dumps([identifiers[0]] * 3)}
            definition = load_projection_policy(COLUMNS).by_name["有TREO資源估計"]
            with self.assertRaisesRegex(ValueError, "requires three distinct selected observations"):
                validate_resource_presence(invalid, definition, census, audited.observations, audited.census)
            by_key = {(row["project_id"], row["key"]): Decimal(row["value"]) for row in report["derived_candidates"]}
            self.assertEqual(by_key, {
                ("AU-0098e17a63e7c05c213f", "mreo_kt"): Decimal("16.8"),
                ("AU-9bd8e5ed8408cd84d876", "Dy2O3_kt"): Decimal("6.132"),
                ("AU-9bd8e5ed8408cd84d876", "Nd2O3_kt"): Decimal("2.044"),
                ("AU-9bd8e5ed8408cd84d876", "Pr6O11_kt"): Decimal("0.438"),
                ("AU-9bd8e5ed8408cd84d876", "Tb4O7_kt"): Decimal("0.876"),
                ("AU-9bd8e5ed8408cd84d876", "ndpr_kt"): Decimal("2.482"),
                ("AU-9bd8e5ed8408cd84d876", "magnet_recalculated_kt"): Decimal("9.49"),
                ("AU-9bd8e5ed8408cd84d876", "mreo_kt"): Decimal("9.49"),
            })

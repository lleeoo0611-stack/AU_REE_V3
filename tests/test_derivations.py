"""Exercise Census derivation authority and hard scientific boundaries with synthetic rows."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from au_ree_v3.comparability import TOTAL_REO_15
from au_ree_v3.contracts import ContractViolation, DerivedValue
from au_ree_v3.derivations import (
    FORMULA_REGISTRY_SHA256, RELEASE_REGISTRY_PIN, assess_census_pair, checked_census_derivations,
    persist_census_treo, verify_derivation_assessment,
)
from au_ree_v3.observations import digest
from au_ree_v3.research_inputs import CENSUS_RESOURCE_RULE, CENSUS_RESOURCE_USE, checked_research_inputs
from au_ree_v3.store import Store
from test_store import (
    ACCEPTED, ANCHOR, CANDIDATE, CONTENT, DEPOSIT, PROJECT, RAW, RELEASE, SCOPE, SOURCE,
    TEMPORAL_EVIDENCE, VALIDATION, evidence_decision,
)


def _seed(store, *, pin=True, direct_contained=False):
    with store.transaction():
        store.add_source(SOURCE, CONTENT)
        for entity in (PROJECT, DEPOSIT, SCOPE):
            store.add(entity)
        entries = []
        data = (("ore_mt", "20.0", "Mt", ["19.95", "20.05"], 20),
                ("grade", "2.0", "%", ["1.95", "2.05"], 36))
        for variable, token, unit, interval, start in data:
            anchor = replace(ANCHOR, anchor_id=variable + "-anchor", column=variable, quote="Total 20.0 2.0")
            payload = {
                "reporting_standard": "JORC 2012", "resource_class": "MINERAL_RESOURCE",
                "resource_category": "INDICATED_INFERRED_TOTAL", "cutoff_grade": "1", "cutoff_unit": "%",
                "cutoff_analyte": "TREO", "treo_members": sorted(TOTAL_REO_15),
                "research_use": CENSUS_RESOURCE_USE, "extraction_strategy": "native-resource-table-v1",
                "statement_context": [{"physical_page": 1, "quote": "Resource tonnages are reported on a dry basis."}],
                "native_table_binding": {
                    "value_anchor": {"text": token, "physical_page": 1, "line": 3,
                                     "start_column": start, "end_column": start + len(token) - 1},
                    "header_anchor": {"physical_page": 1}, "unit_anchor": {"physical_page": 1},
                    "caption_anchors": [{"physical_page": 1}], "raw_rounding_interval": interval,
                },
            }
            raw = replace(RAW, observation_id=variable + "-observation", native_raw_id=variable + "-record",
                          anchor_id=anchor.anchor_id, category=payload["resource_category"], variable=variable,
                          raw_value=token, raw_unit=unit, material_basis="WHOLE_ORE", chemical_basis="TREO",
                          denominator_basis="WHOLE_ORE" if variable == "grade" else None, original_payload=payload)
            candidate = replace(CANDIDATE, candidate_id=variable + "-candidate", observation_id=raw.observation_id,
                                raw_record_id=raw.raw_record_id, value=token, unit=unit, material_basis=raw.material_basis,
                                chemical_basis=raw.chemical_basis, denominator_basis=raw.denominator_basis)
            decision = evidence_decision(raw)
            validation = replace(VALIDATION, validation_id=variable + "-validation", candidate_id=candidate.candidate_id,
                                 evidence_anchor_ids=(anchor.anchor_id,), temporal_decision_id=decision.decision_id)
            for record in (anchor, raw, candidate, validation):
                store.add(record)
            entries.append((variable, validation))
        if direct_contained:
            store.add(replace(raw, observation_id="direct-reported-observation", native_raw_id="direct-reported-raw",
                              variable="contained_treo_kt", raw_value="399.9", raw_unit="kt TREO"))
        fingerprints = {"admission_plan": "f" * 64}
        if pin:
            fingerprints[RELEASE_REGISTRY_PIN] = FORMULA_REGISTRY_SHA256
        release = replace(RELEASE, release_mode="FORMAL", validation_status="PASS", result_fingerprints=fingerprints,
                          temporal_decision_ids=tuple(sorted({entry.temporal_decision_id for _,entry in entries})),
                          temporal_evidence_ids=(TEMPORAL_EVIDENCE.evidence_id,))
        store.add(release)
        for variable, validation in entries:
            store.add(replace(ACCEPTED, acceptance_id=variable + "-accepted", validation_id=validation.validation_id,
                              selection_key=f"{PROJECT.entity_id}:{variable}", reason=CENSUS_RESOURCE_RULE))
    return release


def _item_payload(item, *, drop=(), **edits):
    payload = json.loads(item.raw.original_payload)
    for key in drop:
        del payload[key]
    payload.update(edits)
    return replace(item, raw=replace(item.raw, original_payload=payload))


class DerivationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = self.enterContext(Store(":memory:", trusted_temporal_evidence_ids=(TEMPORAL_EVIDENCE.evidence_id,)))

    def test_registered_percent_formula_and_idempotent_scoped_store(self):
        release = _seed(self.store)
        with self.store.transaction():
            first, = persist_census_treo(self.store, release)
        self.assertEqual((first.value, first.rounding_interval), ("400", ("389.025", "411.025")))
        self.assertEqual(first.formula_id, "contained-treo-from-mt-pct-v1")
        self.assertTrue(verify_derivation_assessment(first.to_dict()))
        with self.store.transaction():
            self.assertEqual(persist_census_treo(self.store, release), (first,))
        value, = checked_census_derivations(self.store, release)
        self.assertEqual(value.input_observation_ids, ("ore_mt-record", "grade-record"))
        self.assertEqual(self.store.derivation_input_keys(value.derived_id), value.input_observation_ids)
        self.assertEqual(self.store.integrity(), {"integrity_check": ["ok"], "foreign_key_check": []})
        self.assertEqual(checked_research_inputs(self.store, release, purpose="MCDA"), ())
        self.assertEqual(len(checked_research_inputs(self.store, release, purpose="CENSUS")), 2)

    def test_older_release_without_registry_pin_keeps_read_only_behavior(self):
        release = _seed(self.store, pin=False)
        with self.assertRaisesRegex(ContractViolation, "pin"), self.store.transaction():
            persist_census_treo(self.store, release)
        self.assertEqual(self.store.records(DerivedValue), [])
        self.assertEqual(checked_census_derivations(self.store, release), ())
        self.assertEqual(len(checked_research_inputs(self.store, release, purpose="CENSUS")), 2)

    def test_original_contained_report_takes_precedence_even_if_unselected(self):
        release = _seed(self.store, direct_contained=True)
        with self.store.transaction():
            result, = persist_census_treo(self.store, release)
        self.assertEqual(result.reason, "REPORTED_CONTAINED_TREO_TAKES_PRECEDENCE")
        self.assertEqual(self.store.records(DerivedValue), [])

    def test_formal_write_recomputes_and_rolls_back_forged_values(self):
        release = _seed(self.store)
        with self.assertRaisesRegex(ContractViolation, "exact approved"), self.store.transaction():
            persist_census_treo(self.store, release)
            original, = self.store.derivations_for_release(release.release_id)
            self.store.add(replace(original, derived_id="forged", value="123"))
        self.assertEqual(self.store.records(DerivedValue), [])
        with self.store.transaction():
            persist_census_treo(self.store, release)
        original, = self.store.derivations_for_release(release.release_id)
        for name, change in {
            "unassessed": {"assessment": None}, "unregistered": {"formula": "grade²"},
            "raw-edition": {"input_observation_ids": ("ore_mt-observation", "grade-record")},
            "factor": {"factor": "100"}, "scope": {"scope_id": "other-scope"},
        }.items():
            with self.subTest(name=name), self.assertRaises(ContractViolation), self.store.transaction():
                self.store.add(replace(original, derived_id=name, **change))
        self.assertEqual(checked_census_derivations(self.store, release), (original,))

    def test_static_verifier_catches_even_resigned_point_interval_policy_and_input_forgery(self):
        release = _seed(self.store)
        with self.store.transaction():
            original, = persist_census_treo(self.store, release)
        for key, value in {"value":"401", "rounding_interval":["389","412"], "factor":"0.001",
                           "purpose":"MCDA", "formula_sha256":"c"*64}.items():
            with self.subTest(key=key):
                tampered = original.to_dict()
                del tampered["assessment_id"]
                tampered[key] = value
                tampered["assessment_id"] = digest(tampered)
                self.assertFalse(verify_derivation_assessment(tampered))

    def test_scientific_scope_and_precision_counterexamples(self):
        release = _seed(self.store)
        selected = {item.raw.variable:item for item in checked_research_inputs(self.store, release, purpose="CENSUS")}
        ore, grade = (selected[name] for name in ("ore_mt", "grade"))
        payload = json.loads(grade.raw.original_payload)
        cases = {
            "ore reserve": _item_payload(grade, resource_class="ORE_RESERVE"),
            "treo+sc": replace(grade, raw=replace(grade.raw, chemical_basis="TREO+Sc2O3")),
            "treo-ce": _item_payload(grade, cutoff_analyte="TREO-CeO2"),
            "heavy minerals": replace(grade, raw=replace(grade.raw, denominator_basis="HEAVY_MINERALS")),
            "mixed grade": replace(grade, raw=replace(grade.raw, material_basis="CONCENTRATE")),
            "limited chemistry": _item_payload(grade, treo_members=sorted(TOTAL_REO_15)[:-1]),
            "year": replace(grade, raw=replace(grade.raw, effective_date="2025-07")),
            "cutoff": _item_payload(grade, cutoff_grade="1.2"),
            "project": replace(grade, raw=replace(grade.raw, project_id="other-pid")),
            "deposit": replace(grade, raw=replace(grade.raw, deposit_id="other-id")),
            "scope": replace(grade, raw=replace(grade.raw, scope_id="other-scope")),
            "statement": replace(grade, raw=replace(grade.raw, statement_id="wrong-statement")),
            "source edition": replace(grade, raw=replace(grade.raw, source_sha="c" * 64)),
            "reported conflict": replace(grade, validation=replace(grade.validation, outcome="SCIENTIFIC_CONFLICT", comparable=False)),
            "unproved moisture": _item_payload(grade, statement_context=[{"physical_page":1,"quote":"Tonnages are wet."}]),
            "wrong normalized quantity": replace(grade, candidate=replace(grade.candidate, value="20")),
        }
        for name, edits in {"altered interval":{"raw_rounding_interval":["1","3"]},
                            "unproved publisher precision":{"reported_significant_figures":1},
                            "cross row":{"value_anchor":{**payload["native_table_binding"]["value_anchor"],"line":4}}}.items():
            native = deepcopy(payload["native_table_binding"])
            native.update(edits)
            cases[name] = _item_payload(grade, native_table_binding=native)
        for name, variant in cases.items():
            with self.subTest(name=name):
                self.assertNotEqual(assess_census_pair(ore, variant, release).status, "APPROVED")
        self.assertEqual(assess_census_pair(ore, grade, release).status, "APPROVED")

"""Project approved Census formulas and verify their serialized lineage."""

from decimal import Decimal
import json

from .census import COLUMNS
from .contracts import DerivedValue, canonical_json
from .derivations import checked_census_derivations, registered_formula, verify_derivation_assessment

CONTAINED_COLUMN = COLUMNS["contained_treo_kt"]


def _precision(assessment):
    return {
        "mode": "DERIVED_FROM_REPORTED_PRECISION",
        "unit": assessment["output_unit"],
        "rounding_interval": assessment["rounding_interval"],
    }


def _evidence(record, assessment):
    return {
        "derived_value_id": record.derived_id,
        "assessment_id": assessment["assessment_id"],
        "formula": record.formula,
        "formula_registry_sha256": assessment["formula_registry_sha256"],
        "evidence_type": assessment["purpose"],
    }


def governed_derived_projection(store, release, inputs):
    """Use exact release-approved values and retain the proof in the Census receipt."""
    selected = {row.raw.raw_record_id: row for row in inputs}
    cells, proofs = {}, []
    for record in checked_census_derivations(store, release):
        assessment = record.assessment
        grade = selected[record.input_observation_ids[1]]
        trace, anchor = grade.temporal_trace, grade.anchor
        cells[record.project_id, CONTAINED_COLUMN] = {
            "value": record.value, "unit": record.unit, "value_state": "PRESENT",
            "reason": assessment["purpose"], "value_origin": "FORMAL_DERIVED", "evidence_state": "FORMAL_DERIVED",
            "release_id": record.release_id, "source_key": grade.source.source_key,
            "source_sha256": grade.source.sha256, "source_url": grade.source.url,
            "anchor_id": anchor.anchor_id, "locator": anchor.locator, "physical_page": anchor.page,
            "source_table": anchor.table, "source_row": anchor.row, "source_column": anchor.column,
            "scope_id": record.scope_id, "scope": grade.scope.name, "statement_id": grade.raw.statement_id,
            "effective_date": grade.raw.effective_date, "category": grade.raw.category,
            "material_basis": record.material_basis, "chemical_basis": record.chemical_basis,
            "denominator_basis": record.denominator_basis, "comparable": "NO",
            "temporal_policy_version": trace["policy_version"], "temporal_status": trace["status"],
            "temporal_decision_id": trace["decision_id"],
            "temporal_evidence_ids": canonical_json(trace["matching_evidence_ids"]),
            "rules_sha256": release.rules_sha, "formula_id": assessment["formula_id"],
            "formula_sha256": assessment["formula_sha256"], "precision": canonical_json(_precision(assessment)),
            "input_observation_ids": canonical_json([row["observation_id"] for row in assessment["inputs"]]),
            "metadata_evidence": canonical_json(_evidence(record, assessment)),
        }
        proofs.append(record.to_dict())
    return cells, proofs


def _match(cell, pairs):
    return all(cell.get(key) == value for key, value in pairs.items())


def _check_input(proof, assessment, selected, ledger):
    source_fields = {
        "source_sha256": assessment["source_sha256"], "scope_id": assessment["scope_id"],
        "statement_id": assessment["statement_id"], "effective_date": assessment["effective_date"],
    }
    source_value = selected.get(proof["observation_id"], {})
    row = ledger[assessment["project_id"], COLUMNS[proof["variable"]]]
    if not _match(source_value, {
        **source_fields, "project_id": assessment["project_id"], "variable": proof["variable"],
        "raw_value": proof["raw_token"], "raw_unit": proof["raw_unit"],
        "publication_date": assessment["publication_date"],
    }) or not _match(row, {
        **source_fields, "release_id": assessment["release_id"], "value_origin": "FORMAL_SELECTION",
        "observation_id": proof["observation_id"], "raw_record_id": proof["raw_record_id"],
        "selection_id": proof["selection_id"], "validation_id": proof["validation_id"],
        "candidate_id": proof["candidate_id"], "anchor_id": proof["anchor_id"],
        "temporal_decision_id": proof["temporal_decision_id"], "temporal_status": "ELIGIBLE",
        "material_basis": "WHOLE_ORE", "chemical_basis": "TREO",
    }):
        raise ValueError("Census derivation has an unselected, unverified or differently scoped input")
    precision = json.loads(row["precision"])
    if list(map(Decimal, precision["rounding_interval"])) != list(map(Decimal, proof["raw_rounding_interval"])):
        raise ValueError("Census derivation input precision differs from its approved cell")


def verify_census_derivation(cell, document, selected, ledger, release_id):
    """Check the archived candidate using the formula registry and the direct evidence ledger."""
    record = DerivedValue.from_dict(document)
    proof = record.assessment
    if not verify_derivation_assessment(proof):
        raise ValueError("Census formula, precision or derivation assessment fingerprint differs")
    formula = registered_formula(proof["formula_id"])
    inputs = proof["inputs"]
    grade = inputs[1]
    if not _match(cell, {
        "project_id": record.project_id, "column": CONTAINED_COLUMN, "release_id": release_id,
        "scope_id": record.scope_id, "source_sha256": proof["source_sha256"],
        "statement_id": proof["statement_id"], "source_key": proof["source_key"], "source_url": proof["source_url"],
        "effective_date": proof["effective_date"], "category": proof["resource_category"],
        "value": record.value, "unit": record.unit, "reason": proof["purpose"],
        "anchor_id": grade["anchor_id"], "temporal_status": "ELIGIBLE",
        "temporal_decision_id": grade["temporal_decision_id"],
        "formula_id": proof["formula_id"], "formula_sha256": proof["formula_sha256"],
        "material_basis": record.material_basis, "chemical_basis": record.chemical_basis,
        "denominator_basis": record.denominator_basis, "observation_id": "", "raw_value": "", "raw_unit": "",
    }) or proof["release_id"] != record.release_id or record.release_id != release_id:
        raise ValueError("Census derivation differs from its release-scoped record")
    if (
        record.input_observation_ids != tuple(item["raw_record_id"] for item in inputs)
        or record.derived_id != proof["assessment_id"] or record.formula != formula.expression
        or json.loads(cell["input_observation_ids"]) != [item["observation_id"] for item in inputs]
        or json.loads(cell["precision"]) != _precision(proof)
        or json.loads(cell["metadata_evidence"]) != _evidence(record, proof)
        or record.value != proof["value"] or record.unit != proof["output_unit"]
        or record.variable != proof["output_variable"] or record.factor != proof["factor"]
    ):
        raise ValueError("Census derived output or stored input links differ from their assessment")
    for entry in inputs:
        _check_input(entry, proof, selected, ledger)

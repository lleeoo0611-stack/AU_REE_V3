"""Authorize a separately pinned mass calculation from a reported MREO aggregate."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .comparability import MREO_BASIS, MREO_VARIABLE, normalize_input, resource_context, verify_reported_mreo_units
from .contracts import ContractViolation, DerivedValue, ResearchRelease, canonical_json
from .derivations import _calculate, _decimal_text, _measure
from .observations import digest
from .research_inputs import RESOURCE_RULE, ResearchInput, checked_research_inputs

if TYPE_CHECKING:
    from .documents import PDFDocument
    from .store import Store

OXIDE_POLICY_VERSION = "approved-census-reported-mreo-derivation-v1"
OXIDE_ASSESSMENT_VERSION = "census-oxide-derivation-assessment-v1"
OXIDE_PURPOSE = "CENSUS_FORMAL_REPORTED_MREO_MASS"
OXIDE_RELEASE_PIN = "census_oxide_derivation_registry"
OXIDE_VARIABLE = "mreo_kt"
MEMBERS = ("Dy2O3", "Nd2O3", "Pr6O11", "Tb4O7")
INPUT_VARIABLES = ("ore_mt", "grade", MREO_VARIABLE)
FORMULA = {
    "id": "contained-reported-mreo-mt-ppm-v1", "purpose": OXIDE_PURPOSE,
    "expression": "ore_mt × reported_mreo_ppm × 0.001", "factor": "0.001", "output_unit": "kt",
    "input_role": "REPORTED_NAMED_GROUP", "oxide_species": list(MEMBERS), "output_variable": OXIDE_VARIABLE,
}
FORMULA_SHA256 = digest(FORMULA)

# This edition-specific evidence contract is derived from the reviewed native PDF and
# its embedded combined-estimate image. It is not a parser or a portable heuristic.
REVIEW = {
    "schema": "reported-mreo-original-scope-review-v1",
    "source_sha256": "89467aedb62238c6a98902633fba1bcaf4ccd0f081beaa736facf71533ac3864",
    "source_key": "seed-f09f74b43a7e20826fb9eda9",
    "project_id": "AU-0098e17a63e7c05c213f",
    "scope": "Wudinna REE Project Mineral Resource, Garford and Pidinga total",
    "scope_id": "49a12551476a4937c90dc2b988e143da0ab0edd11768a1938395ed50cc7903d3",
    "statement_id": "763a4d2d39f3afed5792b22f9bcf99f7dd22e60c5138e9259baaced2756087f0",
    "effective_date": "2026-10-22", "category": "INDICATED_INFERRED_TOTAL",
    "cutoff": ["200", "ppm", "TREO-CeO2"],
    "scope_filters": [
        {"role": "OPEN_PIT_RESOURCE_CUTOFF", "physical_page": 1,
         "quote": "Open pittable cutoff grade of 200 ppm TREO excluding cerium (\"TREO-Ce\")."},
        {"role": "PIDINGA_ISR_ECONOMIC_CUTOVER", "physical_page": 1,
         "quote": "For mineralisation within the Pidinga formation amenable to ISR, an economic cut-over of US$ 5.27/tonne has been applied."},
    ],
    "dry_basis": {"physical_page": 12, "quote": "density methodology was selected to ensure that the values applied represent dry in-situ tonnes for Mineral Resource reporting."},
    "table": "Table 1 Wudinna REE Project Mineral Resource Estimate at cut-off grade of 200 ppm TREO-Ce",
    "physical_page": 3, "row": "Total", "row_quote": "Total 150 470 101 10 112",
    "definition": {"physical_page": 3, "quote": "MREO = Magnet Rare Earth Oxide(Nd2O3, Pr6O11, Dy2O3, Tb4O7)"},
    "companion": {
        "physical_page": 15, "image_slot": 2,
        "image_sha256": "f776a023a5a7f96c8ef90aeb52ce3a05427b609603a01064d4e100bd440447f0",
        "locator": "Combined Estimate > Total > Indicated + Inferred > ISR & OP",
        "ore_token_mt": "149.92", "grade_token_ppm": "470", "nd_pr_token_ppm": "101", "dy_tb_token_ppm": "10.5",
        "grade_unit": "ppm", "group_unit": "ppm",
    },
    "selected_inputs": [
        {"variable": "ore_mt", "column": "Mt", "token": "150", "unit": "Mt",
         "observation_id": "41dc54bc8c976886f628d7c191822f037c259e0a055bfaf7703198b70e078da0"},
        {"variable": "grade", "column": "TREO", "token": "470", "unit": "ppm",
         "observation_id": "3006c076a1c7abfebf3d00dfd9aae2ca526790db3a5cccda1a1ee79ab2a3984d"},
        {"variable": "mreo_ppm", "column": "MREO", "token": "112", "unit": "ppm",
         "observation_id": "2de14f251ec4892688a0b52ebf490cbf24666a586dc2c73941a35f6c4d7a46c8"},
    ],
}
SOURCE_REVIEW_SHA256 = digest(REVIEW)
OXIDE_FORMULA_REGISTRY_SHA256 = digest({
    "version": OXIDE_POLICY_VERSION, "formula_sha256": FORMULA_SHA256,
    "source_review_sha256": SOURCE_REVIEW_SHA256, "input_selection_rule": RESOURCE_RULE,
    "rounding_policy": "independently-reported-last-digit-v1", "cross_source_join": False,
    "aggregate_evidence": "original-scope-reported-mreo-v1",
})

_POLICY = {"schema_version": OXIDE_POLICY_VERSION, "formula_registry_sha256": OXIDE_FORMULA_REGISTRY_SHA256,
           "formula_id": FORMULA["id"], "source_review_sha256": SOURCE_REVIEW_SHA256}


def registered_oxide_formula(formula_id: str) -> dict | None:
    return json.loads(canonical_json({**FORMULA, "sha256": FORMULA_SHA256})) if formula_id == FORMULA["id"] else None


def load_oxide_derivation_policy(path: Path | str) -> dict:
    policy = json.loads(Path(path).read_bytes())
    if policy != _POLICY:
        raise ValueError("oxide derivation policy does not match the registered formula and review")
    return policy


def verify_reviewed_mreo_document(document: PDFDocument) -> bool:
    """Read back the approved dry basis and native context from the existing PDF view."""
    if document.source_sha256 != REVIEW["source_sha256"]:
        return False
    anchors = [REVIEW["definition"], REVIEW["dry_basis"], *REVIEW["scope_filters"],
               {"physical_page": REVIEW["physical_page"], "quote": REVIEW["row_quote"]},
               {"physical_page": REVIEW["physical_page"], "quote": REVIEW["table"]}]
    return all(1 <= item["physical_page"] <= len(document.pages)
               and " ".join(document.pages[item["physical_page"] - 1].split()).count(item["quote"]) == 1
               for item in anchors)


def _matches(actual: dict, expected: dict) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def _input(item: ResearchInput, check: dict, release: ResearchRelease) -> dict:
    raw, anchor = item.raw, item.anchor
    payload = json.loads(raw.original_payload)
    actual = {
        "observation_id": raw.observation_id, "variable": raw.variable, "column": anchor.column,
        "token": raw.raw_value, "unit": raw.raw_unit,
    }
    context = {
        "project_id": raw.project_id, "scope_id": raw.scope_id, "scope": item.scope.name,
        "statement_id": raw.statement_id, "source_sha256": raw.source_sha, "source_key": raw.source_key,
        "effective_date": raw.effective_date, "category": raw.category, "physical_page": anchor.page,
        "table": anchor.table, "row": anchor.row, "row_quote": " ".join((anchor.quote or "").split()),
        "cutoff": [payload.get("cutoff_grade"), payload.get("cutoff_unit"), payload.get("cutoff_analyte")],
    }
    if actual != check or not _matches(REVIEW, context):
        raise ValueError("REVIEWED_SOURCE_SCOPE_EDITION_OR_NATIVE_CELL_MISMATCH")
    if (item.accepted.reason != RESOURCE_RULE or item.accepted.release_id != release.release_id
            or raw.status != "REPORTED" or raw.observation_role != "HISTORICAL_MEASUREMENT"
            or payload.get("reporting_standard") != "JORC 2012" or raw.material_basis != "WHOLE_ORE"
            or item.candidate.material_basis != "WHOLE_ORE" or item.temporal_trace.get("status") != "ELIGIBLE"
            or item.temporal_trace.get("decision_id") not in release.temporal_decision_ids
            or (raw.source_key, raw.source_sha) != (item.source.source_key, item.source.sha256)
            or (raw.source_key, raw.source_sha) != (anchor.source_key, anchor.source_sha)
            or raw.reported_publication_date != item.source.publication_date
            or item.validation.outcome != "VALIDATED" or raw.anchor_id != anchor.anchor_id):
        raise ValueError("APPROVED_EXACT_HISTORICAL_WHOLE_ORE_INPUT_REQUIRED")
    if raw.variable != "ore_mt" and (raw.denominator_basis, item.candidate.denominator_basis) != ("WHOLE_ORE", "WHOLE_ORE"):
        raise ValueError("GRADE_DENOMINATOR_MUST_BE_WHOLE_ORE")
    normalized = normalize_input(item, check["unit"])
    if not normalized.verify() or normalized.status != "NORMALIZED" or (
        Decimal(normalized.value) != Decimal(item.candidate.value) or item.candidate.unit != check["unit"]
    ):
        raise ValueError("NORMALIZED_ORIGINAL_OXIDE_TOKEN_MISMATCH")
    numbers = _measure(check["token"], check["unit"], check["unit"], None)
    if tuple(map(Decimal, normalized.rounding_interval)) != numbers[1:]:
        raise ValueError("ORIGINAL_OXIDE_ROUNDING_INTERVAL_MISMATCH")
    return {
        "variable": raw.variable, "observation_id": raw.observation_id, "raw_record_id": raw.raw_record_id,
        "selection_id": item.accepted.acceptance_id, "validation_id": item.validation.validation_id,
        "candidate_id": item.candidate.candidate_id, "anchor_id": anchor.anchor_id,
        "temporal_decision_id": item.temporal_trace["decision_id"], "physical_page": anchor.page,
        "table": anchor.table, "row": anchor.row, "column": anchor.column,
        "raw_token": raw.raw_value, "raw_unit": raw.raw_unit, "normalized_value": _decimal_text(numbers[0]),
        "normalized_unit": item.candidate.unit, "raw_rounding_interval": list(map(_decimal_text, numbers[1:])),
        "normalized_rounding_interval": list(map(_decimal_text, numbers[1:])), "original_payload_sha256": digest(payload),
    }


def _group_evidence(proof: dict) -> bool:
    _, grade, aggregate = REVIEW["selected_inputs"]
    definition = {"page": REVIEW["definition"]["physical_page"], "quote": REVIEW["definition"]["quote"]}
    companion = {**REVIEW["companion"], "version": "fixed-original-combined-oxide-table-review-v1",
                 **{key: REVIEW[key] for key in ("source_sha256", "scope", "effective_date", "cutoff")}}
    return _matches(proof, {
        "method": "original-scope-reported-mreo-v1", "reported_definition": definition,
        "reported_group_token": aggregate["token"], "reported_group_unit": "ppm",
        "treo_token": grade["token"], "treo_unit": grade["unit"], "oxide_species": list(MEMBERS),
        "input_observation_ids": [aggregate["observation_id"], grade["observation_id"]],
    }) and _matches(proof.get("reported_unit_evidence", {}), companion)


def _result(inputs: tuple[dict, ...]) -> tuple[str, list[str]]:
    ore, total, aggregate = (_measure(item["raw_token"], item["raw_unit"], item["normalized_unit"], None) for item in inputs)
    if ore[1] <= 0 or aggregate[1] <= 0 or aggregate[2] > total[1]:
        raise ValueError("REPORTED_MREO_MUST_FIT_WHOLE_ORE_TREO_BOUNDS")
    value, interval = _calculate(ore, aggregate, FORMULA["factor"])
    return value, list(interval)


def assess_reported_mreo(inputs: tuple[ResearchInput, ...], release: ResearchRelease) -> dict:
    """Assess the reported group for a pinned, independent Census-only use."""
    def refused(reason):
        return {"schema_version": OXIDE_ASSESSMENT_VERSION, "purpose": OXIDE_PURPOSE, "status": "EVIDENCE_INSUFFICIENT",
                "reason": reason, "release_id": release.release_id, "source_review_sha256": SOURCE_REVIEW_SHA256}

    if release.release_mode != "FORMAL" or release.validation_status != "PASS":
        return refused("FORMAL_OBSERVATION_RELEASE_REQUIRED")
    if (release.result_fingerprints or {}).get(OXIDE_RELEASE_PIN) != OXIDE_FORMULA_REGISTRY_SHA256:
        return refused("REPORTED_MREO_FORMULA_REGISTRY_UNPINNED")
    selected = [item for item in inputs if item.raw.project_id == REVIEW["project_id"]]
    if any(item.raw.variable == OXIDE_VARIABLE for item in selected):
        return refused("DIRECT_REPORTED_MREO_MASS_TAKES_PRECEDENCE")
    by_variable = {key: [item for item in selected if item.raw.variable == key] for key in INPUT_VARIABLES}
    if any(len(by_variable[key]) != 1 for key in INPUT_VARIABLES):
        return refused("EXACT_ORE_TREO_AND_REPORTED_MREO_SELECTIONS_REQUIRED")
    items = tuple(by_variable[key][0] for key in INPUT_VARIABLES)
    ore, grade, aggregate = items
    if any(resource_context(item) != resource_context(ore) for item in items):
        return refused("REPORTED_GROUP_SCOPE_OR_SOURCE_MISMATCH")
    if (aggregate.raw.chemical_basis != MREO_BASIS or aggregate.candidate.chemical_basis != MREO_BASIS
            or any(item.raw.chemical_basis != "TREO" for item in (ore, grade))):
        return refused("EXACT_REPORTED_MREO_QUARTET_ROLE_REQUIRED")
    payload = json.loads(aggregate.raw.original_payload)
    species = payload.get("oxide_species")
    if not isinstance(species, list) or not all(isinstance(item, str) for item in species):
        return refused("REPORTED_MREO_DEFINITION_OR_COMPANION_EVIDENCE_UNBOUND")
    units = verify_reported_mreo_units(aggregate, ore, grade, Decimal(REVIEW["selected_inputs"][1]["token"]))
    group_evidence = {
        "method": "original-scope-reported-mreo-v1",
        "reported_definition": payload.get("oxide_definition"),
        "reported_group_token": aggregate.raw.raw_value, "reported_group_unit": aggregate.raw.raw_unit,
        "treo_token": grade.raw.raw_value, "treo_unit": grade.raw.raw_unit,
        "input_observation_ids": [aggregate.raw.observation_id, grade.raw.observation_id],
        "oxide_species": sorted(species), "reported_unit_evidence": units or {},
    }
    if not _group_evidence(group_evidence):
        return refused("REPORTED_MREO_DEFINITION_OR_COMPANION_EVIDENCE_UNBOUND")
    try:
        entries = tuple(_input(item, check, release) for item, check in zip(items, REVIEW["selected_inputs"], strict=True))
        value, interval = _result(entries)
    except (ValueError, ArithmeticError) as error:
        return refused(str(error))
    proof = {
        "schema_version": OXIDE_ASSESSMENT_VERSION, "status": "APPROVED", "reason": "EXACT_REPORTED_MREO_MASS",
        "purpose": OXIDE_PURPOSE, "policy_version": OXIDE_POLICY_VERSION, "formula_id": FORMULA["id"],
        "formula_sha256": FORMULA_SHA256, "formula_registry_sha256": OXIDE_FORMULA_REGISTRY_SHA256,
        "source_review_sha256": SOURCE_REVIEW_SHA256,
        "release_id": release.release_id, "project_id": ore.raw.project_id, "scope_id": ore.scope.scope_id,
        "statement_id": ore.raw.statement_id, "source_sha256": ore.source.sha256, "source_key": ore.source.source_key,
        "source_url": ore.source.url, "effective_date": ore.raw.effective_date,
        "publication_date": ore.raw.reported_publication_date, "resource_category": ore.raw.category,
        "research_cutoff": release.cutoff, "cutoff": REVIEW["cutoff"], "oxide_species": list(MEMBERS),
        "scope_filters": REVIEW["scope_filters"],
        "result_basis": "REPORTED_NAMED_GROUP", "value": value, "rounding_interval": interval,
        "output_variable": OXIDE_VARIABLE, "output_unit": FORMULA["output_unit"], "factor": FORMULA["factor"],
        "inputs": list(entries), "dry_basis_review": REVIEW["dry_basis"], "group_evidence": group_evidence,
    }
    return json.loads(canonical_json({**proof, "assessment_id": digest(proof)}))


def verify_oxide_derivation_assessment(proof: dict) -> bool:
    """Verify registered scope, group definition and recomputed Decimal output."""
    try:
        expected = {"schema_version": OXIDE_ASSESSMENT_VERSION, "status": "APPROVED", "purpose": OXIDE_PURPOSE,
                    "policy_version": OXIDE_POLICY_VERSION, "formula_id": FORMULA["id"], "formula_sha256": FORMULA_SHA256,
                    "formula_registry_sha256": OXIDE_FORMULA_REGISTRY_SHA256, "source_review_sha256": SOURCE_REVIEW_SHA256,
                    "factor": FORMULA["factor"], "oxide_species": list(MEMBERS), "result_basis": "REPORTED_NAMED_GROUP",
                    "output_variable": OXIDE_VARIABLE, "output_unit": FORMULA["output_unit"],
                    "resource_category": REVIEW["category"], "dry_basis_review": REVIEW["dry_basis"],
                    "scope_filters": REVIEW["scope_filters"],
                    **{key: REVIEW[key] for key in ("project_id", "scope_id", "statement_id", "source_sha256", "source_key", "effective_date", "cutoff")}}
        if not _matches(proof, expected) or not _group_evidence(proof["group_evidence"]):
            return False
        if proof["assessment_id"] != digest({key:value for key, value in proof.items() if key != "assessment_id"}):
            return False
        if len(proof["inputs"]) != len(REVIEW["selected_inputs"]):
            return False
        for item, check in zip(proof["inputs"], REVIEW["selected_inputs"], strict=True):
            numbers = _measure(check["token"], check["unit"], check["unit"], None)
            if not _matches(item, {"observation_id":check["observation_id"], "variable":check["variable"],
                                   "column":check["column"], "raw_token":check["token"], "raw_unit":check["unit"],
                                   "normalized_unit":check["unit"], "normalized_value":_decimal_text(numbers[0]),
                                   "physical_page":REVIEW["physical_page"], "table":REVIEW["table"], "row":REVIEW["row"],
                                   "raw_rounding_interval":list(map(_decimal_text, numbers[1:])),
                                   "normalized_rounding_interval":list(map(_decimal_text, numbers[1:]))}):
                return False
        return _result(tuple(proof["inputs"])) == (proof["value"], proof["rounding_interval"])
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
        return False


def _approved_record(store: Store, release: ResearchRelease) -> DerivedValue | None:
    proof = assess_reported_mreo(checked_research_inputs(store, release, purpose="CENSUS"), release)
    if proof["status"] != "APPROVED":
        return None
    return DerivedValue(derived_id=proof["assessment_id"], release_id=release.release_id, project_id=proof["project_id"],
                        scope_id=proof["scope_id"], variable=OXIDE_VARIABLE, value=proof["value"], unit=proof["output_unit"],
                        formula=FORMULA["expression"], transform_version=OXIDE_POLICY_VERSION, factor=FORMULA["factor"],
                        denominator_basis="ABSOLUTE_RESOURCE_MASS", material_basis="WHOLE_ORE", chemical_basis=MREO_BASIS,
                        input_observation_ids=tuple(item["raw_record_id"] for item in proof["inputs"]), assessment=proof)


def persist_census_oxide(store: Store, release: ResearchRelease) -> tuple[DerivedValue, ...]:
    """Persist the independently approved reported-aggregate formula in an existing transaction."""
    approved = _approved_record(store, release)
    if approved is None:
        return ()
    store.add(approved)
    return (approved,)


def validate_census_oxide_derivation(store: Store, release: ResearchRelease, record: DerivedValue) -> None:
    expected = _approved_record(store, release)
    if expected is None or record.to_dict() != expected.to_dict():
        raise ContractViolation("oxide derivation lacks the exact approved formula, source and selection lineage")


def checked_census_oxide_derivations(store: Store, release: ResearchRelease) -> tuple[DerivedValue, ...]:
    """Revalidate formula, original evidence, release pin and exact raw FK links."""
    records = tuple(item for item in store.derivations_for_release(release.release_id)
                    if item.assessment is not None and item.variable == OXIDE_VARIABLE)
    if not records:
        return ()
    record, = records
    validate_census_oxide_derivation(store, release, record)
    if store.derivation_input_keys(record.derived_id) != record.input_observation_ids:
        raise ContractViolation("stored oxide derivation has different exact input keys")
    return records

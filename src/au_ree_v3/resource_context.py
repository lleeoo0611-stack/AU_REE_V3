"""Bind parsed resources to their original editions, scope, and observation evidence."""

from dataclasses import dataclass, replace
from decimal import Decimal
import json

from .contracts import (
    Entity, EvidenceAnchor, NormalizedObservation, RawObservation, ResourceScope, Source, ValidatedObservation,
)
from .contracts.temporal import TemporalDecision, TemporalEvidence, evaluate_temporal
from .documents import DocumentError
from .observations import digest
from .research_inputs import NATIVE_RESOURCE_CANDIDATE
from .resource_extraction import STRATEGIES, ResourceCell, ResourceStatement
from .resource_sources import CapturedSource, NATIVE_STRATEGY, describe_document_error


@dataclass(frozen=True)
class Measurement:
    raw: RawObservation
    candidate: NormalizedObservation
    anchor: EvidenceAnchor
    validation: ValidatedObservation

    def science(self, payload: dict | None = None) -> dict:
        if payload is None:
            payload = json.loads(self.raw.original_payload)
        row = {
            "observation_id": self.raw.observation_id, "project_id": self.raw.project_id,
            "scope_id": self.raw.scope_id, "statement_id": self.raw.statement_id,
            "source_sha256": self.raw.source_sha, "variable": self.raw.variable,
            "raw_value": self.raw.raw_value, "raw_unit": self.raw.raw_unit,
            "value": self.candidate.value, "unit": self.candidate.unit,
            "page": self.anchor.page, "table": self.anchor.table, "row": self.anchor.row,
            "column": self.anchor.column, "effective_date": self.raw.effective_date,
            "publication_date": self.raw.reported_publication_date,
            "material_basis": self.raw.material_basis, "chemical_basis": self.raw.chemical_basis,
            "category": self.raw.category, "cutoff_grade": payload["cutoff_grade"],
            "cutoff_unit": payload["cutoff_unit"], "cutoff_analyte": payload["cutoff_analyte"],
            "date_basis": payload["date_basis"], "method": payload["extraction_strategy"],
            "outcome": self.validation.outcome, "reason": self.validation.reason,
        }
        if review := payload.get("reviewed_oxide_table"):
            row.update(anchor_id=self.anchor.anchor_id, quote=self.anchor.quote,
                       denominator_basis=self.raw.denominator_basis, reviewed_oxide_table=review)
            if conflict := payload.get("same_statement_precision_conflict"):
                row["same_statement_precision_conflict"] = conflict
        return row


@dataclass(frozen=True)
class BoundStatement:
    project: Entity
    source: Source
    scope: ResourceScope
    statement_id: str
    statement: ResourceStatement
    decision: TemporalDecision
    measurements: tuple[Measurement, ...]
    research_use: str | None = None
    case_id: str | None = None


def _make_cell(
    cell: ResourceCell, statement: ResourceStatement, project: Entity, source: Source,
    scope: ResourceScope, statement_id: str, binding: dict, decision: TemporalDecision,
    proofs: tuple[TemporalEvidence, ...], rules_sha: str, reacquired_at: str | None,
) -> Measurement:
    identity = [source.sha256, statement_id, cell.variable, cell.page, cell.table, cell.row, cell.column, cell.token]
    observation_id = digest(["fresh-resource-observation-v1", identity])
    native = binding["strategy"] == NATIVE_STRATEGY
    exact_locator = ((cell.evidence_context or {}).get("native_table_binding") or {}).get("value_anchor", {})
    locator = f"physical PDF page {cell.page}; {cell.table}; {cell.row}; {cell.column}"
    if native and exact_locator.get("locator"):
        locator = f"{exact_locator['locator']}; {cell.table}; {cell.row}; {cell.column}"
    anchor = EvidenceAnchor(
        anchor_id=digest(["fresh-resource-anchor-v1", source.source_key, identity]),
        source_key=source.source_key, source_sha=source.sha256, page=cell.page,
        locator=locator,
        table=cell.table, row=cell.row, column=cell.column, quote=cell.quote,
        role="REPORTED_RESOURCE_VALUE",
    )
    material = cell.material_basis if native else "WHOLE_ORE"
    denominator = cell.denominator_basis if native else (
        "WHOLE_ORE" if cell.variable == "grade" or cell.variable.endswith("_ppm") else None
    )
    raw = RawObservation(
        observation_id=observation_id, id_version="fresh-resource-observation-v1",
        project_id=project.entity_id, scope_id=scope.scope_id,
        source_key=source.source_key, source_sha=source.sha256,
        variable=cell.variable, raw_value=cell.token, raw_unit=cell.raw_unit,
        material_basis=material, chemical_basis=cell.chemical_basis if native else cell.chemical_basis or "TREO",
        denominator_basis=denominator,
        statement_id=statement_id, category=statement.category,
        effective_date=statement.effective_date, reported_publication_date=source.publication_date,
        status="REPORTED", transform_version=binding["strategy"], anchor_id=anchor.anchor_id,
        original_payload={
            "reporting_standard": statement.reporting_standard if native else "JORC 2012",
            "resource_category": statement.category,
            "cutoff_grade": statement.cutoff, "cutoff_unit": statement.cutoff_unit, "cutoff_analyte": statement.cutoff_analyte,
            "date_basis": "EFFECTIVE_DATE_FROM_DOCUMENT" if statement.effective_date else "ISSUER_DISCLOSURE_WITH_EFFECTIVE_DATE_UNREPORTED",
            "statement_context": [{"physical_page": page, "quote": text} for page, text in statement.context],
            **(cell.evidence_context or {}),
            **({
                "research_use": binding.get("research_use", NATIVE_RESOURCE_CANDIDATE),
                "resource_class": statement.resource_class,
                "treo_members": statement.treo_members,
            } if native else {}),
            **({"reported_chemical_label": statement.reported_chemical_label}
               if statement.reported_chemical_label is not None else {}),
            "extraction_strategy": binding["strategy"],
            "normalization": {
                "formula": "reported_value * factor",
                "factor": cell.factor,
                "input_observation_ids": [observation_id],
            },
        },
    )
    candidate_id = digest(["fresh-resource-normalization-v1", observation_id, cell.value, cell.normalized_unit])
    candidate = NormalizedObservation(
        candidate_id=candidate_id, observation_id=observation_id,
        value=cell.value, unit=cell.normalized_unit, material_basis=raw.material_basis,
        chemical_basis=raw.chemical_basis, denominator_basis=raw.denominator_basis,
        transform_version="exact-reported-unit-conversion-v1",
    )
    outcome_by_date = {"ELIGIBLE": "VALIDATED", "CANDIDATE": "EVIDENCE_INSUFFICIENT",
                       "INELIGIBLE": "AFTER_CUTOFF", "CONFLICT": "SCIENTIFIC_CONFLICT"}
    if cell.conflict:
        outcome, reason = "SCIENTIFIC_CONFLICT", cell.conflict
    elif cell.evidence_issue:
        outcome, reason = "EVIDENCE_INSUFFICIENT", cell.evidence_issue
    else:
        outcome = outcome_by_date[decision.status]
        reason = "EXACT_SOURCE_AND_RESOURCE_EVIDENCE" if outcome == "VALIDATED" else decision.reason
    comparable = outcome == "VALIDATED"
    validation = ValidatedObservation(
        validation_id=digest(["fresh-resource-validation-v1", candidate_id, rules_sha, decision.decision_id, outcome, reason]),
        candidate_id=candidate_id, cutoff=decision.cutoff, rules_sha=rules_sha, outcome=outcome,
        reason=reason, comparable=comparable, evidence_anchor_ids=(anchor.anchor_id,),
        temporal_evidence=proofs, temporal_decision_id=decision.decision_id, reacquired_at=reacquired_at,
    )
    return Measurement(raw, candidate, anchor, validation)


def _bound_statement(
    statement: ResourceStatement, project: Entity, source: Source, binding: dict, proofs,
    cutoff: str, rules_sha: str, reacquired_at: str | None,
) -> BoundStatement:
    native = binding["strategy"] == NATIVE_STRATEGY
    scope_cutoff = format(Decimal(statement.cutoff).normalize(), "f") if statement.cutoff is not None else None
    if native:
        materials = {cell.material_basis for cell in statement.measurements}
        material = materials.pop() if len(materials) == 1 else None
    else:
        material = "WHOLE_ORE"
    scope_id = digest(["fresh-resource-scope-v1", project.entity_id, statement.scope, statement.category,
                       scope_cutoff, statement.cutoff_unit, statement.cutoff_analyte, material])
    scope = ResourceScope(scope_id=scope_id, project_id=project.entity_id, name=statement.scope)
    statement_id = digest(["fresh-resource-statement-v1", scope_id, source.sha256,
                           statement.effective_date, source.publication_date])
    decision = evaluate_temporal(
        source_sha256=source.sha256, cutoff=cutoff, publication_date=source.publication_date,
        acquired_at=source.acquired_at, reacquired_at=reacquired_at,
        effective_date=statement.effective_date, reported_publication_date=source.publication_date,
        evidence=proofs,
    )
    measurements = tuple(
        _make_cell(cell, statement, project, source, scope, statement_id, binding, decision, proofs, rules_sha, reacquired_at)
        for cell in statement.measurements
    )
    use = binding.get("research_use", NATIVE_RESOURCE_CANDIDATE) if native else None
    return BoundStatement(project, source, scope, statement_id, statement, decision, measurements, use, binding["case_id"])


def _with_image_chemistry(cell, evidence):
    context = cell.evidence_context
    native = context["native_table_binding"]
    issue = "TREO_CHEMICAL_DEFINITION_UNBOUND"
    remaining = [flag for flag in native["contextual_holds"] if flag != issue]
    chemistry = cell.chemical_basis
    if cell.variable == "ore_mt" and chemistry is None and cell.material_basis == "WHOLE_ORE":
        chemistry = "TREO"
    return replace(
        cell, chemical_basis=chemistry,
        evidence_issue=(remaining[0] if remaining else None) if cell.evidence_issue == issue else cell.evidence_issue,
        evidence_context={**context, "native_table_binding": {**native, "contextual_holds": remaining},
                          "treo_image_context": evidence},
    )


def _review_image(statement: ResourceStatement, image_review: dict, capture: CapturedSource, case: dict) -> ResourceStatement:
    if not statement.primary:
        return statement
    from .composition_review import verify_treo_image_context

    try:
        members, evidence = verify_treo_image_context(capture.pdf.pages, statement, image_review, capture.content)
    except DocumentError as error:
        case["composition_extraction"] = {"outcome": error.outcome, "reason": str(error)}
        field = "conflict" if error.outcome == "SCIENTIFIC_CONFLICT" else "evidence_issue"
        return replace(statement, measurements=tuple(
            replace(cell, **{field: str(error)}) for cell in statement.measurements
        ))
    case["composition_extraction"] = {"outcome": "VERIFIED_NATIVE_IMAGE_MEMBER_COLUMNS"}
    return replace(
        statement, treo_members=members,
        context=statement.context + tuple((image_review["physical_page"], image_review[key])
                                         for key in ("statement_quote", "definition_quote")),
        measurements=tuple(_with_image_chemistry(cell, evidence) for cell in statement.measurements),
    )


def _bind_composition(statement: ResourceStatement, binding: dict, capture: CapturedSource, case: dict) -> ResourceStatement:
    from .composition_extraction import classified_total_oxides
    from .composition_review import corroborated_aggregate

    try:
        components = classified_total_oxides(capture.pdf.pages, binding, statement, source_sha256=capture.source.sha256)
        aggregate = corroborated_aggregate(capture.pdf.pages, binding, statement, capture.content, capture.source.sha256)
    except DocumentError as error:
        case["composition_extraction"] = {"outcome": error.outcome, "reason": str(error)}
        return statement
    if components or aggregate:
        case["composition_extraction"] = {"outcome": "EXTRACTED", "count": len(components + aggregate)}
    return replace(statement, measurements=statement.measurements + components + aggregate)


def _comparability(decoded: tuple[ResourceStatement, ...]) -> str:
    if any(cell.conflict for statement in decoded for cell in statement.measurements):
        return "MIXED"
    if any(cell.evidence_issue for statement in decoded for cell in statement.measurements):
        return "INSUFFICIENT_EVIDENCE"
    return "COMPATIBLE"


def bind_context(
    binding: dict, project: Entity, capture: CapturedSource, case: dict, *, cutoff: str, rules_sha: str,
) -> list[BoundStatement]:
    """Bind one source case and record failures without invalidating independent cases."""
    source = capture.source
    try:
        decoded = STRATEGIES[binding["strategy"]](capture.pdf.pages, binding, source.publication_date)
    except DocumentError as error:
        describe_document_error(case, error)
        return []
    if binding["strategy"] == NATIVE_STRATEGY and (image_review := binding.get("treo_image_context")):
        decoded = tuple(_review_image(item, image_review, capture, case) for item in decoded)
    if binding.get("composition_table") or binding.get("oxide_aggregate_review"):
        decoded = tuple(_bind_composition(item, binding, capture, case) for item in decoded)
    bound = [
        _bound_statement(item, project, source, binding, capture.proofs, cutoff, rules_sha, capture.reacquired_at)
        for item in decoded
    ]
    case.update(
        outcome="EXTRACTED", reason="DECLARED_RESOURCE_STATEMENTS_BOUND",
        scientific_comparability=_comparability(decoded),
        scope_ids=[item.scope.scope_id for item in bound],
        observation_decisions=[item.decision.to_dict() for item in bound],
        observation_ids=[cell.raw.observation_id for item in bound for cell in item.measurements],
    )
    return bound

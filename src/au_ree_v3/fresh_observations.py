"""Extract and govern scoped observations from captured V3 source editions."""

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from hashlib import sha256
from itertools import chain
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import (
    AcceptedResearchValue, Entity, EvidenceAnchor, NormalizedObservation, RawObservation,
    ResearchRelease, ResourceScope, Source, ValidatedObservation, canonical_json,
)
from .contracts.temporal import TEMPORAL_POLICY_VERSION, TemporalDecision, TemporalEvidence, evaluate_temporal
from .documents import DocumentCache, DocumentError
from .observations import digest
from .research_inputs import checked_research_inputs
from .resource_extraction import STRATEGIES, ResourceCell, ResourceStatement
from .sources import ContentStore, IntegrityError, SCHEMA as ACQUISITION_SCHEMA, validate_edition_proofs
from .store import Store

SCHEMA = "fresh-resource-extraction-v1"
RULE = "approved-whole-ore-jorc-resource-v1"


@dataclass(frozen=True)
class Measurement:
    raw: RawObservation
    candidate: NormalizedObservation
    anchor: EvidenceAnchor
    validation: ValidatedObservation

    def science(self) -> dict:
        payload = json.loads(self.raw.original_payload)
        return {
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


@dataclass(frozen=True)
class BoundStatement:
    project: Entity
    source: Source
    scope: ResourceScope
    statement_id: str
    statement: ResourceStatement
    decision: TemporalDecision
    measurements: tuple[Measurement, ...]


def _project(binding: dict) -> Entity:
    record = Entity(kind="PROJECT", **binding["project"])
    if "AU-" + digest([record.native_namespace, record.native_id])[:20] != record.entity_id:
        raise ValueError("Reviewed binding Project ID disagrees with the native identity")
    return record


def _make_cell(
    cell: ResourceCell, statement: ResourceStatement, project: Entity, source: Source,
    scope: ResourceScope, statement_id: str, binding: dict, decision: TemporalDecision,
    proofs: tuple[TemporalEvidence, ...], rules_sha: str, reacquired_at: str | None,
) -> Measurement:
    identity = [source.sha256, statement_id, cell.variable, cell.page, cell.table, cell.row, cell.column, cell.token]
    observation_id = digest(["fresh-resource-observation-v1", identity])
    anchor = EvidenceAnchor(
        anchor_id=digest(["fresh-resource-anchor-v1", source.source_key, identity]),
        source_key=source.source_key, source_sha=source.sha256, page=cell.page,
        locator=f"physical PDF page {cell.page}; {cell.table}; {cell.row}; {cell.column}",
        table=cell.table, row=cell.row, column=cell.column, quote=cell.quote,
        role="REPORTED_RESOURCE_VALUE",
    )
    raw = RawObservation(
        observation_id=observation_id, id_version="fresh-resource-observation-v1",
        project_id=project.entity_id, scope_id=scope.scope_id,
        source_key=source.source_key, source_sha=source.sha256,
        variable=cell.variable, raw_value=cell.token, raw_unit=cell.raw_unit,
        material_basis="WHOLE_ORE", chemical_basis=cell.chemical_basis or "TREO",
        denominator_basis="WHOLE_ORE" if cell.variable == "grade" or cell.variable.endswith("_ppm") else None,
        statement_id=statement_id, category=statement.category,
        effective_date=statement.effective_date, reported_publication_date=source.publication_date,
        status="REPORTED", transform_version=binding["strategy"], anchor_id=anchor.anchor_id,
        original_payload={
            "reporting_standard": "JORC 2012", "resource_category": statement.category,
            "cutoff_grade": statement.cutoff, "cutoff_unit": statement.cutoff_unit, "cutoff_analyte": statement.cutoff_analyte,
            "date_basis": "EFFECTIVE_DATE_FROM_DOCUMENT" if statement.effective_date else "ISSUER_DISCLOSURE_WITH_EFFECTIVE_DATE_UNREPORTED",
            "statement_context": [{"physical_page": page, "quote": text} for page, text in statement.context],
            **(cell.evidence_context or {}),
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
    scope_id = digest(["fresh-resource-scope-v1", project.entity_id, statement.scope, statement.category,
                       format(Decimal(statement.cutoff).normalize(), "f"), statement.cutoff_unit,
                       statement.cutoff_analyte, "WHOLE_ORE"])
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
    return BoundStatement(project, source, scope, statement_id, statement, decision, measurements)


def _select(statements: list[BoundStatement]) -> tuple[dict[str, tuple[Measurement, ...]], dict[str, str]]:
    groups = defaultdict(list)
    selection = {}
    reasons = {}
    for item in statements:
        if item.statement.primary and item.decision.status == "ELIGIBLE":
            groups[item.project.entity_id].append(item)
    for project_id, choices in groups.items():
        if len({item.scope.scope_id for item in choices}) != 1:
            reasons[project_id] = "CONFLICTING_RESEARCH_SCOPES"
            continue
        if len(choices) > 1 and any(item.statement.effective_date is None for item in choices):
            reasons[project_id] = "ESTIMATE_ORDER_UNRESOLVED"
            continue
        effective = max(item.statement.effective_date or "" for item in choices)
        if any(
            date != effective and effective.startswith(date + "-")
            for date in (item.statement.effective_date for item in choices)
            if date
        ):
            reasons[project_id] = "ESTIMATE_ORDER_PRECISION_UNRESOLVED"
            continue
        current = [item for item in choices if (item.statement.effective_date or "") == effective]
        signatures = {
            tuple(
                (m.raw.variable, Decimal(m.candidate.value), m.candidate.unit,
                 m.candidate.chemical_basis, m.candidate.material_basis, m.validation.outcome)
                for m in item.measurements
            )
            for item in current
        }
        if len(signatures) != 1:
            reasons[project_id] = "CONFLICTING_SAME_DATE_ESTIMATES"
            continue
        chosen = min(current, key=lambda item: (item.source.sha256, item.source.source_key))
        selection[project_id] = tuple(m for m in chosen.measurements if m.validation.outcome == "VALIDATED")
        reasons[project_id] = "APPROVED_REPORTING_SCOPE"
    return selection, reasons


def _matched_editions(spec: dict, manifest: dict) -> list[tuple[dict, dict, Entity]]:
    result = []
    for binding in spec["bindings"]:
        project = _project(binding)
        editions = [
            edition for edition in manifest["editions"]
            if edition["url"] in binding["approved_urls"]
            and binding["source_key"] in edition["source_keys"]
            and project.entity_id in edition["project_ids"]
        ]
        for edition in sorted(editions, key=lambda item: item["sha256"]):
            result.append((binding, edition, project))
    return result


def _write_report(out: Path, receipt: dict, rows: list[dict]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "extraction.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "observations.jsonl").write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")
    return receipt


def extract_sources(
    bindings: Path, receipt: Path, assets: Path, database: Path, out: Path, *, code_commit: str,
) -> dict:
    """Rebuild typed observation and selection records from the V3 acquisition store."""
    spec = json.loads(bindings.read_bytes())
    manifest_bytes = receipt.read_bytes()
    manifest = json.loads(manifest_bytes)
    if spec["schema"] != SCHEMA or manifest["schema"] != ACQUISITION_SCHEMA:
        raise ValueError("Unsupported extraction bindings or acquisition manifest")
    if any(spec[key] != manifest[key] for key in ("profile_id", "cutoff", "timezone")):
        raise ValueError("The extraction and acquisition research profiles disagree")
    if not spec["bindings"]:
        raise ValueError("No reviewed structure bindings")
    if database.resolve() in {(out / name).resolve() for name in ("extraction.json", "observations.jsonl")}:
        raise ValueError("Database path overlaps extraction outputs")
    if len({row["case_id"] for row in spec["bindings"]}) != len(spec["bindings"]):
        raise ValueError("Extraction binding case IDs must be unique")

    cutoff = spec["cutoff"]
    rules_sha = digest([RULE, spec])
    timezone = ZoneInfo(spec["timezone"])
    content = ContentStore(assets)
    matches = _matched_editions(spec, manifest)
    found = {binding["case_id"] for binding, _, _ in matches}
    pages_required = {}
    for binding, edition, _ in matches:
        required = max(
            binding["page"], binding.get("details_page", 1), binding.get("summary_page", 1),
            binding.get("composition_table", {}).get("page", 1),
            *(item["page"] for item in binding.get("context", ())),
        )
        pages_required[edition["sha256"]] = max(required, pages_required.get(edition["sha256"], 0))
    cases = [{
        "case_id": binding["case_id"], "project_id": binding["project"]["entity_id"],
        "source_key": binding["source_key"], "method": binding["strategy"],
        "source_integrity": "MISSING", "outcome": "SOURCE_UNAVAILABLE", "reason": "NO_MATCHING_CAPTURED_EDITION",
    } for binding in spec["bindings"] if binding["case_id"] not in found]
    originals = {}
    documents = {}
    cache = DocumentCache()
    statements = []
    for binding, edition, project in matches:
        source = Source(
            source_key=edition["source_key"], url=edition["url"], sha256=edition["sha256"],
            kind=edition["kind"], source_version=edition["source_version"],
            publication_date=binding.get("publication_date"), acquired_at=edition["acquired_at"],
            original_path=f"objects/{edition['sha256']}",
        )
        case = {
            "case_id": binding["case_id"], "project_id": project.entity_id, "source_key": source.source_key,
            "source_sha256": source.sha256, "method": binding["strategy"], "source_integrity": "NOT_RUN",
            "scientific_comparability": "NOT_EVALUATED",
        }
        cases.append(case)
        try:
            if source.sha256 not in originals:
                originals[source.sha256] = content.read(source.sha256)
            case["source_integrity"] = "EXACT_SHA"
        except FileNotFoundError:
            case.update(source_integrity="MISSING", outcome="SOURCE_UNAVAILABLE", reason="SOURCE_OBJECT_MISSING")
            continue
        except IntegrityError:
            case.update(source_integrity="WRONG_SHA", outcome="SCIENTIFIC_CONFLICT", reason="SOURCE_OBJECT_HASH_MISMATCH")
            continue
        try:
            proofs = validate_edition_proofs(content, edition, timezone)
        except (OSError, ValueError, KeyError, TypeError):
            proofs = ()
        case["proof_status"] = "EXACT_CAPTURE_PROOF" if proofs else "UNVERIFIED_CAPTURE_PROOF"
        temporal = evaluate_temporal(
            source_sha256=source.sha256, cutoff=cutoff, publication_date=source.publication_date,
            acquired_at=source.acquired_at, reacquired_at=edition["reacquired_at"],
            observation_role="DOCUMENT", evidence=proofs,
        )
        case["temporal_decision"] = temporal.to_dict()
        if temporal.status in {"INELIGIBLE", "CONFLICT"}:
            case.update(outcome=temporal.status, reason=temporal.reason)
            continue
        try:
            if source.kind != "pdf":
                raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_BINDING_REQUIRES_PDF")
            if source.sha256 not in documents:
                documents[source.sha256] = cache.pdf(
                    originals[source.sha256], source.sha256, pages_required[source.sha256],
                )
            decoded = STRATEGIES[binding["strategy"]](documents[source.sha256].pages, binding, source.publication_date)
        except DocumentError as error:
            compatibility = ("INCOMPATIBLE" if error.outcome == "SCIENTIFIC_CONFLICT" else
                             "INSUFFICIENT_EVIDENCE" if error.outcome == "EVIDENCE_INSUFFICIENT" else "NOT_EVALUATED")
            case.update(outcome=error.outcome, reason=str(error), scientific_comparability=compatibility)
            continue
        if binding.get("composition_table") or binding.get("oxide_aggregate_review"):
            from .composition_extraction import classified_total_oxides
            from .composition_review import corroborated_aggregate

            enriched = []
            for item in decoded:
                try:
                    components = classified_total_oxides(documents[source.sha256].pages, binding, item)
                    aggregate = corroborated_aggregate(
                        documents[source.sha256].pages, binding, item, originals[source.sha256], source.sha256,
                    )
                except DocumentError as error:
                    case["composition_extraction"] = {"outcome": error.outcome, "reason": str(error)}
                    components = aggregate = ()
                if components or aggregate:
                    case["composition_extraction"] = {"outcome": "EXTRACTED", "count": len(components + aggregate)}
                enriched.append(replace(item, measurements=item.measurements + components + aggregate))
            decoded = tuple(enriched)
        parsed = [
            _bound_statement(item, project, source, binding, proofs, cutoff, rules_sha, edition["reacquired_at"])
            for item in decoded
        ]
        statements.extend(parsed)
        case.update(
            outcome="EXTRACTED",
            reason="DECLARED_RESOURCE_STATEMENTS_BOUND",
            scientific_comparability=(
                "MIXED" if any(cell.conflict for item in decoded for cell in item.measurements)
                else "INSUFFICIENT_EVIDENCE" if any(cell.evidence_issue for item in decoded for cell in item.measurements)
                else "COMPATIBLE"
            ),
            scope_ids=[item.scope.scope_id for item in parsed],
            observation_decisions=[item.decision.to_dict() for item in parsed],
            observation_ids=[m.raw.observation_id for item in parsed for m in item.measurements],
        )

    selection, reasons = _select(statements)
    selected = {item.raw.observation_id: item for items in selection.values() for item in items}
    measurements = sorted(
        ((statement, cell) for statement in statements for cell in statement.measurements),
        key=lambda item: item[1].raw.observation_id,
    )
    rows = []
    for statement, item in measurements:
        is_selected = item.raw.observation_id in selected
        if is_selected:
            selection_status = "SELECTED"
        elif item.validation.outcome != "VALIDATED":
            selection_status = "UNVALIDATED"
        elif not statement.statement.primary:
            selection_status = "SHADOW_OUTSIDE_REPORTING_SCOPE"
        else:
            selection_status = reasons.get(item.raw.project_id, "NO_COMPATIBLE_SELECTION")
            if selection_status == "APPROVED_REPORTING_SCOPE":
                selection_status = "OTHER_COMPATIBLE_EDITION_SELECTED"
        rows.append(dict(item.science(), selected_for_census=is_selected, selection_status=selection_status))
    result = {
        "schema": SCHEMA, "profile_id": spec["profile_id"], "cutoff": cutoff, "timezone": spec["timezone"],
        "status": "BLOCKED", "release_id": None, "cases": cases, "selection_reasons": reasons,
        "rules_sha256": rules_sha, "acquisition_manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "parser": next(iter(documents.values())).runtime if documents else None,
        "source_count": len(originals), "parsed_source_count": len(documents),
        "mode_count": len({case["method"] for case in cases if case["outcome"] == "EXTRACTED"}),
        "selected_count": len(selected), "candidate_count": len(measurements),
        "validated_count": sum(item.validation.comparable for _, item in measurements),
        "scientific_fingerprint": digest(rows),
    }
    if not measurements:
        return _write_report(out, result, rows)
    decisions = tuple(sorted({item.validation.temporal_decision_id for item in selected.values()}))
    proofs = tuple(sorted({proof.evidence_id for item in selected.values() for proof in item.validation.temporal_evidence}))
    release = ResearchRelease(
        release_id=digest([SCHEMA, code_commit, rules_sha, result["acquisition_manifest_sha256"], result["scientific_fingerprint"]]),
        input_sha=result["acquisition_manifest_sha256"], code_commit=code_commit, rules_sha=rules_sha,
        cutoff=cutoff, temporal_policy_version=TEMPORAL_POLICY_VERSION,
        temporal_decision_ids=decisions, temporal_evidence_ids=proofs,
        cohort=tuple(sorted({item.project.entity_id for item in statements})),
        validation_status="PASS" if selected else "NOT_RUN",
        release_mode="FORMAL" if selected else "EXPERIMENTAL",
        result_fingerprints={"admission_plan": result["scientific_fingerprint"]},
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    with Store(database, trusted_temporal_evidence_ids=proofs) as store:
        with store.transaction():
            for item in statements:
                store.add(item.project)
                store.add(item.scope)
                store.add_source(item.source, originals[item.source.sha256])
                for m in item.measurements:
                    for record in (m.anchor, m.raw, m.candidate, m.validation):
                        store.add(record)
            store.add(release)
            for m in selected.values():
                store.add(AcceptedResearchValue(
                    acceptance_id=digest(["fresh-resource-selection-v1", release.release_id, m.validation.validation_id]),
                    validation_id=m.validation.validation_id, release_id=release.release_id,
                    selection_key=f"{m.raw.project_id}:{m.raw.variable}", reason=RULE,
                ))
            emitted = checked_research_inputs(store, release, purpose="CENSUS")
            if len(emitted) != len(selected):
                raise RuntimeError("Census does not see the complete governed selection")
        result["sqlite_integrity"] = store.integrity()
    result["release_id"] = release.release_id
    result["release_mode"] = release.release_mode
    result["trusted_temporal_evidence_ids"] = list(proofs)
    selected_strategies = {json.loads(item.raw.original_payload)["extraction_strategy"] for item in selected.values()}
    result["selected_mode_count"] = len(selected_strategies)
    verified_modes = {
        case["method"] for case in cases
        if case["outcome"] == "EXTRACTED" and case["proof_status"] == "EXACT_CAPTURE_PROOF"
    }
    result["verified_source_mode_count"] = len(verified_modes)
    required_modes = {binding["strategy"] for binding in spec["bindings"] if not binding.get("optional")}
    result["status"] = "PASS" if required_modes <= verified_modes and selected else "BLOCKED"
    for case in cases:
        ids = case.get("observation_ids", [])
        case["selected_count"] = sum(identifier in selected for identifier in ids)
    return _write_report(out, result, rows)

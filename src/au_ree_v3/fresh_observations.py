"""Extract and govern scoped observations from captured V3 source editions."""

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from hashlib import sha256
from itertools import chain
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .comparability import TOTAL_REO_15, reported_inventory_bounds
from .contracts import (
    AcceptedResearchValue, Entity, EvidenceAnchor, NormalizedObservation, RawObservation,
    ResearchRelease, ResourceScope, Source, ValidatedObservation, canonical_json,
)
from .contracts.temporal import TEMPORAL_POLICY_VERSION, TemporalDecision, TemporalEvidence, evaluate_temporal
from .documents import DocumentCache, DocumentError
from .normalization import reported_interval
from .observations import CATEGORY, digest
from .research_inputs import (
    CENSUS_RESOURCE_RULE, CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE, NATIVE_RESOURCE_CANDIDATE, RESOURCE_RULE,
    checked_research_inputs,
)
from .resource_extraction import STRATEGIES, ResourceCell, ResourceStatement
from .sources import ContentStore, IntegrityError, SCHEMA as ACQUISITION_SCHEMA, validate_edition_proofs
from .store import Store

SCHEMA = "fresh-resource-extraction-v1"
RULE = RESOURCE_RULE
_NATIVE_STRATEGY = "native-resource-table-v1"
_CENSUS_FIELDS = frozenset(("ore_mt", "grade"))
_RESOURCE_CATEGORIES = {CATEGORY, "INDICATED_INFERRED_TOTAL", "MEASURED_INDICATED_TOTAL"}


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
    research_use: str | None = None
    case_id: str | None = None


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
    native = binding["strategy"] == _NATIVE_STRATEGY
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
    native = binding["strategy"] == _NATIVE_STRATEGY
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


def _native_resource_verdict(item: BoundStatement) -> tuple[str, str]:
    statement = item.statement
    uses = {CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE}
    if item.research_use not in uses:
        return "CANDIDATE_ONLY", "NO_AUTHORIZED_NATIVE_RESOURCE_USE"
    full = item.research_use == MCDA_RESOURCE_USE
    if any(cell.validation.outcome == "SCIENTIFIC_CONFLICT" for cell in item.measurements):
        return "SCIENTIFIC_CONFLICT", "CONFLICTING_NATIVE_MEASUREMENT"
    if item.decision.status != "ELIGIBLE":
        return item.decision.status, item.decision.reason
    if not statement.primary or statement.resource_class != "MINERAL_RESOURCE" or statement.reporting_standard != "JORC 2012":
        return "REJECTED", "PRIMARY_JORC_MINERAL_RESOURCE_REQUIRED"
    if statement.category not in _RESOURCE_CATEGORIES:
        return "REJECTED", "APPROVED_PROJECT_RESOURCE_CATEGORY_REQUIRED"
    if statement.cutoff_analyte != "TREO" or statement.cutoff_unit not in {"ppm", "%"} or statement.cutoff is None:
        return "REJECTED", "EXACT_PRIMARY_TREO_CUTOFF_REQUIRED"
    if not 0 < Decimal(statement.cutoff) <= (100 if statement.cutoff_unit == "%" else 1_000_000):
        return "SCIENTIFIC_CONFLICT", "INVALID_PRIMARY_TREO_CUTOFF"
    if tuple(sorted(statement.treo_members)) != tuple(sorted(TOTAL_REO_15)):
        return "EVIDENCE_INSUFFICIENT", "EXACT_TREO_MEMBER_SET_REQUIRED"
    needed = _CENSUS_FIELDS | {"contained_treo_kt"} if full else _CENSUS_FIELDS
    if len(item.measurements) != len(needed) or {m.raw.variable for m in item.measurements} != needed:
        return "REJECTED", "EXACT_REPORTED_RESOURCE_BUNDLE_REQUIRED" if full else "EXACT_ORE_AND_GRADE_PAIR_REQUIRED"
    anchors = {(m.anchor.page, m.anchor.table, m.anchor.row) for m in item.measurements}
    if len(anchors) != 1 or not all(next(iter(anchors))) or len({m.anchor.column for m in item.measurements}) != len(needed):
        return "EVIDENCE_INSUFFICIENT", "EXACT_SAME_ROW_AND_DISTINCT_FIELDS_REQUIRED"
    units = {"ore_mt": {"Mt"}, "grade": {"ppm", "%"}, "contained_treo_kt": {"kt TREO"}}
    for m in item.measurements:
        raw = m.raw
        payload = json.loads(raw.original_payload)
        evidence = payload.get("native_table_binding")
        if (m.validation.outcome != "VALIDATED" or not isinstance(evidence, dict)
                or not all(evidence.get(key) for key in ("value_anchor", "header_anchor", "unit_anchor", "caption_anchors"))):
            return "EVIDENCE_INSUFFICIENT", "NATIVE_CELL_EVIDENCE_REQUIRED"
        if raw.material_basis != "WHOLE_ORE" or raw.chemical_basis != "TREO" or (
            raw.variable == "grade" and raw.denominator_basis != "WHOLE_ORE"
        ):
            return "REJECTED", "WHOLE_ORE_TREO_QUANTITY_REQUIRED"
        if m.candidate.unit not in units[raw.variable]:
            return "REJECTED", "CENSUS_RESOURCE_UNIT_NOT_SUPPORTED"
        max_grade = 100 if m.candidate.unit == "%" else 1_000_000
        value = Decimal(m.candidate.value)
        if value <= 0 or (raw.variable == "grade" and value > max_grade):
            return "SCIENTIFIC_CONFLICT", "CENSUS_RESOURCE_QUANTITY_OUT_OF_RANGE"
    if full:
        cells = {cell.variable: cell for cell in statement.measurements}
        ore, grade, contained = (cells[name] for name in ("ore_mt", "grade", "contained_treo_kt"))
        def selected_precision(cell):
            raw, low, _ = reported_interval(cell.token)
            return Decimal(cell.value), (raw - low) * Decimal(cell.factor)
        ore_value, ore_half = selected_precision(ore)
        grade_value, grade_half = selected_precision(grade)
        inventory = Decimal(contained.value)
        low, high = reported_inventory_bounds(ore_value, ore_half, grade_value, grade_half, inventory,
                                               grade.normalized_unit == "%")
        if not low <= inventory <= high:
            return "SCIENTIFIC_CONFLICT", "REPORTED_CONTAINED_OUTSIDE_ROUNDING_INTERVAL"
        return "ADMISSIBLE", "COMPLETE_DIRECT_REPORTED_RESOURCE_BUNDLE"
    return "ADMISSIBLE", "EXACT_DIRECT_CENSUS_RESOURCE_OBSERVATION"


def _native_resource_admission(statements, legacy_selected):
    legacy_primary = {item.project.entity_id for item in statements if item.research_use is None and item.statement.primary}
    native = [item for item in statements if item.research_use is not None]
    requested = defaultdict(list)
    for item in native:
        if item.research_use in {CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE} and item.statement.primary:
            requested[item.project.entity_id].append(item)
    selected, audit = {}, []
    for item in native:
        project_id = item.project.entity_id
        verdict, reason = _native_resource_verdict(item)
        if verdict == "ADMISSIBLE":
            if project_id in legacy_primary or project_id in legacy_selected:
                verdict, reason = "REJECTED", "EXISTING_RESEARCH_STATEMENT_PRESERVED"
            elif len(requested[project_id]) != 1:
                verdict, reason = "SCIENTIFIC_CONFLICT", "ALTERNATIVE_CENSUS_RESOURCE_STATEMENTS_REQUIRE_RESOLUTION"
            else:
                verdict = "SELECTED"
                selected[project_id] = item.measurements
        audit.append({
            "project_id": project_id, "statement_id": item.statement_id, "scope_id": item.scope.scope_id,
            "case_id": item.case_id, "source_key": item.source.source_key,
            "scope": item.scope.name, "edition_sha256": item.source.sha256, "source_url": item.source.url,
            "research_use": item.research_use,
            "policy_version": RULE if item.research_use == MCDA_RESOURCE_USE else CENSUS_RESOURCE_RULE,
            "verdict": verdict, "reason": reason,
            "mcda_r1_s1_use": verdict == "SELECTED" and item.research_use == MCDA_RESOURCE_USE,
            "contained_mass_selection": verdict == "SELECTED" and item.research_use == MCDA_RESOURCE_USE,
            "effective_date": item.statement.effective_date, "publication_date": item.source.publication_date,
            "category": item.statement.category, "resource_class": item.statement.resource_class,
            "reporting_standard": item.statement.reporting_standard, "treo_members": item.statement.treo_members,
            "cutoff": [item.statement.cutoff, item.statement.cutoff_unit, item.statement.cutoff_analyte],
            "temporal_decision": item.decision.to_dict(),
            "statement_context": [{"physical_page": page, "quote": quote} for page, quote in item.statement.context],
            "observations": [{
                **entry.science(), "anchor_id": entry.anchor.anchor_id, "quote": entry.anchor.quote,
                "treo_image_context": json.loads(entry.raw.original_payload).get("treo_image_context"),
                "candidate_id": entry.candidate.candidate_id, "validation_id": entry.validation.validation_id,
                "evidence": json.loads(entry.raw.original_payload).get("native_table_binding"),
            } for entry in item.measurements],
        })
    return selected, audit


def _matched_editions(spec: dict, manifest: dict) -> list[tuple[dict, dict, Entity]]:
    result = []
    for binding in spec["bindings"]:
        project = _project(binding)
        editions = [
            edition for edition in manifest["editions"]
            if edition["url"] in binding["approved_urls"]
            and binding["source_key"] in edition["source_keys"]
            and project.entity_id in edition["project_ids"]
            and (binding["strategy"] != _NATIVE_STRATEGY or binding.get("source_sha256") == edition["sha256"])
        ]
        for edition in sorted(editions, key=lambda item: item["sha256"]):
            result.append((binding, edition, project))
    return result


def _write_report(out: Path, receipt: dict, rows: list[dict]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    if audit := receipt.get("census_resource_admission"):
        outputs = {
            "admission_decisions.jsonl": audit,
            "scientific_conflicts.jsonl": [
                entry for entry in audit if entry["verdict"] in {"SCIENTIFIC_CONFLICT", "CONFLICT"}
                or any(row["outcome"] == "SCIENTIFIC_CONFLICT" for row in entry["observations"])
            ],
        }
        receipt["admission_artifact_sha256"] = {}
        for name, items in outputs.items():
            payload = "".join(canonical_json(item) + "\n" for item in items).encode("utf-8")
            (out / name).write_bytes(payload)
            receipt["admission_artifact_sha256"][name] = sha256(payload).hexdigest()
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
    native_bindings = [binding for binding in spec["bindings"] if binding["strategy"] == _NATIVE_STRATEGY]
    for binding in native_bindings:
        if not isinstance(binding.get("source_sha256"), str) or len(binding["source_sha256"]) != 64 or any(
            character not in "0123456789abcdef" for character in binding["source_sha256"]
        ):
            raise ValueError("Native tables require an exact source SHA-256")
        if binding.get("research_use", NATIVE_RESOURCE_CANDIDATE) not in {CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE, NATIVE_RESOURCE_CANDIDATE}:
            raise ValueError("Unknown native resource research use")
    rules_sha = digest([RULE, CENSUS_RESOURCE_RULE, spec] if native_bindings else [RULE, spec])
    timezone = ZoneInfo(spec["timezone"])
    content = ContentStore(assets)
    matches = _matched_editions(spec, manifest)
    found = {binding["case_id"] for binding, _, _ in matches}
    pages_required = {}
    for binding, edition, _ in matches:
        required = max(
            binding["page"], binding.get("details_page", 1), binding.get("summary_page", 1),
            binding.get("composition_table", {}).get("page", 1),
            binding.get("treo_image_context", {}).get("physical_page", 1),
            binding.get("table", {}).get("header_page", 1),
            *(item["page"] for item in binding.get("context", ())),
            *(link[name]["page"] for link in binding.get("context_links", ())
              for name in ("start", "end", "reference") if name in link),
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
        if binding["strategy"] == _NATIVE_STRATEGY and (image_review := binding.get("treo_image_context")):
            from .composition_review import verify_treo_image_context

            reviewed = []
            for item in decoded:
                if not item.primary:
                    reviewed.append(item)
                    continue
                measurements = item.measurements
                try:
                    members, evidence = verify_treo_image_context(
                        documents[source.sha256].pages, item, image_review, originals[source.sha256],
                    )
                except DocumentError as error:
                    case["composition_extraction"] = {"outcome": error.outcome, "reason": str(error)}
                    field = "conflict" if error.outcome == "SCIENTIFIC_CONFLICT" else "evidence_issue"
                    reviewed.append(replace(item, measurements=tuple(
                        replace(cell, **{field: str(error)}) for cell in measurements
                    )))
                    continue
                case["composition_extraction"] = {"outcome": "VERIFIED_NATIVE_IMAGE_MEMBER_COLUMNS"}
                reviewed.append(replace(
                    item, treo_members=members,
                    context=item.context + tuple((image_review["physical_page"], image_review[key])
                                                 for key in ("statement_quote", "definition_quote")),
                    measurements=tuple(_with_image_chemistry(cell, evidence) for cell in measurements),
                ))
            decoded = tuple(reviewed)
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

    selection, reasons = _select([item for item in statements if item.research_use is None])
    native_selected, admission = _native_resource_admission(statements, selection)
    selection.update(native_selected)
    for item in admission:
        if item["verdict"] == "SELECTED":
            reasons[item["project_id"]] = item["policy_version"]
    scoped_ids = {entry["observation_id"] for item in admission if item["verdict"] == "SELECTED"
                  and item["research_use"] == CENSUS_RESOURCE_USE for entry in item["observations"]}
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
        elif statement.research_use is not None:
            selection_status = "NATIVE_RESOURCE_CANDIDATE_NOT_SELECTED"
        elif not statement.statement.primary:
            selection_status = "SHADOW_OUTSIDE_REPORTING_SCOPE"
        else:
            selection_status = reasons.get(item.raw.project_id, "NO_COMPATIBLE_SELECTION")
            if selection_status == "APPROVED_REPORTING_SCOPE":
                selection_status = "OTHER_COMPATIBLE_EDITION_SELECTED"
        row = dict(item.science(), selected_for_census=is_selected, selection_status=selection_status)
        if statement.research_use is not None:
            row.update(research_use=statement.research_use,
                       selection_rule=(CENSUS_RESOURCE_RULE if item.raw.observation_id in scoped_ids else RULE)
                       if is_selected else None)
        rows.append(row)
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
    if native_bindings:
        for case in cases:
            if case["method"] != _NATIVE_STRATEGY or case["outcome"] == "EXTRACTED":
                continue
            binding = next(item for item in native_bindings if item["case_id"] == case["case_id"])
            admission.append({
                "project_id": case["project_id"], "source_key": case["source_key"], "case_id": case["case_id"],
                "research_use": binding.get("research_use", NATIVE_RESOURCE_CANDIDATE),
                "edition_sha256": case.get("source_sha256", binding["source_sha256"]), "policy_version": CENSUS_RESOURCE_RULE,
                "verdict": case["outcome"], "reason": case["reason"], "observations": [],
                "mcda_r1_s1_use": False, "contained_mass_selection": False,
            })
        result["census_resource_admission"] = admission
        result["census_only_selected_count"] = len(scoped_ids)
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
                    selection_key=f"{m.raw.project_id}:{m.raw.variable}",
                    reason=CENSUS_RESOURCE_RULE if m.raw.observation_id in scoped_ids else RULE,
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

"""Replay a reviewed resource slice from exact PDF assets in an experimental release."""

from collections import Counter
import csv
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from .census import COLUMNS, accepted_resource_rows, table_alternatives
from .contracts import (
    AcceptedResearchValue, ContractViolation, Entity, EvidenceAnchor, NormalizedObservation,
    RawObservation, ResearchRelease, ResourceScope, Source, ValidatedObservation, canonical_json,
)
from .contracts.temporal import TEMPORAL_POLICY_VERSION, TemporalDecision, TemporalEvidence, evaluate_temporal
from .documents import DocumentError, pdf_pages, pdf_parser_version, read_asset
from .observations import ResourceEvidence, VARIABLES, digest, exact_value, legacy_claim, read_resource
from .store import Store

SCHEMA = "reviewed-resource-bindings-v1"
READER = "native-narrative-and-total-table-v1"
SELECTION_REASON = "MATCHED_SELECTED_STATEMENT"


@dataclass
class Assessment:
    binding: dict
    project: Entity
    scope: ResourceScope
    source: Source
    outcome: str
    reason: str
    document_temporal: TemporalDecision
    observation_temporal: TemporalDecision
    temporal_evidence: tuple[TemporalEvidence, ...]
    reacquired_at: str | None
    evidence: ResourceEvidence | None = None
    content: bytes | None = None
    observed_sha256: str | None = None
    source_integrity: str = "NOT_RUN"
    scientific_comparability: str = "NOT_EVALUATED"

    def receipt(self) -> dict:
        return {
            "case_id": self.binding["case_id"],
            "project_id": self.project.entity_id,
            "statement_id": self.binding["statement_id"],
            "scope_id": self.scope.scope_id,
            "source_sha256": self.source.sha256,
            "observed_sha256": self.observed_sha256,
            "source_integrity": self.source_integrity,
            "scientific_comparability": self.scientific_comparability,
            "outcome": self.outcome,
            "reason": self.reason,
            "document_temporal": self.document_temporal.to_dict(),
            "observation_temporal": self.observation_temporal.to_dict(),
        }


def _assess(
    binding: dict, assets: Path, cutoff: str,
    originals: dict[str, bytes], documents: dict[tuple[str, int], tuple[str, ...]],
) -> Assessment:
    source_fields = dict(binding["source"])
    reacquired_at = source_fields.pop("reacquired_at", None)
    source = Source(**source_fields)
    project = Entity(kind="PROJECT", **binding["project"])
    if project.entity_id != "AU-" + digest([project.native_namespace, project.native_id])[:20]:
        raise ContractViolation("binding contains a mismatched native Project ID")
    scope = ResourceScope(
        scope_id=digest(["scope-v1", project.entity_id, binding["scope_name"]]),
        project_id=project.entity_id,
        name=binding["scope_name"],
    )
    if binding["reader"] != READER or source.kind != "pdf" or type(binding["page"]) is not int or binding["page"] < 1:
        raise ContractViolation("unsupported reviewed resource binding")
    proofs = tuple(TemporalEvidence(**entry) for entry in binding.get("temporal_evidence", ()))
    temporal_inputs = {
        "source_sha256": source.sha256,
        "cutoff": cutoff,
        "publication_date": source.publication_date,
        "available_date": source.available_date,
        "acquired_at": source.acquired_at,
        "reacquired_at": reacquired_at,
        "availability_role": source.availability_role,
        "evidence": proofs,
    }
    document_gate = evaluate_temporal(**temporal_inputs, observation_role="DOCUMENT")
    observation_gate = evaluate_temporal(
        **temporal_inputs, effective_date=binding["effective_date"],
        reported_publication_date=source.publication_date, observation_role="HISTORICAL_MEASUREMENT",
    )
    outcome_by_status = {
        "ELIGIBLE": "VALIDATED",
        "INELIGIBLE": "AFTER_CUTOFF",
        "CANDIDATE": "EVIDENCE_INSUFFICIENT",
        "CONFLICT": "SCIENTIFIC_CONFLICT",
    }
    item = Assessment(
        binding, project, scope, source, outcome_by_status[observation_gate.status],
        observation_gate.reason, document_gate, observation_gate, proofs, reacquired_at,
    )
    parse_allowed = observation_gate.status in {"ELIGIBLE", "CANDIDATE"}
    try:
        if source.sha256 not in originals:
            originals[source.sha256] = read_asset(source, assets)
    except DocumentError as error:
        item.observed_sha256 = error.observed_sha256
        item.source_integrity = str(error)
        if parse_allowed:
            item.outcome, item.reason = error.outcome, str(error)
        return item

    item.observed_sha256 = source.sha256
    item.source_integrity = "EXACT_SHA"
    if not parse_allowed:
        return item
    item.content = originals[source.sha256]
    page_key = (source.sha256, binding["page"])
    try:
        if page_key not in documents:
            documents[page_key] = pdf_pages(item.content, binding["page"])
        item.evidence = read_resource(documents[page_key], binding)
    except DocumentError as error:
        if error.outcome == "SCIENTIFIC_CONFLICT":
            item.scientific_comparability = "INCOMPATIBLE"
        if error.outcome == "EVIDENCE_INSUFFICIENT":
            item.scientific_comparability = "INSUFFICIENT_EVIDENCE"
        if observation_gate.status != "CANDIDATE" or error.outcome != "EVIDENCE_INSUFFICIENT":
            item.outcome, item.reason = error.outcome, str(error)
        return item
    item.scientific_comparability = "COMPATIBLE"
    if item.outcome == "VALIDATED":
        item.reason = "BOUND_SAME_PAGE_V2_DECLARATION"
    return item


def _write_observations(store: Store, item: Assessment, release: ResearchRelease) -> None:
    store.add_source(item.source, item.content)
    evidence = item.evidence
    for variable, token, quote in zip(VARIABLES, evidence.tokens, evidence.quotes):
        unit, printed_unit = VARIABLES[variable]
        claim = legacy_claim(item.binding, evidence, variable, token)
        observation_id = digest([item.project.entity_id, item.source.sha256, claim])
        is_table = variable == "contained_treo_kt"
        anchor = EvidenceAnchor(
            anchor_id=digest(["resource-anchor-v1", observation_id, quote]),
            source_key=item.source.source_key, source_sha=item.source.sha256,
            locator=item.binding["locator"], page=item.binding["page"],
            table=evidence.table if is_table else None,
            row="Total" if is_table else None,
            column="TREO" if is_table else None,
            quote=quote,
            role="REPORTED_RESOURCE_VALUE",
        )
        raw = RawObservation(
            observation_id=observation_id, project_id=item.project.entity_id, scope_id=item.scope.scope_id,
            source_key=item.source.source_key, source_sha=item.source.sha256, variable=variable, raw_value=token,
            raw_unit=printed_unit, status="REPORTED", transform_version=READER, anchor_id=anchor.anchor_id,
            material_basis=item.binding["material_basis"], chemical_basis=item.binding["chemical_basis"],
            denominator_basis="WHOLE_ORE" if variable == "grade" else None, statement_id=item.binding["statement_id"],
            category=item.binding["category"], effective_date=item.binding["effective_date"],
            reported_publication_date=item.source.publication_date, id_version="v2-published-observation-id",
            observation_role="HISTORICAL_MEASUREMENT",
            original_payload={
                "legacy_claim": claim,
                "legacy_claim_reference": item.binding["legacy_claim_reference"],
                "resource_category": item.binding["category"], "reporting_standard": "JORC 2012",
                "cutoff_grade_pct_treo": evidence.cutoff_grade, "native_unit": printed_unit,
                "table_label": evidence.table, "table_title": evidence.title,
                "table_tokens": list(evidence.table_tokens),
                "chemical_definition": evidence.definition, "effective_date_quote": evidence.date_quote,
            },
        )
        candidate = NormalizedObservation(
            candidate_id=digest(["resource-normalized-v1", observation_id, token]),
            observation_id=observation_id, value=format(exact_value(token), "f"), unit=unit,
            material_basis=raw.material_basis, chemical_basis=raw.chemical_basis, denominator_basis=raw.denominator_basis,
            transform_version="reported-resource-decimal-v1",
        )
        validation = ValidatedObservation(
            validation_id=digest([
                "resource-validation-v2", candidate.candidate_id, release.rules_sha, item.observation_temporal.decision_id,
            ]),
            candidate_id=candidate.candidate_id, cutoff=release.cutoff, rules_sha=release.rules_sha,
            outcome=item.outcome, reason=item.reason, evidence_anchor_ids=(anchor.anchor_id,),
            comparable=item.outcome == "VALIDATED",
            temporal_evidence=item.temporal_evidence,
            temporal_decision_id=item.observation_temporal.decision_id,
            reacquired_at=item.reacquired_at,
        )
        for record in (anchor, raw, candidate, validation):
            store.add(record)
        if item.outcome == "VALIDATED":
            store.add(AcceptedResearchValue(
                acceptance_id=digest(["resource-selection-v1", release.release_id, validation.validation_id]),
                validation_id=validation.validation_id, release_id=release.release_id,
                selection_key=f"{item.project.entity_id}:{variable}", reason=SELECTION_REASON,
            ))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def _rejection_rows(assessments: list[dict], observations: list[dict], cells: list[dict],
                    profile: str, cutoff: str) -> list[dict]:
    selected = {
        (
            value["project_id"], value["statement_id"], value["source_sha256"],
            digest(["scope-v1", value["project_id"], value["scope"]]),
        )
        for value in (row["values"] for row in observations)
    }
    cells_by_project = {}
    for row in cells:
        value = row["values"]
        cells_by_project.setdefault(value["project_id"], {})[value["field"]] = {
            key: value[key] for key in ("value", "value_state", "status")
        }
    return [
        {
            "case_id": f"{profile}:rejection:{item['case_id']}",
            "values": {
                "outcome": item["outcome"], "reason": item["reason"],
                "research_cutoff": cutoff,
                "selected_for_census": (
                    item["project_id"], item["statement_id"], item["source_sha256"], item["scope_id"],
                ) in selected,
                "source_edition": item["source_integrity"],
                "census": cells_by_project.get(item["project_id"], {}),
            },
            "v3_reference": "replay.json",
        }
        for item in assessments if item["outcome"] != "VALIDATED"
    ]


def replay(bindings: Path, assets: Path, database: Path, out: Path, *, code_commit: str) -> dict:
    """Read locked assets, apply a scoped selection, and project accepted cells."""
    outputs = (
        "v3-observations.jsonl", "v3-evidence.jsonl", "v3-oracle-actual.jsonl",
        "v3-oracle-rejections.jsonl", "v3-table-alternatives.jsonl", "census.csv", "replay.json",
    )
    if database.resolve() in {(out / name).resolve() for name in outputs}:
        raise ValueError("database path overlaps a replay output")
    out.mkdir(parents=True, exist_ok=True)
    receipt = {"release_mode": "EXPERIMENTAL", "oracle_status": "NOT_RUN"}
    observations, cells, evidence, rejections, alternatives = [], [], [], [], []
    try:
        manifest_bytes = bindings.read_bytes()
        spec = json.loads(manifest_bytes)
        if spec["schema"] != SCHEMA or not spec["bindings"]:
            raise ContractViolation("expected a nonempty reviewed resource bindings v1 manifest")
        if len({row["case_id"] for row in spec["bindings"]}) != len(spec["bindings"]):
            raise ContractViolation("binding case IDs must be unique")
        cutoff = date.fromisoformat(spec["research_cutoff"]).isoformat()
        originals = {}
        documents = {}
        results = [
            _assess(binding, assets, cutoff, originals, documents)
            for binding in sorted(spec["bindings"], key=lambda row: row["case_id"])
        ]
        competing = Counter(item.project.entity_id for item in results if item.outcome == "VALIDATED")
        for item in results:
            if item.outcome == "VALIDATED" and competing[item.project.entity_id] > 1:
                item.outcome, item.reason = "SCIENTIFIC_CONFLICT", "COMPETING_STATEMENTS"
                item.scientific_comparability = "INCOMPATIBLE"
        receipts = [item.receipt() for item in results]
        rules_sha = digest(spec)
        input_sha = digest(receipts)
        parser = pdf_parser_version() if documents else None
        release = ResearchRelease(
            release_id=digest(["experimental-resource-release-v1", rules_sha, input_sha, code_commit, parser]),
            input_sha=input_sha, code_commit=code_commit, rules_sha=rules_sha, cutoff=cutoff,
            cohort=tuple(sorted({result.project.entity_id for result in results})),
            validation_status="NOT_RUN",
            temporal_policy_version=TEMPORAL_POLICY_VERSION,
            temporal_decision_ids=tuple(sorted({item.observation_temporal.decision_id for item in results})),
        )
        database.parent.mkdir(parents=True, exist_ok=True)
        with Store(database) as store:
            with store.transaction():
                for item in results:
                    store.add(item.project)
                    store.add(item.scope)
                store.add(release)
                for item in results:
                    if item.evidence is not None:
                        _write_observations(store, item, release)
                observations, cells, evidence = accepted_resource_rows(store, release, spec["profile"])
                receipt["sqlite_integrity"] = store.integrity()
                if receipt["sqlite_integrity"] != {"integrity_check": ["ok"], "foreign_key_check": []}:
                    raise ContractViolation("research SQLite integrity check failed")
        outcomes = {item.outcome for item in results}
        if outcomes & {"CODE_ERROR", "SCIENTIFIC_CONFLICT", "AFTER_CUTOFF"} or any(
            item.outcome == "EVIDENCE_INSUFFICIENT" and item.observation_temporal.status != "CANDIDATE"
            for item in results
        ):
            status = "FAIL"
        elif outcomes != {"VALIDATED"}:
            status = "BLOCKED"
        else:
            status = "PASS"
        rejections = _rejection_rows(receipts, observations, cells, spec["profile"], cutoff)
        alternatives = table_alternatives(observations, evidence, spec["profile"])
        receipt.update({
            "status": status, "release_id": release.release_id, "parser": parser,
            "cases": receipts, "observations": len(observations), "cells": len(cells),
            "scope": spec["profile"], "research_cutoff": cutoff, "rules_sha256": rules_sha,
            "input_sha256": input_sha, "bindings_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "temporal_policy_version": release.temporal_policy_version,
            "temporal_decision_ids": list(release.temporal_decision_ids),
            "source_pdf_count": len(originals),
            "parsed_pdf_count": len({source_sha for source_sha, _ in documents}),
            "scientific_fingerprint": digest([
                [row["values"] for row in observations + cells + rejections + alternatives],
                [{key: row[key] for key in ("case_id", "raw", "anchor")} for row in evidence],
            ]),
        })
    except (ContractViolation, DocumentError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        observations, cells, evidence = [], [], []
        receipt.update({"status": "FAIL", "outcome": "CODE_ERROR", "reason": str(error)})

    _write_jsonl(out / "v3-observations.jsonl", observations)
    _write_jsonl(out / "v3-evidence.jsonl", evidence)
    _write_jsonl(out / "v3-oracle-actual.jsonl", observations + cells)
    _write_jsonl(out / "v3-oracle-rejections.jsonl", rejections)
    _write_jsonl(out / "v3-table-alternatives.jsonl", alternatives)
    with (out / "census.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["project_id", "variable", "field", "value", "state"])
        writer.writeheader()
        for cell in cells:
            value = cell["values"]
            variable = "grade" if value["field"] == "treo_grade_pct" else value["field"]
            writer.writerow({
                "project_id": value["project_id"], "variable": variable, "field": COLUMNS[variable],
                "value": value["value"], "state": value["value_state"],
            })
    (out / "replay.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt

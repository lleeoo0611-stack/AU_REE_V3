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
    evidence: ResourceEvidence | None = None
    content: bytes | None = None
    observed_sha256: str | None = None

    def receipt(self) -> dict:
        return {
            "case_id": self.binding["case_id"],
            "project_id": self.project.entity_id,
            "statement_id": self.binding["statement_id"],
            "scope_id": self.scope.scope_id,
            "source_sha256": self.source.sha256,
            "observed_sha256": self.observed_sha256,
            "outcome": self.outcome,
            "reason": self.reason,
        }


def _assess(binding: dict, assets: Path, cutoff: str, documents: dict) -> Assessment:
    source = Source(**binding["source"])
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
    effective = date.fromisoformat(binding["effective_date"])
    raw_bytes_verified = False
    try:
        dates = [effective, *(date.fromisoformat(x) for x in (source.publication_date, source.available_date) if x)]
        if not source.publication_date or not source.available_date:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "HISTORICAL_SOURCE_DATES_REQUIRED")
        if max(dates) > date.fromisoformat(cutoff):
            raise DocumentError("AFTER_CUTOFF", "SOURCE_AFTER_CUTOFF")
        if (source.sha256, binding["page"]) not in documents:
            content = read_asset(source, assets)
            raw_bytes_verified = True
            documents[source.sha256, binding["page"]] = content, pdf_pages(content, binding["page"])
        content, pages = documents[source.sha256, binding["page"]]
        raw_bytes_verified = True
        evidence = read_resource(pages, binding)
    except DocumentError as error:
        observed_sha = source.sha256 if raw_bytes_verified else error.observed_sha256
        return Assessment(binding, project, scope, source, error.outcome, str(error), observed_sha256=observed_sha)
    return Assessment(
        binding, project, scope, source, "VALIDATED", "BOUND_SAME_PAGE_V2_DECLARATION",
        evidence, content, source.sha256,
    )


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
            validation_id=digest(["resource-validation-v1", candidate.candidate_id, release.rules_sha]),
            candidate_id=candidate.candidate_id, cutoff=release.cutoff, rules_sha=release.rules_sha,
            outcome=item.outcome, reason=item.reason, evidence_anchor_ids=(anchor.anchor_id,),
            comparable=item.outcome == "VALIDATED",
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
                "source_edition": (
                    "NOT_VERIFIED" if item["observed_sha256"] is None
                    else "EXACT_SHA" if item["observed_sha256"] == item["source_sha256"] else "WRONG_SHA"
                ),
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
        documents = {}
        results = [
            _assess(binding, assets, cutoff, documents)
            for binding in sorted(spec["bindings"], key=lambda row: row["case_id"])
        ]
        competing = Counter(item.project.entity_id for item in results if item.outcome == "VALIDATED")
        for item in results:
            if item.outcome == "VALIDATED" and competing[item.project.entity_id] > 1:
                item.outcome, item.reason = "SCIENTIFIC_CONFLICT", "COMPETING_STATEMENTS"
        receipts = [item.receipt() for item in results]
        rules_sha = digest(spec)
        input_sha = digest(receipts)
        parser = pdf_parser_version() if documents else None
        release = ResearchRelease(
            release_id=digest(["experimental-resource-release-v1", rules_sha, input_sha, code_commit, parser]),
            input_sha=input_sha, code_commit=code_commit, rules_sha=rules_sha, cutoff=cutoff,
            cohort=tuple(sorted({result.project.entity_id for result in results})),
            validation_status="NOT_RUN",
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
        outcomes = {row["outcome"] for row in receipts}
        if outcomes & {"CODE_ERROR", "SCIENTIFIC_CONFLICT", "EVIDENCE_INSUFFICIENT", "AFTER_CUTOFF"}:
            status = "FAIL"
        elif "SOURCE_UNAVAILABLE" in outcomes:
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
            "source_pdf_count": len({item.source.sha256 for item in results if item.observed_sha256 == item.source.sha256}),
            "scientific_fingerprint": digest([[row["values"] for row in observations + cells + rejections + alternatives], evidence]),
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

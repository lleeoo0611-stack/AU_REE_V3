"""Build deterministic resource evidence rows and extraction receipts."""

from collections.abc import Iterator
from hashlib import sha256
import json
from pathlib import Path

from .contracts import ResearchRelease, canonical_json
from .observations import digest
from .research_inputs import CENSUS_RESOURCE_RULE, MCDA_RESOURCE_USE, NATIVE_RESOURCE_CANDIDATE
from .resource_context import BoundStatement, Measurement
from .resource_selection import NativeAdmission, SelectionProposal
from .resource_sources import NATIVE_STRATEGY, SCHEMA, ResourceSourceReader, SourcePlan


def _admission_observation(entry: Measurement) -> dict:
    payload = json.loads(entry.raw.original_payload)
    return {
        **entry.science(payload), "anchor_id": entry.anchor.anchor_id, "quote": entry.anchor.quote,
        "treo_image_context": payload.get("treo_image_context"),
        "candidate_id": entry.candidate.candidate_id, "validation_id": entry.validation.validation_id,
        "evidence": payload.get("native_table_binding"),
    }


def _admission_record(decision: NativeAdmission) -> dict:
    item = decision.statement
    project_id = item.project.entity_id
    verdict, reason = decision.verdict, decision.reason
    selected_for_mcda = verdict == "SELECTED" and item.research_use == MCDA_RESOURCE_USE
    return {
        "project_id": project_id, "statement_id": item.statement_id, "scope_id": item.scope.scope_id,
        "case_id": item.case_id, "source_key": item.source.source_key,
        "scope": item.scope.name, "edition_sha256": item.source.sha256, "source_url": item.source.url,
        "research_use": item.research_use,
        "policy_version": decision.policy,
        "verdict": verdict, "reason": reason,
        "mcda_r1_s1_use": selected_for_mcda, "contained_mass_selection": selected_for_mcda,
        "effective_date": item.statement.effective_date, "publication_date": item.source.publication_date,
        "category": item.statement.category, "resource_class": item.statement.resource_class,
        "reporting_standard": item.statement.reporting_standard, "treo_members": item.statement.treo_members,
        "cutoff": [item.statement.cutoff, item.statement.cutoff_unit, item.statement.cutoff_analyte],
        "temporal_decision": item.decision.to_dict(),
        "statement_context": [{"physical_page": page, "quote": quote} for page, quote in item.statement.context],
        "observations": [_admission_observation(entry) for entry in item.measurements],
    }


def write_report(out: Path, receipt: dict, rows: list[dict]) -> dict:
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


def _selection_status(statement: BoundStatement, item: Measurement, selected: bool, reasons: dict[str, str]) -> str:
    if selected:
        return "SELECTED"
    if item.validation.outcome != "VALIDATED":
        return "UNVALIDATED"
    if statement.research_use is not None:
        return "NATIVE_RESOURCE_CANDIDATE_NOT_SELECTED"
    if not statement.statement.primary:
        return "SHADOW_OUTSIDE_REPORTING_SCOPE"
    reason = reasons.get(item.raw.project_id, "NO_COMPATIBLE_SELECTION")
    return "OTHER_COMPATIBLE_EDITION_SELECTED" if reason == "APPROVED_REPORTING_SCOPE" else reason


def _science_row(statement: BoundStatement, item: Measurement, proposal: SelectionProposal) -> dict:
    observation_id = item.raw.observation_id
    selected = observation_id in proposal.selected
    row = dict(
        item.science(), selected_for_census=selected,
        selection_status=_selection_status(statement, item, selected, proposal.reasons),
    )
    if statement.research_use is not None:
        row.update(research_use=statement.research_use, selection_rule=proposal.selection_rule(observation_id) if selected else None)
    return row


def _unavailable_admissions(cases: list[dict], plan: SourcePlan) -> Iterator[dict]:
    bindings = {item["case_id"]: item for item in plan.native_bindings}
    for case in cases:
        if case["method"] != NATIVE_STRATEGY or case["outcome"] == "EXTRACTED":
            continue
        binding = bindings[case["case_id"]]
        yield {
            "project_id": case["project_id"], "source_key": case["source_key"], "case_id": case["case_id"],
            "research_use": binding.get("research_use", NATIVE_RESOURCE_CANDIDATE),
            "edition_sha256": case.get("source_sha256", binding["source_sha256"]), "policy_version": CENSUS_RESOURCE_RULE,
            "verdict": case["outcome"], "reason": case["reason"], "observations": [],
            "mcda_r1_s1_use": False, "contained_mass_selection": False,
        }


def prepare_report(
    plan: SourcePlan, reader: ResourceSourceReader, cases: list[dict], statements: list[BoundStatement],
    proposal: SelectionProposal,
) -> tuple[dict, list[dict]]:
    measurements = sorted(
        ((statement, cell) for statement in statements for cell in statement.measurements),
        key=lambda item: item[1].raw.observation_id,
    )
    rows = [_science_row(statement, cell, proposal) for statement, cell in measurements]
    result = {
        "schema": SCHEMA, "profile_id": plan.spec["profile_id"], "cutoff": plan.cutoff, "timezone": plan.spec["timezone"],
        "status": "BLOCKED", "release_id": None, "cases": cases, "selection_reasons": proposal.reasons,
        "rules_sha256": plan.rules_sha, "acquisition_manifest_sha256": plan.manifest_sha,
        "parser": next(iter(reader.documents.values())).runtime if reader.documents else None,
        "source_count": len(reader.originals), "parsed_source_count": len(reader.documents),
        "mode_count": len({case["method"] for case in cases if case["outcome"] == "EXTRACTED"}),
        "selected_count": len(proposal.selected), "candidate_count": len(measurements),
        "validated_count": sum(item.validation.comparable for _, item in measurements),
        "scientific_fingerprint": digest(rows),
    }
    if plan.native_bindings:
        result["census_resource_admission"] = [
            *(_admission_record(decision) for decision in proposal.admissions), *_unavailable_admissions(cases, plan),
        ]
        result["census_only_selected_count"] = len(proposal.scoped_ids)
    return result, rows


def complete_report(
    result: dict, plan: SourcePlan, proposal: SelectionProposal, release: ResearchRelease, integrity: dict,
) -> None:
    result["sqlite_integrity"] = integrity
    result["release_id"] = release.release_id
    result["release_mode"] = release.release_mode
    result["trusted_temporal_evidence_ids"] = list(release.temporal_evidence_ids)
    selected_strategies = {
        json.loads(item.raw.original_payload)["extraction_strategy"] for item in proposal.selected.values()
    }
    result["selected_mode_count"] = len(selected_strategies)
    verified_modes = {
        case["method"] for case in result["cases"]
        if case["outcome"] == "EXTRACTED" and case["proof_status"] == "EXACT_CAPTURE_PROOF"
    }
    result["verified_source_mode_count"] = len(verified_modes)
    required_modes = {binding["strategy"] for binding in plan.spec["bindings"] if not binding.get("optional")}
    result["status"] = "PASS" if required_modes <= verified_modes and proposal.selected else "BLOCKED"
    for case in result["cases"]:
        case["selected_count"] = sum(identifier in proposal.selected for identifier in case.get("observation_ids", []))

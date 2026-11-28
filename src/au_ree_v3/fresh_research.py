"""Build and verify the fresh V3 research outputs from frozen local inputs."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from .census_metadata import CensusMetadata, prepare_census_metadata
from .contracts import ResearchRelease, canonical_json
from .contracts.temporal import evaluate_temporal
from .derivations import FORMULA_REGISTRY_SHA256, RELEASE_REGISTRY_PIN, persist_census_treo
from .fresh_census import build_census
from .fresh_decision import build_decision
from .fresh_observations import extract_sources
from .research_artifacts import ARTIFACTS
from .research_environment import environment_lock
from .registry import load_census_columns, load_profile, load_registry
from .sources import ContentStore, SCHEMA as SOURCE_SCHEMA, validate_edition_proofs
from .store import SCHEMA_VERSION, Store

BUILD_SCHEMA = "fresh-research-build-v1"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json_value(value: object) -> object:
    return json.loads(canonical_json(value))


def _verified_edition(profile, edition, event, content):
    source_sha256 = edition["sha256"]
    receipt_sha = edition["receipt_sha256"]
    if event is None or (
        event["sha256"], event["requested_url"], event["final_url"], event["project_ids"]
    ) != (source_sha256, edition["url"], edition["final_url"], edition["project_ids"]):
        raise ValueError("source edition does not bind the exact capture event and Projects")
    proofs = validate_edition_proofs(content, edition, profile.timezone)
    if not proofs:
        raise ValueError("captured source edition requires public capture proof")
    capture = json.loads(content.read(receipt_sha))
    if any(capture.get(key) != value for key, value in event.items() if key != "receipt_sha256"):
        raise ValueError("source manifest event differs from the immutable capture receipt")
    decision = evaluate_temporal(
        source_sha256=source_sha256, cutoff=profile.research_cutoff.isoformat(), observation_role="DOCUMENT",
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proofs,
    )
    if _json_value(decision.to_dict()) != edition["temporal_decision"]:
        raise ValueError("source edition temporal decision disagrees with its frozen public proof")
    return decision


def verify_source_manifest(profile, source_manifest: dict, registry, content: ContentStore) -> dict:
    """Read raw source and public capture pairs, then recompute each date decision."""
    if (
        source_manifest.get("schema") != SOURCE_SCHEMA
        or (source_manifest["profile_id"], source_manifest["cutoff"], source_manifest["timezone"])
        != (profile.profile_id, profile.research_cutoff.isoformat(), profile.timezone.key)
    ):
        raise ValueError("source manifest does not belong to the pinned fresh profile")

    project_ids = {project.project_id for project in registry.projects}
    events_by_project = defaultdict(list)
    event_by_capture = {}
    for event in source_manifest["events"]:
        event_projects = event["project_ids"]
        if not event_projects or len(set(event_projects)) != len(event_projects) or not set(event_projects) <= project_ids:
            raise ValueError("source event links missing, duplicate, or out-of-cohort Project IDs")
        for project_id in event_projects:
            events_by_project[project_id].append(event)
        if event["status"] == "PUBLIC_CAPTURED":
            key = event["receipt_sha256"]
            if key in event_by_capture:
                raise ValueError("source manifest capture receipt is repeated")
            event_by_capture[key] = event

    verified_sources = set()
    eligible_sources = set()
    reported_eligible_pairs = set()
    verified_pairs = set()
    verified_captures = set()
    referenced_captures = set()
    failures = []
    for edition in source_manifest["editions"]:
        source_sha256 = edition["sha256"]
        receipt_sha = edition["receipt_sha256"]
        event = event_by_capture.get(receipt_sha)
        if receipt_sha in referenced_captures:
            raise ValueError("source manifest edition repeats a current capture")
        referenced_captures.add(receipt_sha)
        pairs = {(project_id, source_sha256) for project_id in edition["project_ids"]}
        if edition["temporal_decision"]["status"] == "ELIGIBLE":
            reported_eligible_pairs.update(pairs)
        try:
            decision = _verified_edition(profile, edition, event, content)
        except (OSError, ValueError) as error:
            failures.append({
                "source_sha256": source_sha256, "receipt_sha256": receipt_sha,
                "status": "BLOCKED" if isinstance(error, OSError) else "FAIL",
                "reason": "SOURCE_OBJECT_UNAVAILABLE" if isinstance(error, OSError) else
                          "SOURCE_OBJECT_OR_PROOF_MISMATCH",
            })
            continue
        verified_captures.add(receipt_sha)
        verified_pairs.update(pairs)
        verified_sources.add(source_sha256)
        if decision.status == "ELIGIBLE":
            eligible_sources.add(source_sha256)
    if referenced_captures != set(event_by_capture):
        raise ValueError("captured source event has no corresponding source edition")

    project_statuses = {}
    for row in source_manifest.get("projects", ()):
        project_id = row["project_id"]
        if project_id not in project_ids or project_id in project_statuses:
            raise ValueError("source inventory contains a duplicate or unknown Project ID")
        events = events_by_project[project_id]
        acquired = {event["sha256"] for event in events if event["status"] == "PUBLIC_CAPTURED"}
        eligible = {digest for digest in acquired if (project_id, digest) in reported_eligible_pairs}
        if (
            row["attempt_count"] != len(events) or set(row["source_sha256"]) != acquired
            or set(row["eligible_source_sha256"]) != eligible
            or set(row["attempt_statuses"]) != {event["status"] for event in events}
        ):
            raise ValueError(f"source inventory differs from immutable per-Project events for {project_id}")
        expected_status = (
            "NOT_SEARCHED" if not events else
            "SOURCE_ACQUIRED" if acquired else
            "SOURCE_LOCATOR_REACHABLE" if any(item["status"] == "SOURCE_LOCATOR_REACHABLE" for item in events) else
            "NO_SOURCE_FOUND" if all(item["status"] in {"HTTP_404", "HTTP_410"} for item in events) else
            "SOURCE_LOOKUP_BLOCKED"
        )
        if row["status"] != expected_status:
            raise ValueError(f"source inventory status differs from observed events for {project_id}")
        project_statuses[project_id] = (
            "SOURCE_OBJECT_UNAVAILABLE"
            if acquired and not any((project_id, digest) in verified_pairs for digest in acquired)
            else expected_status
        )
    complete = set(project_statuses) == project_ids and all(events_by_project[project_id] for project_id in project_ids)
    return {
        "status": "FAIL" if any(failure["status"] == "FAIL" for failure in failures) else
                  "PASS" if complete and not failures else "BLOCKED",
        "projects_searched": sum(bool(events_by_project[project_id]) for project_id in project_ids),
        "projects": len(project_ids), "verified_sha256": sorted(verified_sources),
        "eligible_sha256": sorted(eligible_sources), "project_statuses": project_statuses,
        "verified_capture_sha256": sorted(verified_captures), "failures": failures,
    }


def _research_projections(
    profile: Path, out: Path, database: Path, observation_receipt: dict, statuses: dict, metadata: CensusMetadata,
):
    release_id = observation_receipt["release_id"]
    if release_id is None:
        return (
            build_census(profile, out / "census", project_statuses=statuses, metadata=metadata),
            build_decision(profile, out / "decision"),
        )
    with Store(database, trusted_temporal_evidence_ids=observation_receipt["trusted_temporal_evidence_ids"]) as store:
        release = store.get(ResearchRelease, release_id)
        if release.result_fingerprints.get(RELEASE_REGISTRY_PIN) == FORMULA_REGISTRY_SHA256:
            with store.transaction():
                persist_census_treo(store, release)
        return (
            build_census(
                profile, out / "census", store=store, release_id=release_id,
                project_statuses=statuses, metadata=metadata,
            ),
            build_decision(profile, out / "decision", store=store, release_id=release_id),
        )


def build_research(
    *, profile: Path, bindings: Path, sources: Path, assets: Path, out: Path, code_commit: str,
) -> dict:
    """Rebuild fresh extraction, selection, Census and applicable decision outputs offline."""
    if not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("research build requires a full code commit")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("research build requires an empty output directory")
    settings = load_profile(profile)
    registry = load_registry(settings.registry_path)
    columns = load_census_columns(settings.census_columns_path)
    if len(registry.projects) != settings.project_count or len(columns) != settings.census_column_count:
        raise ValueError("profile cohort or columns disagree with their pinned contracts")
    source_bytes = sources.read_bytes()
    source_sha = sha256(source_bytes).hexdigest()
    source_manifest = json.loads(source_bytes)
    source_receipt = verify_source_manifest(settings, source_manifest, registry, ContentStore(assets))
    metadata = prepare_census_metadata(profile, source_manifest, source_receipt, source_manifest_sha256=source_sha)
    extraction_sources = sources
    if source_receipt["failures"]:
        verified = set(source_receipt["verified_capture_sha256"])
        extraction_sources = out / "verified_source_subset.json"
        subset = {**source_manifest, "editions": [
            edition for edition in source_manifest["editions"] if edition["receipt_sha256"] in verified
        ]}
        extraction_sources.write_text(canonical_json(subset) + "\n", encoding="utf-8")
    db = out / "observations" / "research.sqlite"
    extraction = extract_sources(
        bindings, extraction_sources, assets, db, out / "observations", code_commit=code_commit,
        census_derivation_registry_sha256=FORMULA_REGISTRY_SHA256,
    )
    if _sha(sources) != source_sha:
        raise ValueError("source manifest changed during research build")
    census, decision = _research_projections(profile, out, db, extraction, source_receipt["project_statuses"], metadata)
    artifact_hashes = {name: _sha(out / name) for name in ARTIFACTS}
    result = {
        "schema": BUILD_SCHEMA, "profile_id": settings.profile_id, "research_cutoff": settings.research_cutoff.isoformat(),
        "code_commit": code_commit, "project_registry_hash": registry.sha256,
        "census_columns_sha256": _sha(settings.census_columns_path),
        "census_projection_policy_sha256": metadata.policy.sha256,
        "evidence_policy_version": "public-response-capture-v1", "parser_version": extraction["parser"],
        "database_schema": SCHEMA_VERSION, "selection_rules_hash": extraction["rules_sha256"],
        "input_artifacts": {"profile": _sha(profile), "bindings": _sha(bindings), "sources": source_sha},
        "environment": environment_lock(), "scientific_artifacts": artifact_hashes,
        "gates": {"F0": "PASS", "F1": source_receipt["status"], "F2": extraction["status"], "F3": census["status"]},
        "scientific_output_count": census["formal_scientific_cells"],
        "verified_source_count": len(source_receipt["verified_sha256"]),
        "eligible_source_count": len(source_receipt["eligible_sha256"]),
        "source_verification_failures": source_receipt["failures"],
        "mcda_inputs_hash": decision["mcda_inputs_sha256"],
        "mcda_panels": decision["panels"],
        "comparable_project_criteria": decision["comparable_project_criteria"],
        "observation_release_id": extraction["release_id"],
        "observation_counts": {name: extraction[name] for name in ("candidate_count", "validated_count", "selected_count")},
    }
    if "census_derivation_registry_sha256" in extraction:
        result["census_derivation_registry_sha256"] = extraction["census_derivation_registry_sha256"]
    (out / "build_receipt.json").write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result

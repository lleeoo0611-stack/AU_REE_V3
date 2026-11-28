"""Compile source-verified display evidence for an immutable research candidate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .census_display import (
    FACT_ARTIFACT, WEB_ARTIFACTS, DisplayFact, display_csv, display_document, load_display_columns,
)
from .contracts import canonical_json
from .documents import DocumentCache
from .fresh_research import verify_source_manifest
from .publication import load_release
from .registry import load_census_columns, load_profile, load_registry
from .research_delta import ResearchBuild
from .sources import ContentStore

RECEIPT = "display_package_receipt.json"


@dataclass(frozen=True, slots=True)
class DisplayPackage:
    release_id: str
    build_receipt_sha256: str
    public_view: bytes
    public_csv: bytes
    facts_jsonl: bytes
    receipt: dict


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _json(value) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _read(path: Path):
    payload = path.read_bytes()
    return json.loads(payload), _sha(payload)


def _bind_parent(build: ResearchBuild, archive: dict, settings, source_sha: str, binding_sha: str):
    wanted = {"profile": settings.sha256, "sources": source_sha, "bindings": binding_sha}
    if (
        build.receipt["input_artifacts"] != wanted or archive["input_artifacts"] != wanted
        or archive["build_receipt_sha256"] != build.receipt_sha
        or archive["source_manifest_hash"] != source_sha
        or archive["output_hashes"] != build.receipt["scientific_artifacts"]
        or archive["cutoff"] != settings.research_cutoff.isoformat()
        or archive["timezone"] != settings.timezone.key
        or any(build.receipt["gates"].get(key) != "PASS" for key in ("F0", "F1", "F2", "F3"))
    ):
        raise ValueError("Display package requires an exact candidate build, archive and source manifest")


def _replay_attribute_quote(document, anchor):
    from .attribute_batches import NativeRegion

    span = anchor["text_span"]
    page = anchor["page"]
    if span["view"] == "NATIVE_PDF_LAYOUT_REGION":
        region = NativeRegion(page, span["start_line"], span["end_line"], span["start_column"])
        original = region.text(document)
    elif span["view"] == "NATIVE_PDF_WHITESPACE_FOLDED":
        original = " ".join(document.pages[page - 1].split())
    else:
        raise ValueError("Unrecognized quoted attribute evidence view")
    if original[span["start"]:span["end"]] != anchor["quote"] or original.count(anchor["quote"]) != 1:
        raise ValueError("Selected completion quote does not replay at its source locator")
    return {key: str(value) for key, value in span.items()}


def _statement_dates(receipt: dict, editions: dict, content: ContentStore, cache: DocumentCache):
    dates = []
    for row in receipt.get("attribute_assessments", {}).get("cases", ()):
        claim = row.get("assertion", {})
        selected = row.get("selection") or {}
        assessment = row.get("assessment") or {}
        if (
            claim.get("attribute_kind") != "DEVELOPMENT_STAGE" or claim.get("scope_kind") != "PROJECT"
            or claim.get("role") != "COMPLETED" or selected.get("purpose") != "CENSUS_DISPLAY_ONLY"
            or assessment.get("verdict") != "VALIDATED" or assessment.get("temporal_status") != "ELIGIBLE"
            or selected.get("assertion_id") != claim.get("assertion_id")
        ):
            continue
        edition = editions[row["capture_sha256"]]
        if edition["kind"] != "pdf" or claim["source_sha256"] != edition["sha256"]:
            continue
        anchor, = [anchor for anchor in row["evidence"] if anchor["role"] == "DEVELOPMENT_STAGE_CLAIM"]
        if (anchor["anchor_id"] not in claim["evidence_anchor_ids"] or anchor["source_sha"] != edition["sha256"]
                or claim["event_date_token"] not in anchor["quote"]):
            raise ValueError("Selected completion date differs from its attributed original")
        page = anchor["page"]
        document = cache.pdf(content.read(edition["sha256"]), edition["sha256"], page)
        span = _replay_attribute_quote(document, anchor)
        dates.append(DisplayFact(
            project_id=claim["project_id"], field_id="study_effective_or_completion_date", value=claim["event_date"],
            raw_token=claim["event_date_token"], raw_unit="", source_sha256=edition["sha256"],
            source_capture_sha256=row["capture_sha256"], source_url=edition["url"], publication_date=claim["publication_date"],
            locator=anchor["locator"], quote=anchor["quote"], scope=claim["scope"], subject_id=claim["assertion_id"],
            data_role="COMPLETED_STUDY", context={
                "as_reported_at": claim["publication_date"], "source_kind": edition["kind"],
                "temporal_status": "ELIGIBLE", "temporal_decision_id": assessment["temporal_decision_id"],
                "stage": claim["controlled_term"], "event_role": "HISTORICAL_COMPLETION",
                "selection_id": selected["selection_id"], "event_date_token": claim["event_date_token"], **span,
            },
        ))
    return dates


def _edition_join(fact: DisplayFact, editions: dict):
    edition = editions.get(fact.source_capture_sha256)
    if (
        edition is None or fact.project_id not in edition["project_ids"] or fact.source_sha256 != edition["sha256"]
        or fact.source_url not in {edition["url"], edition["final_url"]} or fact.context["source_kind"] != edition["kind"]
    ):
        raise ValueError("Display fact does not bind an eligible exact Project capture and source edition")


def compile_display_package(
    *, profile: Path, columns_path: Path, build_directory: Path, release_root: Path, release_id: str,
    sources: Path, bindings: Path, assets: Path,
) -> DisplayPackage:
    """Rebind display evidence to raw frozen bytes with no Store or decision write path."""
    from .census_resource_display import reported_resource_facts

    settings = load_profile(profile)
    archive = load_release(release_root, release_id)
    build = ResearchBuild.load(build_directory)
    source_manifest, source_sha = _read(sources)
    binding_spec, binding_sha = _read(bindings)
    _bind_parent(build, archive, settings, source_sha, binding_sha)
    registry = load_registry(settings.registry_path)
    core_columns = load_census_columns(settings.census_columns_path)
    columns = load_display_columns(columns_path, core_columns=core_columns)
    project_ids = tuple(project.project_id for project in registry.projects)
    if (project_ids, core_columns) != (build.census_projects, build.census_columns):
        raise ValueError("Display anchor does not keep the exact historical Project and core column order")
    content = ContentStore(assets)
    status = verify_source_manifest(settings, source_manifest, registry, content)
    if status["status"] != "PASS":
        raise ValueError("Display package source verification did not pass for the fixed manifest")
    verified = set(status["verified_capture_sha256"])
    editions = {row["receipt_sha256"]: row for row in source_manifest["editions"]
                if row["receipt_sha256"] in verified and row["temporal_decision"]["status"] == "ELIGIBLE"}
    census_receipt, census_receipt_sha = _read(build_directory / "census/census_receipt.json")
    if census_receipt_sha != archive["output_hashes"]["census/census_receipt.json"]:
        raise ValueError("Display input Census receipt changed after candidate verification")
    cache = DocumentCache()
    resource_diagnostics = []
    resources = reported_resource_facts(
        census_receipt, build.observations.values(), source_manifest, assets=assets, cache=cache,
        diagnostics=resource_diagnostics, bindings=binding_spec, manifest_sha=source_sha,
    )
    dates = _statement_dates(census_receipt, editions, content, cache)
    review = tuple(item["review_evidence"] for item in resource_diagnostics if "review_evidence" in item)
    facts = (*resources, *dates)
    for fact in facts:
        _edition_join(fact, editions)
    document = display_document(release_id=release_id, columns=columns, project_ids=project_ids,
                                facts=facts, review_only=tuple(review))
    view_bytes, csv_bytes = _json(document), display_csv(document)
    facts_bytes = b"".join(_json({"fact_id": key, **record}) for key, record in sorted(document["facts"].items()))
    role_counts = {role: sum(fact.data_role == role for fact in facts) for role in sorted({fact.data_role for fact in facts})}
    receipt = {
        "schema_version": "census-display-package-v2", "status": "PASS", "release_id": release_id,
        "code_commit": archive["code_commit"], "build_receipt_sha256": build.receipt_sha,
        "source_manifest_sha256": source_sha, "source_editions": len(status["verified_sha256"]),
        "verified_source_captures": len(verified), "contract_sha256": document["contract_sha256"],
        "input_files": {"columns": _sha(columns_path.read_bytes())},
        "coverage": document["coverage"], "fact_records": len(facts), "fact_roles": role_counts,
        "distinct_original_subjects": len({(fact.project_id, fact.source_sha256, fact.subject_id) for fact in facts}),
        "distinct_original_editions": len({fact.source_sha256 for fact in facts}),
        "distinct_projects": len({fact.project_id for fact in facts}), "review_only_records": len(review),
        "source_rejections": resource_diagnostics,
        "files": {WEB_ARTIFACTS[0]: _sha(view_bytes), WEB_ARTIFACTS[1]: _sha(csv_bytes), FACT_ARTIFACT: _sha(facts_bytes)},
    }
    return DisplayPackage(release_id, build.receipt_sha, view_bytes, csv_bytes, facts_bytes, receipt)


def write_display_package(package: DisplayPackage, destination: Path) -> dict:
    payloads = {WEB_ARTIFACTS[0]: package.public_view, WEB_ARTIFACTS[1]: package.public_csv,
                FACT_ARTIFACT: package.facts_jsonl, RECEIPT: _json(package.receipt)}
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or any(destination.iterdir()):
        raise ValueError("Display package output requires an empty regular directory")
    for name, payload in payloads.items():
        (destination / name).write_bytes(payload)
    return package.receipt

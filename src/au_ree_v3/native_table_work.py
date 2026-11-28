"""Reread pinned source inventory and close candidate-only table review work."""

from collections import Counter, defaultdict
from contextlib import closing
import csv
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .contracts import Entity, EntityKind, EvidenceAnchor, RawObservation, ResourceScope, Source, canonical_json
from .contracts.temporal import evaluate_temporal
from .documents import DocumentCache, DocumentError, read_asset
from .native_table_discovery import DISCOVERY_RULE, discover_page_rows
from .normalization import KNOWN_OXIDE_SPECIES
from .registry import SHA256, load_profile, load_registry
from .resource_table_binding import DETECTION_RULE, RULE, _baseline_overlap, _plain, _unit_label, bind_page_row
from .source_case_inventory import V2_REF, _git_blob
from .source_inventory import MAX_DOCUMENT_BYTES, _CheckedBody, _verify_edition
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_MANIFEST_SCHEMA
from .store import Store

WORK_RULE = "native-resource-table-work-v1"
_LOCATOR = re.compile(r"PDF page (\d+), line (\d+)")
_NATIVE = "NATIVE_TABLE_TOTAL_ROW"
_REVIEWED = "REVIEWED_BINDING_ORIGINAL_TOKEN"
_UNRECOGNIZED = "UNRECOGNIZED_SOURCE_PROJECT"
_UNBOUND_LEDGER = "unbound_numeric_tokens.jsonl"
_LINEAGE = "value_evidence_lineage.jsonl"
_REVIEW_RESULT = "prior_binding_dispositions.jsonl"
_DISCOVERED = "discovered_native_rows.jsonl"
LEGACY_LANE_SCHEMA = "legacy-resource-source-lane-replay-v1"
COHORT_RECOVERY_SCHEMA = "historical-resource-cohort-recovery-v1"
_LEGACY_STATEMENTS = "results/ree-project-census/coverage-locked-20261125/resource_statement_observations.csv"
_LEGACY_STATEMENTS_SHA = "067e6bf1d8ba46426fb5f16ec7ab404606aa58c181b6ec57445ea6321e97aeb2"
_LEGACY_SPECIES = {item.casefold() for item in KNOWN_OXIDE_SPECIES}


def _digest(data):
    return sha256(data).hexdigest()


def _identity(data):
    return tuple(data[key] for key in ("profile_id", "cutoff", "timezone"))


def _jsonl(content):
    return [json.loads(line) for line in content.splitlines()]


def _load_inputs(settings, inventory, inventory_receipt, sources):
    identity = settings.profile_id, settings.research_cutoff.isoformat(), settings.timezone.key
    cohort = {project.project_id for project in load_registry(settings.registry_path).projects}
    inventory_bytes, receipt_bytes, source_bytes = (Path(path).read_bytes() for path in (inventory, inventory_receipt, sources))
    receipt, manifest = map(json.loads, (receipt_bytes, source_bytes))
    hashes = {name: _digest(content) for name, content in (
        ("source_case_inventory.jsonl", inventory_bytes), ("source_case_inventory_receipt.json", receipt_bytes),
        ("source_manifest.json", source_bytes),
    )}
    if (receipt.get("schema") != "source-case-inventory-v1" or receipt.get("candidate_only") is not True
            or receipt.get("gates", {}).get("SY0") != "PASS"
            or receipt["output_sha256"]["source_case_inventory.jsonl"] != hashes["source_case_inventory.jsonl"]
            or receipt["input_sha256"]["source_manifest_sha256"] != hashes["source_manifest.json"]
            or any(_identity(item) != identity for item in (manifest, receipt))):
        raise ValueError("Native binding inputs do not match the pinned source-case inventory and manifest")
    cases = _jsonl(inventory_bytes)
    if len({row["case_id"] for row in cases}) != len(cases) or any(
        row.get("candidate_only") is not True or row.get("formal_admission") is not False
        or row.get("project_id") not in cohort or row.get("kind") not in {_NATIVE, _REVIEWED, _UNRECOGNIZED}
        or not isinstance(row.get("source_sha256"), str) or not SHA256.fullmatch(row["source_sha256"])
        for row in cases
    ):
        raise ValueError("Native binding inventory violates candidate or Project identity")
    return identity, hashes, cases, manifest


def _load_prior(path, identity, hashes, cases):
    if path is None:
        return None, ()
    prior = Path(path)
    content = (prior / "table_binding_receipt.json").read_bytes()
    receipt = json.loads(content)
    if (receipt.get("schema") != RULE or receipt.get("candidate_only") is not True
            or receipt.get("formal_scientific_values_added") != 0 or receipt.get("status") != "PASS"
            or receipt.get("detection_rule") not in {
                "native-resource-table-detection-v2", "native-resource-table-detection-v3", "native-resource-table-detection-v4",
            }
            or receipt.get("inputs") != hashes or _identity(receipt) != identity):
        raise ValueError("Prior table binding does not match the exact same inventory, manifest and profile")
    ledgers = []
    for name in (_UNBOUND_LEDGER, _LINEAGE):
        rows = (prior / name).read_bytes()
        if _digest(rows) != receipt["outputs"][name]:
            raise ValueError(f"Prior table binding artifact digest mismatch: {name}")
        ledgers.append(_jsonl(rows))
    unbound, lineage = ledgers
    inventory = {case["case_id"]: case for case in cases}
    expected = {case["case_id"] for case in cases if case["kind"] == _UNRECOGNIZED}
    missing = [row for row in lineage if row["status"] == "NO_TABLE_STRUCTURE"]
    prior_structure = [row for row in lineage if row["status"] in {"NO_TABLE_STRUCTURE", "STRUCTURAL_CANDIDATE_LOCATED"}]
    lineage_by_id = {row["case_id"]: row for row in lineage}
    issue_keys = ("project_id", "source_sha256", "edition_id", "source_position_id")
    if (set(lineage_by_id) != set(inventory) or len(lineage) != len(inventory)
            or {row["case_id"] for row in prior_structure} != expected
            or len(missing) != receipt["work_items_without_table_structure"]
            or len({row["case_id"] for row in unbound}) != receipt["unbound_candidate_sightings"]
            or len(unbound) != receipt["unbound_candidate_sightings"] or any(
                row["case_id"] not in inventory or inventory[row["case_id"]]["kind"] != _NATIVE
                or row != lineage_by_id[row["case_id"]]
                for row in unbound) or any(
                    row.get(key) != inventory[row["case_id"]].get(key)
                    for row in (*unbound, *prior_structure) for key in issue_keys)):
        raise ValueError("Prior binding review cases do not match the fixed source inventory")
    return _digest(content), (*unbound, *missing)


def _verified_view(cas, cache, sha, editions, records, identity, timezone):
    try:
        if cas.path(sha).stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "SOURCE_EXCEEDS_DOCUMENT_BYTE_LIMIT")
        content = cas.read(sha)
    except IntegrityError as error:
        raise DocumentError("SCIENTIFIC_CONFLICT", "ORIGINAL_PDF_SHA_MISMATCH") from error
    except OSError as error:
        raise DocumentError("SOURCE_UNAVAILABLE", "ORIGINAL_CAS_PDF_UNAVAILABLE") from error
    reader = _CheckedBody(cas, sha, content)
    eligible = defaultdict(list)
    for edition in editions:
        proof = _verify_edition(edition, reader, identity[1], timezone)
        if proof["status"] == "ELIGIBLE" and edition["kind"] == "pdf":
            for project_id in edition["project_ids"]:
                eligible[project_id, edition["edition_id"]].append(edition)
    if not any(eligible[case["project_id"], case["edition_id"]] for case in records):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "EXACT_ELIGIBLE_PROJECT_EDITION_UNBOUND")
    return cache.pdf(content, sha), eligible


def _result(case, status, reason, **extra):
    return {
        "case_id": case["case_id"], "project_id": case["project_id"], "source_sha256": case["source_sha256"],
        "edition_id": case["edition_id"], "physical_page": case["physical_page"], "table": case["table"],
        "source_position_id": case.get("source_position_id"), "source_token": case["source_token"],
        "candidate_only": True, "formal_admission": False, "status": status, "reason": reason, **extra,
    }


def _candidate(case, cell, editions, baseline):
    details = cell.to_dict()
    details.pop("reason")
    identity = [RULE, case["source_sha256"], case["project_id"], details["value_anchor"], cell.variable, cell.unit]
    item = _result(
        case, "TYPED_CANDIDATE", cell.reason, candidate_id=_digest(canonical_json(identity).encode()),
        source_urls=sorted({edition["url"] for edition in editions}),
        quantity_kind={"ore_mt": "ORE_MASS", "contained_treo_kt": "CONTAINED_OXIDE_MASS", "grade": "GRADE"}
        .get(cell.variable, "OXIDE_COMPOSITION"), material_basis=None, denominator_basis=None,
        estimate_effective_date=None, reporting_standard=None, cutoff_context=None, **details,
    )
    overlap = _baseline_overlap(case, cell, baseline)
    if overlap:
        item.update(status=overlap["status"], baseline_overlap=overlap)
    return item


def _located_cell(view, case, cache):
    page = case["physical_page"]
    row = _LOCATOR.fullmatch(case["row"]["locator"])
    caption = _LOCATOR.fullmatch(case["table"]["locator"])
    if not row or not caption or int(row[1]) != page or int(caption[1]) != page:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "INVENTORY_NATIVE_LOCATOR_UNBOUND")
    raw = view.locate(page, line=int(row[2]))
    if raw.text != case["row"]["text"]:
        raise DocumentError("SCIENTIFIC_CONFLICT", "PINNED_NATIVE_ROW_DIFFERS_FROM_ORIGINAL")
    position = case["token_columns"]
    original = view.locate(page, line=int(row[2]), column=position["start"], end_column=position["end"])
    if original.text != case["source_token"]:
        raise DocumentError("SCIENTIFIC_CONFLICT", "PINNED_NATIVE_TOKEN_DIFFERS_FROM_ORIGINAL")
    key = page, int(row[2]), int(caption[2])
    if key not in cache:
        cache[key] = {(value.value.start_column, value.value.end_column): value
                      for value in bind_page_row(view, page=page, row_line=key[1], caption_line=key[2])}
    bound = cache[key].get((position["start"], position["end"]))
    if bound is None:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "NATIVE_TOKEN_NOT_A_UNIQUE_NUMERIC_CELL")
    return bound


def _unlocated(case, status, reason, *, pages=(), row_count=0):
    return _result(case, status, reason, physical_pages_inspected=tuple(pages),
                   prior_route_status=case.get("prior_route_status"), candidate_row_count=row_count)


def _discovered_cell(view, route, found, cell, reviewed, used_positions):
    page = found.physical_page
    raw = view.locate(page, line=found.row_line).text
    source_position = _digest(canonical_json([view.source_sha256, found.row_locator, cell.value.start_column,
                                              cell.value.end_column, cell.value.text]).encode())
    shared = [key for key, old in reviewed.items() if old["source_sha256"] == view.source_sha256
              and old["project_id"] == route["project_id"] and old["physical_page"] == page
              and old["source_token"] == cell.value.text and _plain(old["row"]["text"]) == _plain(raw)]
    case = {
        **route, "source_position_id": source_position, "physical_page": page, "source_token": cell.value.text,
        "row": {"locator": found.row_locator, "text": raw},
        "table": {"locator": view.locate(page, line=found.caption_line).locator},
        "existing_reviewed_row_lexeme_overlap": sorted(shared),
    }
    item = _candidate(case, cell, route["matched_editions"], reviewed)
    if (route["project_id"], source_position) in used_positions and item["status"] == "TYPED_CANDIDATE":
        item["status"] = "ALREADY_LOCATED_CANDIDATE_POSITION"
    return item


def _structure_review(view, case, editions, page_cache, reviewed, used_positions):
    # Search the exact edition once, sharing each page's discovery across its
    # Project routes. Native locators stay distinct from routing affiliation.
    if not page_cache:
        page_cache.update({page: discover_page_rows(view, page=page) for page in range(1, len(view.pages) + 1)})
    pages = range(1, len(view.pages) + 1)
    findings = [{
        "source_sha256": view.source_sha256, "project_id": case["project_id"], "edition_id": case["edition_id"],
        "case_id": case["case_id"], "strategy": found.strategy, "row_locator": found.row_locator,
        "physical_page": found.physical_page, "caption_line": found.caption_line,
        "candidate_only": True, "formal_admission": False, "statement_scope_status": "UNBOUND",
        **({"context_anchors": [anchor.to_dict() for anchor in found.context_anchors]} if found.context_anchors else {}),
        "cells": [_discovered_cell(view, {**case, "matched_editions": editions}, found, cell, reviewed, used_positions)
                  for cell in found.cells if cell.variable],
        "unbound_cells": [cell.to_dict() for cell in found.cells if cell.variable is None],
    } for found_rows in page_cache.values() for found in found_rows]
    if findings:
        return _unlocated(case, "STRUCTURAL_CANDIDATE_LOCATED", "NATIVE_RESOURCE_GRID_FOUND_SCOPE_UNVERIFIED",
                          pages=pages, row_count=len(findings)), findings
    native = any(page.strip() for page in view.pages)
    reason = "NO_CONFIRMED_NATIVE_RESOURCE_GRID" if native else "NATIVE_PDF_TEXT_UNAVAILABLE"
    return _unlocated(case, "NO_TABLE_STRUCTURE", reason, pages=pages), []


def _document_work(view, records, eligible, reviewed):
    row_cache, page_cache = {}, {}
    lineage, discovered = [], []
    used_positions = {(case["project_id"], case["source_position_id"]) for case in records if case["kind"] == _NATIVE}
    for case in records:
        editions = eligible[case["project_id"], case["edition_id"]]
        try:
            if not editions:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "EXACT_ELIGIBLE_PROJECT_EDITION_UNBOUND")
            if case["kind"] == _NATIVE:
                cell = _located_cell(view, case, row_cache)
                result = (_candidate(case, cell, editions, reviewed) if cell.variable else
                          _result(case, "EVIDENCE_INSUFFICIENT", cell.reason, native_evidence=cell.to_dict(),
                                  token_columns=case["token_columns"]))
            else:
                result, found = _structure_review(view, case, editions, page_cache, reviewed, used_positions)
                discovered.extend(found)
        except DocumentError as error:
            result = _result(case, error.outcome, str(error), token_columns=case.get("token_columns"))
        lineage.append(result)
    return lineage, discovered, len(page_cache)


def _previous_dispositions(previous, lineage):
    by_key = {row["case_id"]: row for row in lineage}
    return [{
        **{key: by_key[row["case_id"]][key] for key in ("case_id", "source_sha256", "project_id", "edition_id",
                                                        "candidate_only", "formal_admission", "status", "reason")},
        "prior_status": row["status"], "prior_reason": row.get("reason"),
        "source_position_id": by_key[row["case_id"]].get("source_position_id"),
        "candidate_id": by_key[row["case_id"]].get("candidate_id"),
        "candidate_row_count": by_key[row["case_id"]].get("candidate_row_count", 0),
    } for row in previous]


def _write_outputs(out, report, outputs):
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".table-binding-", dir=out) as temporary:
        stage = Path(temporary)
        for name, rows in outputs.items():
            (stage / name).write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")
        report["outputs"] = {name: _digest((stage / name).read_bytes()) for name in outputs}
        (stage / "table_binding_receipt.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
        for name in (*outputs, "table_binding_receipt.json"):
            (stage / name).replace(out / name)
    return report


def _summarize(identity, hashes, cases, lineage, discovered, documents, prior_digest, dispositions, located_only):
    baseline_statuses = {"EXISTING_REVIEWED_CELL_OVERLAP", "BASELINE_ROW_LEXEME_MATCH_UNRESOLVED"}
    native_cases = {row["case_id"] for row in cases if row["kind"] == _NATIVE}
    native = [row for row in lineage if row["case_id"] in native_cases]
    typed = [row for row in native if row["status"] == "TYPED_CANDIDATE"]
    overlaps = [row for row in native if row["status"] in baseline_statuses]
    unbound = [row for row in native if row["status"] not in baseline_statuses | {"TYPED_CANDIDATE"}]
    discovered_cells = [item for row in discovered for item in row["cells"] if item["status"] == "TYPED_CANDIDATE"]
    report = {
        "schema": RULE, "work_rule": WORK_RULE, "detection_rule": DETECTION_RULE, "discovery_rule": DISCOVERY_RULE,
        "profile_id": identity[0], "cutoff": identity[1], "timezone": identity[2], "inputs": hashes,
        "candidate_only": True, "formal_scientific_values_added": 0, "located_only": located_only,
        "status": "FAIL" if any(row["status"] in {"SCIENTIFIC_CONFLICT", "CODE_ERROR"} for row in lineage) else
                  "BLOCKED" if any(row["status"] == "SOURCE_UNAVAILABLE" for row in lineage) else "PASS",
        "source_documents_verified_and_parsed": len(documents), "source_documents": documents,
        "case_count": len(cases), "distinct_native_source_positions": len({row["source_position_id"] for row in native}),
        "resolved_typed_sightings": len(typed) + len(overlaps),
        "distinct_resolved_native_positions": len({row["source_position_id"] for row in (*typed, *overlaps)}),
        "typed_candidate_sightings": len(typed), "distinct_typed_native_positions": len({row["source_position_id"] for row in typed}),
        "existing_reviewed_exact_cell_overlaps": sum(row["status"] == "EXISTING_REVIEWED_CELL_OVERLAP" for row in overlaps),
        "unresolved_baseline_row_lexeme_matches": sum(row["status"] == "BASELINE_ROW_LEXEME_MATCH_UNRESOLVED" for row in overlaps),
        "distinct_project_candidates": len({row["candidate_id"] for row in typed}),
        "unbound_candidate_sightings": len(unbound),
        "resolved_by_layout_method": dict(Counter(row.get("layout_method", "BASELINE_NATIVE_ALIGNMENT")
                                                 for row in (*typed, *overlaps))),
        "work_items_without_table_structure": sum(row["status"] == "NO_TABLE_STRUCTURE" for row in lineage),
        "status_by_case": dict(Counter(row["status"] for row in lineage)),
        "issue_by_case": dict(Counter(row["reason"] for row in unbound)),
        "discovered_resource_row_sightings": len(discovered),
        "distinct_discovered_candidate_positions": len({item["source_position_id"] for item in discovered_cells}),
        "prior_binding_receipt_sha256": prior_digest, "prior_review_case_count": len(dispositions),
        "prior_review_status_counts": dict(Counter(row["status"] for row in dispositions)),
        "ocr_status": "NOT_RUN",
    }
    return report, {
        "typed_candidate_observations.jsonl": typed, _UNBOUND_LEDGER: unbound, _LINEAGE: lineage,
        _DISCOVERED: discovered, _REVIEW_RESULT: dispositions,
    }


def bind_source_inventory(*, profile, inventory, inventory_receipt, sources, assets, out,
                          prior_binding=None, located_only=False):
    """Reread original PDFs once per SHA and keep every prior review outcome explicit."""
    out, assets = Path(out), Path(assets)
    if (out.is_symlink() or out.resolve().is_relative_to(assets.resolve())
            or assets.resolve().is_relative_to(out.resolve()) or (out.exists() and (not out.is_dir() or any(out.iterdir())))):
        raise ValueError("Native table binding requires a new output directory separate from the source store")
    settings = load_profile(profile)
    identity, hashes, cases, manifest = _load_inputs(settings, inventory, inventory_receipt, sources)
    prior_digest, previous = _load_prior(prior_binding, identity, hashes, cases)
    by_sha, catalog = defaultdict(list), defaultdict(list)
    lineage, discovered, documents = [], [], []
    reviewed = {row["existing_observation_id"]: row for row in cases if row["kind"] == _REVIEWED}
    for case in cases:
        if case["kind"] == _REVIEWED:
            lineage.append(_result(case, "EXISTING_REVIEWED_TOKEN", "EXISTING_FROZEN_REVIEWED_MEASUREMENT"))
        elif located_only and case["kind"] == _UNRECOGNIZED:
            lineage.append(_unlocated(case, "NO_TABLE_STRUCTURE", "STRUCTURAL_REDISCOVERY_NOT_REQUESTED",
                                      pages=case.get("physical_pages_inspected", ())))
        else:
            by_sha[case["source_sha256"]].append(case)
    for edition in manifest["editions"]:
        if edition["sha256"] in by_sha:
            catalog[edition["sha256"]].append(edition)
    cas = ContentStore(assets)
    for sha, records in sorted(by_sha.items()):
        # A cache is retained within this document only so full native page
        # text can be released before the next SHA is opened.
        try:
            view, eligible = _verified_view(cas, DocumentCache(), sha, catalog[sha], records, identity, settings.timezone)
        except DocumentError as error:
            lineage.extend(_result(case, error.outcome, str(error)) for case in records)
            continue
        source_lineage, found, scanned = _document_work(view, records, eligible, reviewed)
        lineage.extend(source_lineage)
        discovered.extend(found)
        documents.append({"source_sha256": sha, "native_pages_decoded": len(view.pages),
                          "native_pages_searched_for_structure": scanned, "runtime": view.runtime})
    dispositions = _previous_dispositions(previous, lineage)
    report, outputs = _summarize(identity, hashes, cases, lineage, discovered, documents,
                                 prior_digest, dispositions, located_only)
    if len(documents) != len(by_sha) and report["status"] == "PASS":
        report["status"] = "BLOCKED"
    return _write_outputs(out, report, outputs)


def _legacy_lane(hint):
    """Treat historical token, measurement role and unit solely as lookup discriminators."""
    variable, basis = hint["variable"], hint["basis"].strip().casefold()
    if variable in {"ore_mt", "reported_resource_ore_mt"}:
        return "ore_mt"
    if basis == "treo" and variable in {"grade", "reported_resource_grade", "reported_resource_grade_ppm",
                                         "reported_resource_grade_pct", "contained_treo_kt"}:
        return "contained_treo_kt" if variable == "contained_treo_kt" else "grade"
    if basis in _LEGACY_SPECIES and variable.endswith("_ppm"):
        return f"{basis}_ppm"
    return None


def _legacy_hold(hint, reason):
    return {"legacy_hint_id": hint["observation_id"], "project_id": hint["project_id"],
            "source_sha256": hint["source_sha256"], "physical_page": _legacy_page(hint), "reason": reason}


def _legacy_page(hint):
    value = hint["physical_page"]
    return int(value) if value.isdecimal() and int(value) > 0 else None


def _legacy_page_cells(document, pages):
    by_lane = defaultdict(dict)
    for page in pages:
        for row in discover_page_rows(document, page=page):
            for cell in row.cells:
                if cell.variable and cell.header and cell.unit_label and cell.value.physical_page == page:
                    key = page, cell.variable, cell.unit, cell.value.text
                    by_lane[key][cell.value.locator] = row, cell
    return by_lane


def _legacy_cell(hint, document, index, editions):
    if not editions:
        return None, _legacy_hold(hint, "EXACT_CAPTURE_OR_TEMPORAL_PROOF_UNBOUND")
    if hint["source_transform"]:
        return None, _legacy_hold(hint, "HISTORICAL_TRANSFORM_REQUIRES_SEPARATE_REVIEW")
    if not hint["original_token"]:
        return None, _legacy_hold(hint, "ORIGINAL_TOKEN_HINT_MISSING")
    page = _legacy_page(hint)
    if page is None or page > len(document.pages):
        return None, _legacy_hold(hint, "PHYSICAL_PAGE_UNAVAILABLE")
    lane, unit = _legacy_lane(hint), _unit_label(hint["original_unit"])
    if not lane or not unit:
        reason = "ORIGINAL_UNIT_HINT_MISSING" if not unit else "MEASUREMENT_ROLE_OUTSIDE_REPLAY"
        return None, _legacy_hold(hint, reason)
    cells = index.get((page, lane, unit, hint["original_token"]), {})
    if len(cells) != 1:
        reason = "AMBIGUOUS_TYPED_NATIVE_POSITION" if cells else "TYPED_LANE_NOT_LOCATED"
        return None, _legacy_hold(hint, reason)
    row, cell = next(iter(cells.values()))
    edition = min(editions, key=lambda value: (value["acquired_at"], value["edition_id"]))
    return _recovered_cell(hint["project_id"], document, row, cell, edition), None


def _recovered_cell(project_id, document, row, cell, edition):
    page = cell.value.physical_page
    span = document.locate(page, line=row.row_line)
    identity = [project_id, document.source_sha256, cell.value.locator]
    return {
        **cell.to_dict(), "candidate_id": _digest(canonical_json(identity).encode()),
        "project_id": project_id, "source_sha256": document.source_sha256,
        "source_url": edition["url"], "physical_page": page, "source_edition_id": edition["edition_id"],
        "source_capture_sha256": edition["receipt_sha256"],
        "temporal_decision_id": edition["temporal_decision"]["decision_id"],
        "row_anchor": {"locator": span.locator, "text": span.text},
        "context_anchors": [anchor.to_dict() for anchor in row.context_anchors],
        "scope_status": "UNBOUND", "state": "CANDIDATE", "candidate_only": True, "formal_admission": False,
    }


def _legacy_document(store, digest, hints, editions, identity, timezone, *, fallback=None):
    projects = {hint["project_id"] for hint in hints}
    records = [{"project_id": project, "edition_id": edition["edition_id"]}
               for edition in editions for project in projects.intersection(edition["project_ids"])]
    cache = DocumentCache()
    if editions:
        document, verified = _verified_view(store, cache, digest, editions, records, identity, timezone)
    else:
        document = cache.pdf(read_asset(fallback["source"], fallback["assets"]), digest)
        verified = {}
    by_project = defaultdict(list)
    for (project, edition), captures in verified.items():
        by_project[project].extend(captures)
    if fallback:
        for project in projects - by_project.keys():
            by_project[project].append(fallback["edition"])
    pages = sorted({page for hint in hints if (page := _legacy_page(hint)) and page <= len(document.pages)})
    index = _legacy_page_cells(document, pages)
    found, holds = [], []
    for hint in hints:
        candidate, hold = _legacy_cell(hint, document, index, by_project[hint["project_id"]])
        if hold:
            holds.append(hold)
        else:
            found.append((candidate, hint["observation_id"]))
    receipt = {"source_sha256": digest, "native_pages_decoded": len(document.pages),
               "nominated_pages_read": pages, "runtime": document.runtime}
    return found, holds, receipt, index, document, by_project


def replay_legacy_source_lanes(*, v2_root: Path, sources: Path, assets: Path) -> dict:
    """Replay native resource/oxide lanes against the frozen V2 locator hints, without writes."""
    body = _git_blob(Path(v2_root), _LEGACY_STATEMENTS)
    if body is None or _digest(body) != _LEGACY_STATEMENTS_SHA:
        raise ValueError("Frozen V2 statement observation input is missing or has different bytes")
    hints = list(csv.DictReader(StringIO(body.decode("utf-8-sig"))))
    manifest_bytes = Path(sources).read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != SOURCE_MANIFEST_SCHEMA:
        raise ValueError("Native lane replay requires the V3 source manifest contract")
    identity, store = _identity(manifest), ContentStore(Path(assets))
    timezone = ZoneInfo(manifest["timezone"])
    catalog, grouped, available = defaultdict(list), defaultdict(list), set()
    for edition in manifest["editions"]:
        if edition["kind"] == "pdf" and edition["temporal_decision"]["status"] == "ELIGIBLE":
            catalog[edition["sha256"]].append(edition)
            available.update((project, edition["sha256"]) for project in edition["project_ids"])
    holds, observations, documents = [], {}, []
    for hint in hints:
        if (hint["project_id"], hint["source_sha256"]) in available:
            grouped[hint["source_sha256"]].append(hint)
        else:
            holds.append(_legacy_hold(hint, "NO_ELIGIBLE_EXACT_PROJECT_PDF"))
    failed = []
    for digest, group in sorted(grouped.items()):
        try:
            found, unresolved, receipt, _, _, _ = _legacy_document(
                store, digest, group, catalog[digest], identity, timezone,
            )
        except DocumentError as error:
            failed.append({"source_sha256": digest, "status": error.outcome, "reason": str(error)})
            holds.extend(_legacy_hold(hint, str(error)) for hint in group)
            continue
        holds.extend(unresolved)
        documents.append(receipt)
        for candidate, hint_id in found:
            record = observations.setdefault(candidate["candidate_id"], {**candidate, "legacy_hint_ids": []})
            record["legacy_hint_ids"].append(hint_id)
    counts = {"legacy_hints": len(hints), "exact_manifest_project_pdf_join": sum(map(len, grouped.values())),
              "pdfs_read": len(documents), "nominated_pages_read": sum(len(doc["nominated_pages_read"]) for doc in documents),
              "native_pages_decoded": sum(doc["native_pages_decoded"] for doc in documents),
              "matching_legacy_hints": sum(len(record["legacy_hint_ids"]) for record in observations.values()),
              "candidate_positions": len(observations), "candidate_projects": len({r["project_id"] for r in observations.values()}),
              "holds_by_reason": dict(Counter(hold["reason"] for hold in holds)), "formal_admissions": 0}
    proof_missing = counts["holds_by_reason"].get("EXACT_CAPTURE_OR_TEMPORAL_PROOF_UNBOUND")
    status = "FAIL" if any(row["status"] == "SCIENTIFIC_CONFLICT" for row in failed) else (
        "BLOCKED" if failed or proof_missing or not documents else "PASS"
    )
    return {"schema": LEGACY_LANE_SCHEMA, "status": status, "profile_id": identity[0], "cutoff": identity[1],
            "timezone": identity[2], "source_manifest_sha256": _digest(manifest_bytes), "v2_archive_ref": V2_REF,
            "v2_observations_sha256": _LEGACY_STATEMENTS_SHA, "candidate_only": True, "counts": counts,
            "observations": list(observations.values()), "holds": holds, "documents": documents, "failed_documents": failed}


def _cohort_inventory(v2_root, registry):
    from .migration.baseline import PinnedRepository, checked_csv
    from .migration.project_registry import ASSETS, CENSUS, PROVENANCE, RELEASE, _archive_inputs

    pinned = _archive_inputs(v2_root)
    historical = checked_csv(pinned[CENSUS])
    provenance = checked_csv(pinned[PROVENANCE])
    wanted = {
        (row["專案ID"], column): value
        for row in historical for column, value in row.items()
    }
    if ({p.project_id for p in registry.projects} != {row["專案ID"] for row in historical}
            or len(wanted) != len(provenance) or len(wanted) != 4743
            or {(row["project_id"], row["column"]): row["value"] for row in provenance} != wanted):
        raise ValueError("Historical and current Project or Census axes differ")
    catalog = defaultdict(list)
    for row in json.loads(pinned[ASSETS]):
        catalog[row["sha256"]].append(row)
    claims = PinnedRepository(v2_root).read("science", "data/claims.json")
    if claims is None or _digest(claims) != json.loads(pinned[RELEASE])["claims_sha256"]:
        raise ValueError("Frozen claim extraction configuration differs")
    return provenance, catalog, {**{name: _digest(value) for name, value in pinned.items()},
                                 "data/claims.json": _digest(claims)}, json.loads(claims)


def _historical_source(versions, sha, assets, cutoff):
    records = sorted(
        (row for row in versions if row["role"] == "snapshot" and row["kind"] == "pdf"
         and row["url"].startswith("https://")), key=lambda row: (row["key"], row["url"]),
    )
    if not records:
        return None
    source = Source(source_key=f"historical-pdf-{sha}", sha256=sha, url=records[0]["url"],
                    kind="pdf", source_version=sha)
    decision = evaluate_temporal(source_sha256=sha, cutoff=cutoff, observation_role="DOCUMENT")
    return {"source": source, "assets": assets, "edition": {
        "url": source.url, "edition_id": source.source_key, "receipt_sha256": None,
        "temporal_decision": decision.to_dict(), "acquired_at": "",
    }}


def _cohort_lanes(hints, catalog, manifest, store, historical_assets, inventory):
    identity, timezone = _identity(manifest), ZoneInfo(manifest["timezone"])
    current = defaultdict(list)
    for entry in manifest["editions"]:
        if entry["kind"] == "pdf" and entry["temporal_decision"]["status"] == "ELIGIBLE":
            current[entry["sha256"]].append(entry)
    grouped = defaultdict(list)
    for hint in hints:
        grouped[hint["source_sha256"]].append(hint)
    documents, holds, observations, views = [], [], {}, {}
    for sha, group in sorted(grouped.items()):
        fallback = _historical_source(catalog[sha], sha, historical_assets, identity[1])
        editions = [e for e in current[sha] if set(e["project_ids"]) & {h["project_id"] for h in group}]
        if fallback is None and not editions:
            holds.extend(_legacy_hold(item, "HISTORICAL_PDF_VERSION_NOT_REGISTERED") for item in group)
            continue
        try:
            found, unresolved, receipt, index, document, by_project = _legacy_document(
                store, sha, group, editions, identity, timezone, fallback=fallback,
            )
        except DocumentError as error:
            holds.extend(_legacy_hold(item, str(error)) for item in group)
            continue
        documents.append(receipt)
        views[sha] = document
        holds.extend(unresolved)
        row_locations = {}
        for candidate, hint_id in found:
            record = observations.setdefault(candidate["candidate_id"], {**candidate, "legacy_hint_ids": []})
            record["legacy_hint_ids"].append(hint_id)
            row_key = candidate["physical_page"], candidate["row_anchor"]["locator"]
            row_locations.setdefault(row_key, set()).add(candidate["project_id"])
        for cells in index.values():
            for row, cell in cells.values():
                for project in sorted(row_locations.get((row.physical_page, row.row_locator), ())):
                    edition = min(by_project[project], key=lambda x: (x["acquired_at"], x["edition_id"]))
                    record = _recovered_cell(project, document, row, cell, edition)
                    observations.setdefault(record["candidate_id"], {**record, "legacy_hint_ids": []})
        inventory[sha] = "NATIVE_PDF_REREAD"
    return observations, holds, documents, views


def _verify_cohort_bytes(historical_assets, store, catalog, provenance, inventory):
    """Verify content availability throughout the Census, including external data."""
    for row in provenance:
        for sha in filter(None, row["source_sha256"].split(";")):
            if sha in inventory:
                continue
            try:
                if store.path(sha).is_file():
                    store.read(sha)
                else:
                    versions = catalog[sha]
                    raw = versions[0]
                    read_asset(Source(source_key=raw["key"], url=raw["url"], sha256=sha,
                                      kind=raw["kind"], source_version=sha), historical_assets)
            except (DocumentError, IntegrityError, OSError, IndexError) as error:
                inventory[sha] = "SOURCE_SHA_CONFLICT" if (
                    isinstance(error, IntegrityError) or getattr(error, "outcome", None) == "SCIENTIFIC_CONFLICT"
                ) else "EXACT_BYTES_UNAVAILABLE"
            else:
                inventory[sha] = "EXACT_BYTES_AVAILABLE"


def _write_cohort_store(database, registry, records, store, historical_assets):
    with Store(database) as output, output.transaction():
        for project in registry.projects:
            output.add(Entity(entity_id=project.project_id, kind=EntityKind.PROJECT,
                              native_namespace="v3-project-registry", native_id=project.project_id, name=project.name))
        sources = {}
        for record in sorted(records.values(), key=lambda row: row["candidate_id"]):
            sha = record["source_sha256"]
            if sha not in sources:
                source = Source(source_key=f"historical-recovery-{sha}", url=record["source_url"], sha256=sha,
                                kind="pdf", source_version=sha)
                content = store.read(sha) if record["source_capture_sha256"] else read_asset(source, historical_assets)
                output.add_source(source, content)
                sources[sha] = source
            source = sources[sha]
            project = record["project_id"]
            row_loc = record["row_anchor"]["locator"]
            scope_id = _digest(canonical_json([COHORT_RECOVERY_SCHEMA, project, sha, row_loc]).encode())
            output.add(ResourceScope(scope_id=scope_id, project_id=project, name="Original table row; scope under review"))
            anchor = EvidenceAnchor(anchor_id=record["candidate_id"], source_key=source.source_key, source_sha=sha,
                                    locator=record["value_anchor"]["locator"], page=record["physical_page"],
                                    quote=record["value_anchor"]["text"], role="HISTORICAL_NATIVE_TABLE_TOKEN")
            output.add(anchor)
            output.add(RawObservation(observation_id=record["candidate_id"], project_id=project, scope_id=scope_id,
                                      source_key=source.source_key, source_sha=sha, variable=record["variable"],
                                      raw_value=anchor.quote, raw_unit=record["native_unit"], status="CANDIDATE",
                                      transform_version=COHORT_RECOVERY_SCHEMA, original_payload=record,
                                      anchor_id=anchor.anchor_id, chemical_basis=record["chemical_basis"]))
        return output.integrity()


def recovered_cohort_records(sqlite_bytes):
    """Read the pinned snapshot in memory and verify each persisted provenance join."""
    query = """SELECT raw.payload, anchor.payload FROM raw_observations AS raw
               JOIN anchors AS anchor ON anchor.anchor_id = raw.anchor_id
                 AND anchor.source_key = raw.source_key AND anchor.source_sha = raw.source_sha
               JOIN verified_sources AS verified ON verified.source_key = raw.source_key
                 AND verified.sha256 = raw.source_sha
               ORDER BY raw.observation_id"""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.deserialize(sqlite_bytes)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(query).fetchall()
        if len(rows) != connection.execute("SELECT count(*) FROM raw_observations").fetchone()[0]:
            raise ValueError("Historical cohort SQLite is missing an exact source or anchor join")
    for raw_json, anchor_json in rows:
        raw, anchor = json.loads(raw_json), json.loads(anchor_json)
        cell = json.loads(raw["original_payload"])
        position = cell["value_anchor"]
        candidate = _digest(canonical_json([cell["project_id"], cell["source_sha256"], position["locator"]]).encode())
        if (candidate != cell["candidate_id"] or not cell["candidate_only"] or cell["formal_admission"]
                or raw["status"] != "CANDIDATE" or raw["observation_id"] != candidate
                or raw["anchor_id"] != anchor["anchor_id"] or anchor["anchor_id"] != candidate
                or raw["project_id"] != cell["project_id"] or raw["source_sha"] != cell["source_sha256"]
                or raw["variable"] != cell["variable"] or raw["raw_unit"] != cell["native_unit"]
                or raw["raw_value"] != position["text"] or anchor["quote"] != position["text"]
                or anchor["page"] != cell["physical_page"] or anchor["locator"] != position["locator"]):
            raise ValueError("Historical cohort raw observation differs from its evidence anchor")
        yield cell


def _cohort_cells(provenance, inventory, observations, text, government):
    recovered = defaultdict(set)
    for record in observations.values():
        for hint in record["legacy_hint_ids"]:
            recovered[hint].add(record["candidate_id"])
    cells = []
    for row in provenance:
        shas = {sha: inventory[sha] for sha in filter(None, row["source_sha256"].split(";"))}
        hits = sorted({item for hint in json.loads(row["observation_ids"]) for item in recovered[hint]})
        geographic = government.get(row["project_id"]) if row["column"] in {"經度", "緯度"} else None
        cells.append({
            "project_id": row["project_id"], "column": row["column"], "historical_method": row["method"],
            "historical_present": bool(row["value"]), "source_sha256": shas, "source_url": row["source_url"],
            "locator": row["locator"], "original_anchor_ids": hits,
            "candidate_text_evidence": text.get((row["project_id"], row["column"])),
            "candidate_government_point": geographic,
        })
    return cells


def _text_hints(claims, provenance):
    from .observations import digest

    keys = {(r["project_id"], r["column"], r["source_sha256"], r["locator"]) for r in provenance if r["value"]}
    grouped = defaultdict(list)
    for record in claims["projects"]:
        project = "AU-" + digest([record.get("entity_namespace", "project_registry_v1"), str(record["id"])])[:20]
        for entry in record.get("attributes", ()):
            sha = entry["source_sha256"]
            cell = project, entry["column"], sha, entry["locator"]
            page = entry.get("pdf_page")
            quotes = tuple(dict.fromkeys(" ".join(text.split()) for text in entry.get("page_markers", ()) if len(text.strip()) >= 3))
            if cell in keys and type(page) is int and page > 0 and len(quotes) >= 2:
                grouped[sha].append((cell, page, quotes))
    return grouped


def _cohort_quote_anchors(document, source_key, page, quotes):
    from .development import bind_native_quote

    try:
        return [bind_native_quote(document, source_key, page, quote, "ARCHIVED_TEXT_CANDIDATE") for quote in quotes]
    except DocumentError:
        # A heading or identifier may repeat on one page. Require a unique
        # original paragraph containing the entire bundle of nominated phrases.
        blocks = [block for block in document.blocks(page)
                  if all(quote in " ".join(block.text.split()) for quote in quotes)]
        if len(blocks) != 1:
            raise
    return [bind_native_quote(document, source_key, page, blocks[0].text, "ARCHIVED_TEXT_COLOCATED_QUOTE")]


def _cohort_text_captures(store, editions, sha, cutoff, timezone):
    if not editions or not store.path(sha).is_file():
        return {}
    checked = _CheckedBody(store, sha, store.read(sha))
    captures = {}
    for edition in sorted(editions, key=lambda row: (row["acquired_at"], row["edition_id"])):
        proof = _verify_edition(edition, checked, cutoff, timezone)
        if proof["status"] == "ELIGIBLE":
            for project in edition["project_ids"]:
                captures.setdefault(project, edition)
    return captures


def _rebind_cohort_text(claims, provenance, store, historical_assets, catalog, manifest, views):
    records = {}
    holds = Counter()
    cutoff, timezone = manifest["cutoff"], ZoneInfo(manifest["timezone"])
    editions = defaultdict(list)
    for edition in manifest["editions"]:
        if edition["kind"] == "pdf":
            editions[edition["sha256"]].append(edition)
    for sha, entries in _text_hints(claims, provenance).items():
        source = _historical_source(catalog[sha], sha, historical_assets, cutoff)
        if source is None:
            holds["ORIGINAL_QUOTE_SOURCE_NOT_PDF"] += len(entries)
            continue
        try:
            if sha in views:
                holds["PDF_VIEW_REUSED"] += 1
                document = views[sha]
            else:
                body = store.read(sha) if store.path(sha).is_file() else read_asset(source["source"], historical_assets)
                document = DocumentCache().pdf(body, sha)
        except (DocumentError, IntegrityError, OSError) as error:
            holds[str(error)] += len(entries)
            continue
        try:
            captures = _cohort_text_captures(store, editions[sha], sha, cutoff, timezone)
        except (IntegrityError, OSError):
            captures = {}
        for (project, column, _, _), page, quotes in entries:
            edition = captures.get(project)
            try:
                anchors = _cohort_quote_anchors(document, source["source"].source_key, page, quotes)
            except DocumentError as error:
                holds[str(error)] += 1
                continue
            evidence = {
                "state": "CANDIDATE", "source_sha256": sha, "source_url": source["source"].url,
                "anchors": [anchor.to_dict() for anchor in anchors], "scope_status": "UNBOUND",
                "temporal_decision": source["edition"]["temporal_decision"],
            }
            if edition is not None:
                evidence.update(source_capture_sha256=edition["receipt_sha256"],
                                source_edition_id=edition["edition_id"], source_url=edition["url"],
                                temporal_decision=edition["temporal_decision"])
            records[project, column] = evidence
    reused = holds.pop("PDF_VIEW_REUSED", 0)
    return records, holds, reused


def _cohort_formula_checks(records):
    from .derivations import registered_formula
    from .observations import exact_value

    groups = defaultdict(list)
    for row in records.values():
        groups[row["project_id"], row["source_sha256"], row["row_anchor"]["locator"]].append(row)
    results = []
    for (project, sha, locator), row in sorted(groups.items()):
        masses = [r for r in row if (r["variable"], r["native_unit"]) == ("ore_mt", "Mt")]
        grades = [r for r in row if (r["variable"], r["chemical_basis"]) == ("grade", "TREO")
                  and r["native_unit"] in {"ppm", "%"}]
        if len(masses) != 1 or len(grades) != 1:
            continue
        mass, grade = masses[0], grades[0]
        formula = registered_formula(f"contained-treo-from-mt-{'ppm' if grade['native_unit'] == 'ppm' else 'pct'}-v1")
        amounts = [exact_value(item["value_anchor"]["text"]) for item in (mass, grade)]
        results.append({"project_id": project, "source_sha256": sha, "row_locator": locator, "layer": "SHADOW",
                        "reason": "SAME_ROW_ARITHMETIC_MATERIAL_AND_SCOPE_UNVERIFIED",
                        "input_observation_ids": [mass["candidate_id"], grade["candidate_id"]],
                        "formula_id": formula.formula_id, "formula_sha256": formula.sha256,
                        "value": str(amounts[0] * amounts[1] * Decimal(formula.factor)), "unit": "kt"})
    return results


def _replay_cohort_government(v2_root, geography):
    if geography is None:
        return {}, None
    from .official_geography import replay_historical_points

    prior, store = geography
    payload = prior.read_bytes()
    receipt = replay_historical_points(v2_root, store, prior=json.loads(payload))
    points = {row["project_id"]: row for row in receipt["points"] if row["geometry"] is not None}
    return points, {"input_receipt_sha256": _digest(payload), "status": receipt["status"], "counts": receipt["counts"]}


def recover_historical_cohort(*, v2_root, profile, sources, assets, historical_assets, database, geography=None):
    """Rebuild source routes across the fixed Census and materialize original tokens for review."""
    if database.resolve().is_relative_to(v2_root.resolve()) or database.exists():
        raise ValueError("Recovery requires a new candidate SQLite outside the V2 reference")
    settings = load_profile(profile)
    registry = load_registry(settings.registry_path)
    provenance, catalog, hashes, claims = _cohort_inventory(v2_root, registry)
    content = sources.read_bytes()
    manifest = json.loads(content)
    if (manifest.get("schema") != SOURCE_MANIFEST_SCHEMA
            or _identity(manifest) != (settings.profile_id, settings.research_cutoff.isoformat(), settings.timezone.key)):
        raise ValueError("Cohort recovery manifest and fixed profile differ")
    hints = _git_blob(v2_root, _LEGACY_STATEMENTS)
    if hints is None or _digest(hints) != _LEGACY_STATEMENTS_SHA:
        raise ValueError("Frozen resource observation hints differ")
    hints = list(csv.DictReader(StringIO(hints.decode("utf-8-sig"))))
    store, inventory = ContentStore(assets), {}
    records, holds, documents, views = _cohort_lanes(hints, catalog, manifest, store, historical_assets, inventory)
    _verify_cohort_bytes(historical_assets, store, catalog, provenance, inventory)
    text, text_holds, reused = _rebind_cohort_text(claims, provenance, store, historical_assets, catalog, manifest, views)
    government, government_receipt = _replay_cohort_government(v2_root, geography)
    cells = _cohort_cells(provenance, inventory, records, text, government)
    calculations = _cohort_formula_checks(records)
    database.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=database.parent) as directory:
        temporary = Path(directory) / database.name
        integrity = _write_cohort_store(temporary, registry, records, store, historical_assets)
        database.hardlink_to(temporary)
    return {
        "schema": COHORT_RECOVERY_SCHEMA,
        "status": "FAIL" if "SOURCE_SHA_CONFLICT" in inventory.values() else "PASS" if records else "BLOCKED",
        "complete_historical_rebuild": "BLOCKED" if "EXACT_BYTES_UNAVAILABLE" in inventory.values() else "NOT_RUN",
        "v2_archive_ref": V2_REF, "archive_input_sha256": hashes, "profile_sha256": settings.sha256,
        "source_manifest_sha256": _digest(content), "cutoff": manifest["cutoff"], "timezone": manifest["timezone"],
        "database_sha256": _digest(database.read_bytes()), "sqlite_integrity": integrity,
        "counts": {
            "projects": len(registry.projects), "cells": len(cells), "pdfs_read": len(documents),
            "candidate_positions": len(records), "candidate_projects": len({row["project_id"] for row in records.values()}),
            "v2_hint_matched_positions": sum(bool(row["legacy_hint_ids"]) for row in records.values()),
            "candidate_cells_with_original_anchors": sum(bool(row["original_anchor_ids"]) for row in cells),
            "candidate_text_cells": len(text), "candidate_text_projects": len({project for project, _ in text}),
            "candidate_text_holds": dict(text_holds), "shadow_formula_checks": len(calculations),
            "reused_pdf_views": reused, "government_candidate_projects": len(government),
            "source_status": dict(Counter(inventory.values())), "holds": dict(Counter(row["reason"] for row in holds)),
            "formal_admissions": 0,
        },
        "cells": cells, "resource_holds": holds, "documents": documents, "formula_checks": calculations,
        "government_replay": government_receipt,
    }

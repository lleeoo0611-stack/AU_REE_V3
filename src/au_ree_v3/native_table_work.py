"""Reread pinned source inventory and close candidate-only table review work."""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .contracts import canonical_json
from .documents import DocumentCache, DocumentError
from .native_table_discovery import DISCOVERY_RULE, discover_page_rows
from .registry import SHA256, load_profile, load_registry
from .resource_table_binding import DETECTION_RULE, RULE, _baseline_overlap, _plain, bind_page_row
from .source_inventory import MAX_DOCUMENT_BYTES, _CheckedBody, _verify_edition
from .sources import ContentStore, IntegrityError

WORK_RULE = "native-resource-table-work-v1"
_LOCATOR = re.compile(r"PDF page (\d+), line (\d+)")
_NATIVE = "NATIVE_TABLE_TOTAL_ROW"
_REVIEWED = "REVIEWED_BINDING_ORIGINAL_TOKEN"
_UNRECOGNIZED = "UNRECOGNIZED_SOURCE_PROJECT"
_UNBOUND_LEDGER = "unbound_numeric_tokens.jsonl"
_LINEAGE = "value_evidence_lineage.jsonl"
_REVIEW_RESULT = "prior_binding_dispositions.jsonl"
_DISCOVERED = "discovered_native_rows.jsonl"


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

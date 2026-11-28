"""Build candidate-only review batches from the frozen native routing run."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .contracts import canonical_json
from .documents import DocumentCache, DocumentError
from .sources import ContentStore, IntegrityError

SCHEMA = "source-case-inventory-v1"
BASELINE = "704de116e0d1bb5acc067855ab023826b5ee7d24"
VERIFICATION_SHA = "11431ee9eee0721d33741e7274785c4769e7124618362895019731c2be681c22"
V2_REF = "2af6f611e51eb4ac9dda73419d1afeba70d1307e"
V2_RECOVERY = "results/phase10-2026-11-27/source_recovery_manifest.json"
V2_RECOVERY_SHA = "36b467ac625cc7caccd4589f18562f6d19c5848b83dfed4ae495fad7706ccdc5"
TRANSIENT_LEGACY_FIELDS = {"legacy_display_hint", "legacy_locator_hint", "legacy_scope_hint"}
LINE = re.compile(r"PDF page (\d+), line (\d+)")
LINES = re.compile(r"PDF page (\d+), lines (\d+)-(\d+)")
TABLE_LABEL = re.compile(r"\btable\s+([A-Z]?\d+[A-Z]?(?:[.-]\d+)?)\b", re.I)
DATE = r"(?:[0-3]?\d[ /.-](?:[A-Za-z]{3,9}|[01]?\d)[ ,/.-]+20\d\d|20\d\d-[01]\d-[0-3]\d)"
REQUIREMENTS = (
    "NATIVE_HEADER_UNIT_AND_QUANTITY_BINDING", "JORC_RESOURCE_CLASSIFICATION",
    "PROJECT_DEPOSIT_AND_STATEMENT_SCOPE", "ESTIMATE_EFFECTIVE_DATE_AND_PUBLICATION",
    "CUTOFF_ANALYTE_UNIT_AND_VALUE", "MATERIAL_AND_DENOMINATOR",
    "CHEMICAL_BASIS_AND_COMPONENT_MEMBERSHIP", "DIRECT_REPORTED_CONTAINED_MASS_OR_DERIVATION_PROOF",
)
SIGNALS = {
    "JORC_REFERENCE_NEAR_TABLE": r"\bJORC\b",
    "RESOURCE_KIND_IN_CAPTION": r"\bmineral resources?\b",
    "SCOPE_LABEL_IN_CAPTION": r"\b(?:deposit|zone|domain|area|block|combined|global|[a-z]+ prospect)\b",
    "ESTIMATE_DATE_TEXT_NEAR_TABLE": rf"(?:as (?:of|at)|effective date|estimate date|estimated (?:at|on)).{{0,40}}{DATE}",
    "CUTOFF_TEXT_NEAR_TABLE": r"\bcut[ -]?off\b|\bCOG\b",
    "MATERIAL_TEXT_NEAR_TABLE": r"\b(?:in[ -]situ|whole ore|saprolite|bulk(?: ore)?|concentrate|heavy minerals?)\b",
    "CHEMICAL_TEXT_NEAR_TABLE": r"\b(?:TREO|[A-Z][a-z]\d?O\d|rare earth oxides?)\b",
    "POSSIBLE_DIRECT_CONTENT_HEADER": r"\bcontained\b|\b(?:TREO|REO)\s*\(\s*(?:kt|Mt|t)\s*\)",
}


def _digest(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path, expected=None):
    content = path.read_bytes()
    actual = sha256(content).hexdigest()
    _check(expected is None or actual == expected, f"Frozen input SHA-256 mismatch: {path.name}")
    return content


def _objects(content):
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def _frozen_inputs(verification, routing, discovery, sources):
    frozen = json.loads(_read(verification, VERIFICATION_SHA))
    _check(frozen["status"] == "PASS" and frozen["fixed_input_rerun_equal"], "PR #25 verification is incomplete")
    pins = frozen["receipt_sha256_by_path"]
    route_names = ("source_routing_receipt.json", "routed_original_candidates.jsonl", "routed_source_work.json")
    content = {name: _read(routing / name, pins[f"work/census-completion/committed-a/{name}"])
               for name in route_names}
    receipt = json.loads(content["source_routing_receipt.json"])
    _check({key: value for key, value in receipt.items() if key != "documents"} == frozen["routing"],
           "Routing receipt differs from the approved verification")
    for name, checksum in receipt["outputs"].items():
        _check(sha256(content[name]).hexdigest() == checksum, f"Routing fingerprint mismatch: {name}")
    recovery = "legacy_value_recovery_receipt.json"
    recovery_bytes = _read(discovery / recovery, receipt["inputs"]["recovery_receipt_sha256"])
    recovery_data = json.loads(recovery_bytes)
    _check(recovery_data == frozen["discovery"]["discovery_receipt"],
           "Recovery receipt differs from the approved verification")
    queue_path = "source_project_work_queue.json"
    queue_bytes = _read(discovery / queue_path, receipt["inputs"]["work_queue_sha256"])
    _check(sha256(queue_bytes).hexdigest() == recovery_data["work_queue_sha256"],
           "Source work queue does not match the discovery receipt")
    leads = "legacy_value_leads.jsonl"
    leads_bytes = _read(discovery / leads, frozen["discovery"]["digests"][leads])
    _check(sha256(leads_bytes).hexdigest() == recovery_data["leads_sha256"],
           "Search hints do not match the discovery receipt")
    manifest_bytes = _read(sources, receipt["inputs"]["source_manifest_sha256"])
    manifest = json.loads(manifest_bytes)
    identity = ("profile_id", "cutoff", "timezone")
    _check(all(receipt[key] == manifest[key] for key in identity), "Routing and manifest study versions differ")
    return (frozen, manifest, receipt, _objects(content["routed_original_candidates.jsonl"]),
            json.loads(content["routed_source_work.json"])["rows"], _objects(leads_bytes), {
                "pr25_verification_sha256": VERIFICATION_SHA,
                "source_manifest_sha256": sha256(manifest_bytes).hexdigest(),
                "legacy_value_leads_sha256": sha256(leads_bytes).hexdigest(),
                "legacy_recovery_receipt_sha256": sha256(recovery_bytes).hexdigest(),
                "legacy_work_queue_sha256": sha256(queue_bytes).hexdigest(),
                **{name: sha256(data).hexdigest() for name, data in content.items()},
            })


def _source_editions(manifest):
    grouped = defaultdict(list)
    for edition in manifest["editions"]:
        grouped[edition["sha256"]].append(edition)
    return grouped


def _base_record(source, project, route, editions, page, kind):
    approved = set(route["source_edition_ids"])
    eligible = sorted(e["edition_id"] for e in editions[source]
                      if project in e["project_ids"] and e["edition_id"] in approved
                      and e["temporal_decision"]["status"] == "ELIGIBLE")
    _check(bool(eligible), f"No eligible receipt-bound edition for {source}/{project}")
    matched = [e for e in editions[source] if e["edition_id"] in eligible]
    return {
        "source_sha256": source, "project_id": project, "physical_page": page,
        # Multiple capture receipts may attest to the same source bytes.
        "edition_id": eligible[0], "eligible_edition_ids": eligible,
        "source_urls": sorted({e["url"] for e in matched}),
        "kind": kind, "candidate_only": True, "formal_admission": False,
    }


def _match_location(pattern, locator, page):
    match = pattern.fullmatch(locator)
    _check(match is not None and int(match[1]) == page, f"Inconsistent physical location: {locator}")
    return tuple(map(int, match.groups()[1:]))


def _location_and_context(candidate, document):
    page = candidate["physical_page"]
    lines = document.pages[page - 1].splitlines()
    row_line, = _match_location(LINE, candidate["locator"], page)
    caption_line, = _match_location(LINE, candidate["caption_locator"], page)
    header_start, header_end = _match_location(LINES, candidate["header_locator"], page)
    _check(1 <= caption_line < header_start <= header_end < row_line <= len(lines),
           f"Invalid table and row ordering: {candidate['locator']}")
    _check(lines[row_line - 1] == candidate["row_token"], "Routed row differs from exact original text")
    # Use native header text and keep any intersecting body cells visible.
    # These are layout snippets, so they are not used to assign scientific values.
    header_text = "\n".join(lines[header_start - 1:header_end])
    available = "\n".join(lines[max(0, caption_line - 6):min(len(lines), row_line + 20)])
    caption = candidate["caption"]
    windows = {"RESOURCE_KIND_IN_CAPTION": caption, "SCOPE_LABEL_IN_CAPTION": caption,
               "POSSIBLE_DIRECT_CONTENT_HEADER": header_text}
    evidence = {name: bool(re.search(pattern, windows.get(name, available), re.I))
                for name, pattern in SIGNALS.items()}
    evidence["NATIVE_HEADER_AND_ROW"] = bool(header_text.strip())
    title = TABLE_LABEL.search(caption)
    return (
        {"locator": candidate["caption_locator"], "caption": caption,
         "label_hint": f"Table {title[1]}" if title else None},
        {"locator": candidate["locator"], "text": candidate["row_token"]},
        {"locator": candidate["header_locator"], "text": header_text},
        evidence,
    )


def _position(candidate, token):
    start, end = token["start_column"], token["end_column"]
    _check(type(start) is int and type(end) is int and 1 <= start <= end <= len(candidate["row_token"]),
           "Native token coordinates exceed the routed row")
    value = token["token"]
    _check(candidate["row_token"][start - 1:end] == value, "Lexeme differs from the routed row coordinates")
    return _digest([candidate["source_sha256"], candidate["locator"], start, end, value])


def _text_key(row):
    return (row["source_sha256"], row["project_id"], row["physical_page"], " ".join(row["row_token"].split()))


def _reviewed_by_text(records, matches):
    result = defaultdict(set)
    _check(len(records) == len(matches), "Reviewed token count differs from independent V3 identity receipt")
    ids = set()
    for row, match in zip(records, matches, strict=True):
        identity = ("case_id", "variable", "source_sha256", "physical_page")
        _check(all(row[key] == match[key] for key in identity), "Reviewed source token identity differs")
        _check(match["observation_id"] not in ids, "Reviewed observation appears more than once")
        ids.add(match["observation_id"])
        result[_text_key(row) + (row["native_numeric_tokens"][0]["token"],)].add(match["observation_id"])
    return result


def _reread(cas, editions, candidates):
    maximum = {}
    for row in candidates:
        if row["kind"] == "NATIVE_TABLE_TOTAL_ROW":
            key = row["source_sha256"]
            maximum[key] = max(maximum.get(key, 0), row["physical_page"])
    documents, receipts = {}, []
    cache = DocumentCache()
    for digest, entries in sorted(editions.items()):
        row = {"source_sha256": digest, "edition_ids": sorted(e["edition_id"] for e in entries),
               "kind": entries[0]["kind"], "status": "EXACT_SHA", "native_pages_decoded": 0}
        try:
            content = cas.read(digest)
            if any(e["bytes"] != len(content) for e in entries):
                raise IntegrityError("Length differs from frozen manifest")
            row["bytes"] = len(content)
        except IntegrityError:
            row.update(status="SCIENTIFIC_CONFLICT", reason="SOURCE_SHA_OR_LENGTH_MISMATCH")
        except OSError:
            row.update(status="SOURCE_UNAVAILABLE", reason="ORIGINAL_CAS_BYTES_MISSING")
        else:
            row["observed_sha256"] = digest
            if digest in maximum:
                try:
                    document = cache.pdf(content, digest, maximum[digest])
                    documents[digest] = document
                    row.update(native_pages_decoded=len(document.pages), runtime=document.runtime)
                except DocumentError as exc:
                    row["native_reread_issue"] = str(exc)
        receipts.append(row)
    return documents, receipts


def _structural_cases(candidate, base, document, identified, source_receipt):
    if document is None:
        table = {"locator": candidate["caption_locator"], "caption": candidate["caption"], "label_hint": None}
        row = {"locator": candidate["locator"], "text": candidate["row_token"]}
        header = {"locator": candidate["header_locator"], "text": None}
        signals = {}
        state = source_receipt["status"]
        if state == "EXACT_SHA":
            state = "EVIDENCE_INSUFFICIENT"
    else:
        table, row, header, signals = _location_and_context(candidate, document)
        state = "NOT_RUN"
    unit = _digest([base["project_id"], base["source_sha256"], table["locator"]])
    for token in candidate["native_numeric_tokens"]:
        source_position = _position(candidate, token)
        existing = identified.get(_text_key(candidate) + (token["token"],), set())
        yield {
            **base, "case_id": _digest([SCHEMA, base["project_id"], source_position]),
            "source_position_id": source_position, "review_unit_id": unit,
            "table": table, "row": row, "header": header, "statement_id": None,
            "source_token": token["token"],
            "token_columns": {"start": token["start_column"], "end": token["end_column"]},
            "role_guess": "UNBOUND_TABLE_NUMBER", "context_requirements": list(REQUIREMENTS),
            "priority_signals": signals, "status": state, "open_issues": candidate["evidence_limits"],
            "existing_reviewed_row_lexeme_overlap": sorted(existing),
        }


def _reviewed_case(candidate, base, match):
    prior_status = match["validation_outcome"]
    return {
        **base, "case_id": _digest([SCHEMA, match["observation_id"], match["anchor_id"]]),
        "source_position_id": None, "review_unit_id": None,
        "table": None, "row": {"locator": candidate["locator"], "text": candidate["row_token"]}, "header": None,
        "statement_id": None, "source_token": candidate["native_numeric_tokens"][0]["token"],
        "token_columns": None, "role_guess": candidate["variable"], "context_requirements": [],
        "priority_signals": {}, "status": prior_status, "open_issues": [match["validation_reason"]]
        if prior_status != "VALIDATED" else [],
        "existing_binding_case_id": candidate["case_id"], "existing_observation_id": match["observation_id"],
        "existing_candidate_id": match["candidate_id"], "existing_validation_id": match["validation_id"],
        "existing_formal_acceptance_id": match["formal_acceptance_id"], "existing_anchor_id": match["anchor_id"],
        "existing_baseline_only": True, "source_unit_as_reported": candidate["native_unit"],
    }


def _unrecognized_case(base, route):
    return {
        **base, "case_id": _digest([SCHEMA, base["project_id"], base["source_sha256"], base["kind"]]),
        "source_position_id": None, "review_unit_id": None, "table": None, "row": None, "header": None,
        "statement_id": None, "source_token": None, "token_columns": None, "role_guess": "UNKNOWN",
        "context_requirements": list(REQUIREMENTS), "priority_signals": {}, "status": "EVIDENCE_INSUFFICIENT",
        "open_issues": ["NO_NATIVE_STRUCTURAL_MATCH_IN_PR25_ROUTE"],
        "physical_pages_inspected": route["physical_pages_inspected"], "prior_route_status": route["status"],
    }


def _inventory(candidates, routes, editions, documents, source_receipts, reviewed_matches):
    projects = {(row["source_sha256"], row["project_id"]): row for row in routes}
    source_receipt = {row["source_sha256"]: row for row in source_receipts}
    reviewed = [row for row in candidates if row["kind"] == "REVIEWED_BINDING_ORIGINAL_TOKEN"]
    identified = _reviewed_by_text(reviewed, reviewed_matches)
    indexed_matches = iter(reviewed_matches)
    cases = []
    for row in candidates:
        source, project = row["source_sha256"], row["project_id"]
        route = projects[source, project]
        _check(row["candidate_only"] and not row["formal_admission"], "Route contains a formal observation")
        base = _base_record(source, project, route, editions, row["physical_page"], row["kind"])
        if row["kind"] == "NATIVE_TABLE_TOTAL_ROW":
            cases.extend(_structural_cases(row, base, documents.get(source), identified, source_receipt[source]))
        else:
            _check(row["kind"] == "REVIEWED_BINDING_ORIGINAL_TOKEN", "Unknown source route artifact kind")
            cases.append(_reviewed_case(row, base, next(indexed_matches)))
    for (source, project), route in projects.items():
        if route["status"] == "NATIVE_PAGES_INSPECTED_NO_STRUCTURAL_MATCH":
            base = _base_record(source, project, route, editions, None, "UNRECOGNIZED_SOURCE_PROJECT")
            cases.append(_unrecognized_case(base, route))
    _check(len({row["case_id"] for row in cases}) == len(cases), "Duplicate source cases in frozen input")
    return cases


def _prioritize(cases):
    units = {}
    for case in cases:
        key = case["review_unit_id"]
        if key is None:
            continue
        entry = units.setdefault(key, {
            "review_unit_id": key, "source_sha256": case["source_sha256"], "project_id": case["project_id"],
            "edition_id": case["edition_id"], "physical_page": case["physical_page"],
            "table": case["table"], "case_ids": [], "row_locators": set(), "signals": set(),
            "status": case["status"],
        })
        entry["case_ids"].append(case["case_id"])
        entry["row_locators"].add(case["row"]["locator"])
        entry["signals"].update(k for k, present in case["priority_signals"].items() if present)
    for entry in units.values():
        entry["case_ids"].sort()
        entry["row_locators"] = sorted(entry["row_locators"])
        entry["signals"] = sorted(entry["signals"])

    def order(unit):
        signals = set(unit["signals"])
        return (unit["status"] != "NOT_RUN", "NATIVE_HEADER_AND_ROW" not in signals,
                "POSSIBLE_DIRECT_CONTENT_HEADER" not in signals, -len(signals),
                unit["source_sha256"], unit["project_id"], unit["physical_page"], unit["table"]["locator"])

    ordered = sorted(units.values(), key=order)
    return [{
        **entry, "review_order": number,
        "ordering_basis": "Expected original-document review effort; no evidence validity or admission score",
    } for number, entry in enumerate(ordered, start=1)]


def _git_blob(root, path):
    cmd = ["git", "-C", str(root), "cat-file", "blob", f"{V2_REF}:{path}"]
    result = subprocess.run(cmd, capture_output=True, check=False,
                            env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"})
    return result.stdout if result.returncode == 0 else None


def _public_url(url):
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return url


def _public_attempts(rows):
    return [{key: row.get(key) for key in ("url", "status", "reason", "observed_at", "actual_sha256", "http_status")}
            for row in rows if _public_url(row.get("url"))]


def _gap_rows(v2_root, leads, editions):
    historical = _git_blob(v2_root, V2_RECOVERY)
    reference_status = "SOURCE_UNAVAILABLE"
    history = {}
    if historical is not None:
        _check(sha256(historical).hexdigest() == V2_RECOVERY_SHA, "Frozen V2 recovery manifest fingerprint mismatch")
        history = {row["expected_sha256"]: row for row in json.loads(historical)["assets"]}
        reference_status = "EXACT_SHA"
    missing = defaultdict(list)
    for lead in leads:
        _check(not TRANSIENT_LEGACY_FIELDS.intersection(lead), "Historical numeric payload cannot drive an inventory")
        if lead["status"] == "SOURCE_EDITION_NOT_IN_V3":
            digest, = lead["legacy_source_shas"]
            _check(digest not in editions, "Fixed manifest now contains the formerly missing edition")
            missing[digest].append(lead)
    gaps = []
    for digest, positions in sorted(missing.items()):
        reference = history.get(digest, {})
        locked = reference.get("locked_sources", [])
        locators = sorted({url for record in locked if (url := _public_url(record["original_url"]))})
        kinds = {record["kind"] for record in locked}
        expected_paths = [f"data/snapshots/{digest}.{kind}" for kind in sorted(kinds & {"pdf", "html"})]
        exact = None
        for path in expected_paths:
            body = _git_blob(v2_root, path)
            if body is not None:
                observed = sha256(body).hexdigest()
                _check(observed == digest, f"Historical Git snapshot does not match locked source {digest}")
                exact = {"status": "EXACT_SHA", "git_ref": V2_REF, "path": path,
                         "observed_sha256": observed, "bytes": len(body)}
                break
        projects = sorted({p["project_id"] for p in positions})
        distinct = [{
            "sha256": sha, "project_ids": sorted(set(entry["project_ids"]).intersection(projects)),
            "edition_id": entry.get("edition_id"), "url": entry["url"], "version_status": entry["version_status"],
            "temporal_status": entry.get("temporal_decision", {}).get("status"),
        } for sha, entries in sorted(editions.items()) for entry in entries
            if set(entry["project_ids"]).intersection(projects)
            and (entry["url"] in locators or digest in entry.get("historical_reference_sha256", []))]
        gaps.append({
            "source_sha256": digest, "status": "SOURCE_UNAVAILABLE", "scope": "MISSING_FROM_FIXED_V3_MANIFEST",
            "historical_positions": [{"project_id": row["project_id"], "column": row["column"],
                                      "quantity_group": row["quantity_group"]} for row in positions],
            "impacted_project_ids": projects, "public_source_locators": locators, "kind": sorted(kinds),
            "pinned_locator_status": "PINNED_PUBLIC_LOCATOR" if locators else "SOURCE_UNAVAILABLE",
            "pinned_public_attempts": _public_attempts(reference.get("acquisition_attempts", [])),
            "live_public_retrieval": "NOT_RUN",
            "read_only_v2_snapshot": exact or {"status": "SOURCE_UNAVAILABLE"},
            "distinct_v3_editions": distinct,
            "scope_requirements": [
                "EXACT_SHA_REQUIRED_FOR_HISTORICAL_EDITION",
                "SEPARATE_V3_CAPTURE_AND_TEMPORAL_GATE_FOR_ANY_ADDITION",
            ],
        })
    return gaps, reference_status


def run(*, verification, routing, discovery, sources, assets, v2_root, out):
    """Verify fixed inputs, prepare independent review units and leave admissions untouched."""
    out = Path(out)
    _check(not any(out.resolve().is_relative_to(Path(root).resolve()) for root in (v2_root, assets, routing, discovery)),
           "Inventory output must be outside source and immutable receipt directories")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("Inventory output must be a new empty directory")
    frozen, manifest, route, candidates, routes, leads, input_shas = _frozen_inputs(
        verification, routing, discovery, sources
    )
    editions = _source_editions(manifest)
    documents, source_receipts = _reread(ContentStore(assets), editions, candidates)
    cases = _inventory(candidates, routes, editions, documents, source_receipts,
                       frozen["independent_review"]["baseline_evidence_matches"]["matches"])
    priorities = _prioritize(cases)
    unrecognized = sorted(
        (row for row in cases if row["kind"] == "UNRECOGNIZED_SOURCE_PROJECT"),
        key=lambda row: (len(row["physical_pages_inspected"]), row["project_id"], row["source_sha256"]),
    )
    gaps, history_status = _gap_rows(v2_root, leads, editions)
    counts = Counter(row["kind"] for row in cases)
    unique_positions = {row["source_position_id"] for row in cases if row["source_position_id"]}
    _check(len(unique_positions) == route["unique_native_structural_tokens"], "Native position coverage mismatch")
    _check(counts["REVIEWED_BINDING_ORIGINAL_TOKEN"] == route["unique_reviewed_binding_tokens"],
           "Reviewed observation coverage mismatch")
    _check(counts["UNRECOGNIZED_SOURCE_PROJECT"] ==
           route["candidate_work_statuses"]["NATIVE_PAGES_INSPECTED_NO_STRUCTURAL_MATCH"],
           "Source-project coverage mismatch")
    _check({row["source_sha256"] for row in gaps} ==
           set(frozen["discovery"]["missing_exact_source_editions"]["sha256"]), "Historical gap edition coverage mismatch")
    _check(sum(len(row["historical_positions"]) for row in gaps) ==
           frozen["discovery"]["discovery_receipt"]["statuses"]["SOURCE_EDITION_NOT_IN_V3"],
           "Historical hint coverage mismatch")

    identity = {key: manifest[key] for key in ("profile_id", "cutoff", "timezone")}
    info = {"schema": SCHEMA, **identity, "candidate_only": True, "baseline_code_commit": BASELINE}
    source_checks = Counter(row["status"] for row in source_receipts)
    gate = "PASS"
    if source_checks["SCIENTIFIC_CONFLICT"]:
        gate = "FAIL"
    elif source_checks["SOURCE_UNAVAILABLE"] or history_status != "EXACT_SHA":
        gate = "BLOCKED"
    doc_errors = sum("native_reread_issue" in row for row in source_receipts)
    out.mkdir(parents=True, exist_ok=True)
    output_data = {
        "source_case_inventory.jsonl": "".join(canonical_json({**info, **case}) + "\n" for case in cases),
        "source_case_priority.json": canonical_json({**info, "review_units": priorities,
            "structural_followup": [{key: row[key] for key in (
                "case_id", "source_sha256", "project_id", "physical_pages_inspected", "status"
            )} for row in unrecognized],
            "policy": {"kind": "REVIEW_ORDER_ONLY", "admission_assessed": False,
                       "ordering": ["same-page native header", "possible direct content header",
                                    "more nearby context signals", "stable source/page order"],
                       "structural_followup_ordering": "fewest pages already inspected, then stable source/project order",
                       "grouping": "physical source/project/table caption; statement aliases are unresolved"}}) + "\n",
        "source_recovery_gaps.json": canonical_json({**info, "historical_recovery_manifest_status": history_status,
            "historical_recovery_manifest_sha256": V2_RECOVERY_SHA, "rows": gaps}) + "\n",
        "source_case_sha_receipt.json": canonical_json({**info, "rows": source_receipts,
            "source_manifest_sha256": input_shas["source_manifest_sha256"]}) + "\n",
    }
    for name, content in output_data.items():
        with (out / name).open("x", encoding="utf-8", newline="\n") as file:
            file.write(content)
    receipt = {
        **info, "status": "BLOCKED" if gate == "PASS" and doc_errors else gate,
        "gates": {"SY0": gate, "SY1": "NOT_RUN"},
        "input_sha256": input_shas, "output_sha256": {
            name: sha256(content.encode()).hexdigest() for name, content in output_data.items()
        },
        "source_sha_statuses": dict(source_checks), "kind_counts": dict(counts),
        "structural_row_sightings": route["original_candidate_count_by_method"]["NATIVE_TABLE_TOTAL_ROW"],
        "distinct_native_source_positions": len(unique_positions),
        "unique_review_units": len(priorities), "native_reread_issues": doc_errors,
        "missing_edition_count": len(gaps), "missing_exact_editions_available_in_v2_git": sum(
            row["read_only_v2_snapshot"]["status"] == "EXACT_SHA" for row in gaps
        ),
        "formal_scientific_values_added": 0,
    }
    with (out / "source_case_inventory_receipt.json").open("x", encoding="utf-8", newline="\n") as file:
        file.write(canonical_json(receipt) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("verification", "routing", "discovery", "sources", "assets", "v2-root", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    result = run(**vars(parser.parse_args()))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

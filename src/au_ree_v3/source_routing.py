"""Route legacy page hints back to verified V3 originals for candidate-only review."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .census import COLUMNS
from .contracts import canonical_json
from .documents import DocumentCache, DocumentError
from .fresh_observations import SCHEMA as BINDINGS_SCHEMA, _matched_editions
from .migration.baseline import ARCHIVED_CENSUS, BASELINES
from .registry import SHA256, load_census_columns, load_profile, load_registry
from .resource_extraction import STRATEGIES
from .source_inventory import MAX_DOCUMENT_BYTES, _CheckedBody, _verify_edition
from .sources import ContentStore, IntegrityError, SCHEMA as ACQUISITION_SCHEMA

SCHEMA = "v3-source-work-routing-v1"
QUEUE_SCHEMA = "v2-source-project-rediscovery-queue-v1"
RECOVERY_SCHEMA = "v2-resource-value-rediscovery-v1"
_FIELDS = {
    "source_sha256", "project_id", "target_columns", "value_pages", "context_pages",
    "legacy_seed_pages", "value_hints", "status", "read_original_cas_again", "formal_admission",
}
_CAPTION = re.compile(r"^\s*table\s+[A-Za-z]?\d+(?:[.-]\d+)*\b", re.I)
_RESOURCE = re.compile(r"\bmineral\s+resource(?:s|\s+estimate)?\b", re.I)
_NON_RESOURCE = re.compile(r"\b(?:ore\s+reserves?|exploration\s+targets?)\b", re.I)
_TOTAL = re.compile(r"^\s*(?:grand\s+total|total|global\s+total|combined\s+total)\b\s{2,}", re.I)
_MASS = re.compile(r"\b(?:tonnage|tonnes?|mass)\b|\b(?:Mt|kt)\b", re.I)
_REO = re.compile(r"\b(?:TREO|rare\s+earth\s+oxides?|Nd2O3|Pr6O11|Dy2O3|Tb4O7)\b", re.I)
_NUMBER_CELL = re.compile(r"(?:[<>≤≥]\s*)?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
_COLUMN = re.compile(r"\S(?:.*?\S)?(?=\s{2,}|$)")
_PAGE_FIELDS = ("value_pages", "context_pages", "legacy_seed_pages")


@dataclass(frozen=True, slots=True)
class Route:
    sha256: str
    project_id: str
    target_columns: tuple[str, ...]
    value_pages: tuple[int, ...]
    context_pages: tuple[int, ...]
    legacy_seed_pages: tuple[int, ...]

    @property
    def pages(self) -> tuple[int, ...]:
        return tuple(sorted({*self.value_pages, *self.context_pages, *self.legacy_seed_pages}))

    @property
    def key(self) -> tuple[str, str]:
        return self.sha256, self.project_id


def _json(data: bytes) -> dict:
    def pairs(entries):
        result = dict(entries)
        if len(result) != len(entries):
            raise ValueError("Source routing JSON cannot contain duplicate fields")
        return result

    payload = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(payload, dict):
        raise ValueError("Source routing requires a JSON object")
    return payload


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _valid_sha(value) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _check_receipt(receipt, queue_bytes, sources_bytes, cohort):
    provenance = next(
        pin["sha256"] for pin in BASELINES["pins"][ARCHIVED_CENSUS]
        if pin["path"] == f"{BASELINES['archive_path']}/field_provenance.csv"
    )
    if (
        receipt.get("schema") != RECOVERY_SCHEMA or receipt.get("status") != "PASS"
        or receipt.get("candidate_only") is not True or receipt.get("formal_scientific_values_added") != 0
        or receipt.get("v2_ref") != BASELINES["refs"]["engineering"]
        or receipt.get("v2_field_provenance_sha256") != provenance
        or receipt.get("index_schema") != "candidate-evidence-index-v1"
        or not _valid_sha(receipt.get("v3_candidate_index_sha256"))
        or receipt.get("project_cohort") != len(cohort)
        or receipt.get("work_queue_sha256") != _digest(queue_bytes)
        or receipt.get("source_manifest_sha256") != _digest(sources_bytes)
    ):
        raise ValueError("Recovery receipt does not pin the exact V2 reference, V3 manifest, and candidate queue")


def _route_fields(row, cohort, target_columns) -> Route:
    if not isinstance(row, dict) or set(row) != _FIELDS:
        raise ValueError("Work item must contain only source, Project, column and page hints")
    if (
        not _valid_sha(row["source_sha256"]) or not isinstance(row["project_id"], str) or row["project_id"] not in cohort
        or row["status"] != "CANDIDATE" or row["read_original_cas_again"] is not True or row["formal_admission"] is not False
    ):
        raise ValueError("Work item does not bind a candidate-only source and registered Project")
    columns = row["target_columns"]
    if (
        not isinstance(columns, list) or not columns or any(not isinstance(name, str) for name in columns)
        or len(columns) != len(set(columns)) or not set(columns) <= target_columns
        or type(row["value_hints"]) is not int or row["value_hints"] != len(columns)
    ):
        raise ValueError("Work item has unknown, duplicate, or non-numeric Census target headings")
    pages = []
    for field in _PAGE_FIELDS:
        values = row[field]
        if (not isinstance(values, list) or any(type(page) is not int or not 1 <= page <= 10000 for page in values)
                or len(values) != len(set(values))):
            raise ValueError("Work item physical page hints must be unique integers from 1 to 10000")
        pages.append(tuple(sorted(values)))
    if any(not set(pages[2]).issubset(hits) for hits in pages[:2]):
        raise ValueError("A fixed physical page must be preserved in both review routes")
    return Route(row["source_sha256"], row["project_id"], tuple(sorted(columns)), *pages)


def _manifest_editions(manifest, cohort):
    result = defaultdict(list)
    captures = set()
    for edition in manifest["editions"]:
        digest, projects, edition_id, receipt_sha = (
            edition[name] for name in ("sha256", "project_ids", "edition_id", "receipt_sha256")
        )
        if (
            not _valid_sha(digest) or not _valid_sha(edition_id) or not _valid_sha(receipt_sha)
            or receipt_sha in captures
            or not isinstance(projects, list) or not projects or any(not isinstance(project, str) for project in projects)
            or len(set(projects)) != len(projects) or not set(projects).issubset(cohort)
        ):
            raise ValueError("Acquisition manifest contains invalid source editions or Project links")
        captures.add(receipt_sha)
        result[digest].append(edition)
    return result


def _inputs(profile, queue, recovery_receipt, sources, bindings):
    settings = load_profile(profile)
    identity = (settings.profile_id, settings.research_cutoff.isoformat(), settings.timezone.key)
    registry = load_registry(settings.registry_path)
    cohort = {project.project_id for project in registry.projects}
    queue_bytes, recovery_bytes, sources_bytes, bindings_bytes = (
        Path(path).read_bytes() for path in (queue, recovery_receipt, sources, bindings)
    )
    document, receipt, manifest, spec = map(_json, (queue_bytes, recovery_bytes, sources_bytes, bindings_bytes))
    _check_receipt(receipt, queue_bytes, sources_bytes, cohort)
    if (
        set(document) != {"schema", "rows", "candidate_only", "formal_scientific_values_added"}
        or document.get("schema") != QUEUE_SCHEMA or document.get("candidate_only") is not True
        or document.get("formal_scientific_values_added") != 0 or not isinstance(document.get("rows"), list)
    ):
        raise ValueError("Expected the candidate-only routing queue")
    for name, payload, schema in (("manifest", manifest, ACQUISITION_SCHEMA), ("bindings", spec, BINDINGS_SCHEMA)):
        if payload.get("schema") != schema or tuple(payload.get(key) for key in ("profile_id", "cutoff", "timezone")) != identity:
            raise ValueError(f"Source-routing {name} and research profile differ")
    target_columns = set(COLUMNS.values()) | {
        name for name in load_census_columns(settings.census_columns_path)
        if re.fullmatch(r"[A-Z][a-z]?\d*O\d+_氧化[^_]+_kt", name)
    }
    routes = tuple(_route_fields(row, cohort, target_columns) for row in document["rows"])
    if (
        len({route.key for route in routes}) != len(routes)
        or receipt.get("unique_source_project_work_items") != len(routes)
        or receipt.get("unique_eligible_source_sha") != len({route.sha256 for route in routes})
    ):
        raise ValueError("Recovery receipt and unique source-Project work queue differ")

    editions = _manifest_editions(manifest, cohort)
    matches = defaultdict(list)
    for binding, edition, project in _matched_editions(spec, manifest):
        if binding["strategy"] not in STRATEGIES or project.entity_id not in cohort:
            raise ValueError("Reviewed source binding has an unknown strategy or Project")
        matches[edition["sha256"], project.entity_id].append((binding, edition))
    pins = {
        "profile_sha256": settings.sha256, "registry_sha256": registry.sha256,
        "work_queue_sha256": _digest(queue_bytes), "recovery_receipt_sha256": _digest(recovery_bytes),
        "source_manifest_sha256": _digest(sources_bytes), "reviewed_bindings_sha256": _digest(bindings_bytes),
    }
    return identity, editions, sorted(routes, key=lambda row: row.key), matches, pins


def _native_rows(document, page):
    """Read printed Total cells only where a native same-page table anchors them."""
    lines = document.pages[page - 1].splitlines()
    caption_at = None
    for index, line in enumerate(lines):
        if _CAPTION.match(line):
            caption_at = index
            continue
        if caption_at is None or not 1 <= index - caption_at <= 24 or not _TOTAL.match(line):
            continue
        context = lines[caption_at:min(index, caption_at + 4)]
        caption = " ".join(text.strip() for text in context if text.strip())
        if not _RESOURCE.search(caption) or _NON_RESOURCE.search(caption):
            continue
        headers = [text for text in lines[caption_at + 1:index] if not _TOTAL.match(text)]
        header = "\n".join(headers)
        if not (_MASS.search(header) and _REO.search(header)):
            continue
        parts = list(_COLUMN.finditer(line))
        cells = [
            {"token": match[0], "start_column": match.start() + 1, "end_column": match.end()}
            for match in parts[1:] if _NUMBER_CELL.fullmatch(match[0])
        ]
        if len(cells) < 2:
            continue
        native = document.locate(page, line=index + 1)
        yield {
            "kind": "NATIVE_TABLE_TOTAL_ROW", "physical_page": page, "locator": native.locator,
            "caption_locator": document.locate(page, line=caption_at + 1).locator, "caption": caption,
            "header_locator": f"PDF page {page}, lines {caption_at + 2}-{index}",
            "row_token": native.text, "native_numeric_tokens": cells,
            "reason": "SAME_PAGE_RESOURCE_CAPTION_HEADER_AND_PRINTED_TOTAL_ROW",
            "evidence_limits": ["EFFECTIVE_DATE_NOT_VALIDATED", "PROJECT_SCOPE_NOT_VALIDATED",
                                "CUTOFF_NOT_VALIDATED", "CHEMICAL_BASIS_NOT_VALIDATED",
                                "VALUE_COLUMN_ASSOCIATIONS_NOT_VALIDATED"],
        }


def _reviewed_rows(document, binding):
    """Re-execute the existing reviewed extractor without new Store selections."""
    method = binding["strategy"]
    statements = STRATEGIES[method](document.pages, binding, binding.get("publication_date"))
    for statement in statements:
        for cell in statement.measurements:
            yield {
                "kind": "REVIEWED_BINDING_ORIGINAL_TOKEN", "case_id": binding["case_id"], "strategy": method,
                "physical_page": cell.page, "locator": f"physical PDF page {cell.page}; {cell.table}; {cell.row}; {cell.column}",
                "variable": cell.variable, "native_numeric_tokens": [{"token": cell.token}], "native_unit": cell.raw_unit,
                "scope": statement.scope, "effective_date": statement.effective_date, "category": statement.category,
                "cutoff_grade": statement.cutoff, "cutoff_unit": statement.cutoff_unit, "cutoff_analyte": statement.cutoff_analyte,
                "row_token": cell.quote, "reason": "EXISTING_REVIEWED_RESOURCE_STRATEGY_REREAD",
                "measurement_issue": cell.conflict or cell.evidence_issue,
            }


def _project_result(route, editions, temporal):
    scoped = [edition for edition in editions if route.project_id in edition["project_ids"]]
    decisions = [temporal[edition["receipt_sha256"]] for edition in scoped]
    eligible = [edition for edition, decision in zip(scoped, decisions, strict=True) if decision["status"] == "ELIGIBLE"]
    return {
        "source_sha256": route.sha256, "project_id": route.project_id, "target_columns": route.target_columns,
        "requested_pages": route.pages, "legacy_seed_pages": route.legacy_seed_pages,
        "source_edition_ids": sorted({edition["edition_id"] for edition in scoped}),
        "temporal_editions": decisions, "eligible_capture_sha256": sorted(edition["receipt_sha256"] for edition in eligible),
        "reviewed_bindings": [],
        "physical_pages_inspected": [], "issues": [],
    }


def _project_evidence(route, result, matches, document, native_rows):
    eligible = set(result["eligible_capture_sha256"])
    if not eligible:
        return []
    result["physical_pages_inspected"] = [page for page in route.pages if page <= len(document.pages)]
    missing = sorted(set(route.pages) - set(result["physical_pages_inspected"]))
    issue_list = result["issues"]
    if missing:
        issue_list.append({"reason": "REQUESTED_PHYSICAL_PAGE_MISSING", "physical_pages": missing})
    blank = [page for page in result["physical_pages_inspected"] if not document.pages[page - 1].strip()]
    if blank:
        issue_list.append({"reason": "NO_NATIVE_TEXT_ON_REQUESTED_PAGE", "physical_pages": blank})
    if not route.pages:
        issue_list.append({"reason": "NO_PHYSICAL_PAGE_HINTS"})
    rows = [row for page in result["physical_pages_inspected"] for row in native_rows[page]]
    seen = set()
    for binding, edition in matches:
        if edition["receipt_sha256"] not in eligible or binding["case_id"] in seen:
            continue
        seen.add(binding["case_id"])
        try:
            reviewed = list(_reviewed_rows(document, binding))
        except DocumentError as error:
            result["reviewed_bindings"].append({"case_id": binding["case_id"], "status": error.outcome, "reason": str(error)})
            continue
        result["reviewed_bindings"].append({"case_id": binding["case_id"], "status": "EXTRACTED",
                                            "original_tokens": len(reviewed)})
        rows.extend(reviewed)
    result["status"] = ("CANDIDATE_ROWS_REVIEWED" if rows else
                        "NATIVE_PAGES_INSPECTED_NO_STRUCTURAL_MATCH" if result["physical_pages_inspected"] else
                        "NO_REQUESTED_PAGES_READ")
    return rows


def _capture_decision(edition, reader, cutoff, timezone):
    decision = _verify_edition(edition, reader, cutoff, timezone)
    if decision["status"] == "BLOCKED" and not re.fullmatch(r"[A-Z][A-Z_0-9]+", decision["reason"]):
        # Errors from the filesystem can contain private CAS paths.
        decision = {**decision, "reason": "CAPTURE_PROOF_UNAVAILABLE_OR_UNVERIFIED"}
    return decision


def _route_document(cas, digest, routes, editions, matches, cutoff, timezone):
    scoped = [item for item in editions if set(item["project_ids"]).intersection(route.project_id for route in routes)]
    base_results = [_project_result(route, editions, {
        edition["receipt_sha256"]: {"status": "NOT_RUN", "reason": "SOURCE_BYTES_NOT_READ"} for edition in editions
    }) for route in routes]
    for result in base_results:
        if not result["source_edition_ids"]:
            result.update(status="BLOCKED", issues=[{"reason": "NO_EXACT_V3_PROJECT_SOURCE_ASSOCIATION"}])
    if not scoped:
        return {"source_sha256": digest, "source_integrity": "NOT_RUN", "parse_status": "NOT_RUN"}, base_results, []
    source = {"source_sha256": digest, "source_integrity": "NOT_RUN", "parse_status": "NOT_RUN"}
    kinds = {item["kind"] for item in scoped}
    try:
        if cas.path(digest).stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "SOURCE_EXCEEDS_DOCUMENT_BYTE_LIMIT")
        body = cas.read(digest)
    except (OSError, IntegrityError, DocumentError) as error:
        reason = ("SOURCE_SHA_MISMATCH" if isinstance(error, IntegrityError) else
                  str(error) if isinstance(error, DocumentError) else "SOURCE_BYTES_UNAVAILABLE")
        source.update(source_integrity="FAIL" if isinstance(error, IntegrityError) else "BLOCKED", reason=reason)
        for result in base_results:
            if result["source_edition_ids"]:
                result.update(status="BLOCKED", issues=[{"reason": reason}])
        return source, base_results, []
    source.update(source_integrity="EXACT_SHA", bytes=len(body))
    reader = _CheckedBody(cas, digest, body)
    temporal = {
        edition["receipt_sha256"]: _capture_decision(edition, reader, cutoff, timezone)
        for edition in scoped
    }
    results = [_project_result(route, scoped, temporal) for route in routes]
    for result in results:
        if not result["eligible_capture_sha256"]:
            reason = ("NO_EXACT_V3_PROJECT_SOURCE_ASSOCIATION" if not result["source_edition_ids"]
                      else "NO_ELIGIBLE_VERIFIED_CAPTURE_FOR_PROJECT")
            result.update(status="BLOCKED", issues=[{"reason": reason}])
    if not any(result["eligible_capture_sha256"] for result in results):
        return source, results, []
    if len(kinds) != 1:
        source.update(parse_status="BLOCKED", reason="SOURCE_KIND_CONFLICT")
    else:
        kind, = kinds
        source["kind"] = kind
        cache = DocumentCache()
        try:
            if kind == "pdf":
                document = cache.pdf(body, digest)
                source.update(parse_status="PARSED", runtime=document.runtime, native_pages_decoded=len(document.pages))
            elif kind == "html":
                document = cache.html(body, digest)
                source.update(parse_status="PARSED", native_structures_decoded=len(document.fragments))
            elif kind == "csv":
                document = cache.csv(body, digest)
                source.update(parse_status="PARSED", native_structures_decoded=len(document.rows))
            else:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "NO_SUPPORTED_NATIVE_STRUCTURE_VIEW")
        except (DocumentError, UnicodeError) as error:
            source.update(parse_status="BLOCKED", reason=str(error))
    if source["parse_status"] != "PARSED":
        for result in results:
            if result["eligible_capture_sha256"]:
                result.update(status="BLOCKED", issues=[{"reason": source["reason"]}])
        return source, results, []
    if source["kind"] != "pdf":
        for result in results:
            if result["eligible_capture_sha256"]:
                result.update(status="NATIVE_STRUCTURE_READ_WITHOUT_PHYSICAL_PAGE_REVIEW",
                              issues=[{"reason": "NATIVE_RESOURCE_TABLE_ROUTER_REQUIRES_PDF"}])
        return source, results, []

    wanted = {page for route, result in zip(routes, results, strict=True) if result["eligible_capture_sha256"]
              for page in route.pages if page <= len(document.pages)}
    rows_by_page = {page: list(_native_rows(document, page)) for page in sorted(wanted)}
    candidates = []
    for route, result in zip(routes, results, strict=True):
        for row in _project_evidence(route, result, matches.get(route.key, ()), document, rows_by_page):
            candidates.append({
                "source_sha256": digest, "project_id": route.project_id, "candidate_only": True,
                "formal_admission": False, "source_integrity": "EXACT_SHA", **row,
            })
    return source, results, candidates


def _check_output(out, assets):
    out, assets = Path(out), Path(assets)
    target, content = out.resolve(), assets.resolve()
    if target.is_relative_to(content) or content.is_relative_to(target):
        raise ValueError("Source routing output must be separate from the immutable Content Store")
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise FileExistsError("Source routing requires a new empty output directory")


def _write_outputs(out, receipt, results, candidates):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("Source routing requires a new empty output directory")
    out.mkdir(parents=True, exist_ok=True)
    output = {
        "routed_source_work.json": canonical_json({"schema": SCHEMA, "candidate_only": True, "rows": results}) + "\n",
        "routed_original_candidates.jsonl": "".join(canonical_json(row) + "\n" for row in candidates),
    }
    result = {**receipt, "outputs": {name: _digest(text.encode("utf-8")) for name, text in output.items()}}
    with TemporaryDirectory(prefix=".source-routing-", dir=out) as staged:
        staging = Path(staged)
        for name, text in {**output, "source_routing_receipt.json": canonical_json(result) + "\n"}.items():
            (staging / name).write_text(text, encoding="utf-8")
        for name in (*output, "source_routing_receipt.json"):
            (staging / name).replace(out / name)
    return result


def route_source_work(
    *, profile: Path, queue: Path, recovery_receipt: Path, sources: Path, assets: Path, bindings: Path, out: Path,
) -> dict:
    """Reread queue-addressed exact originals and keep all outputs in the candidate layer."""
    _check_output(out, assets)
    try:
        identity, editions, routes, matches, pins = _inputs(profile, queue, recovery_receipt, sources, bindings)
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid source routing input schema") from error
    cutoff, timezone = identity[1], ZoneInfo(identity[2])
    groups = defaultdict(list)
    for route in routes:
        groups[route.sha256].append(route)
    cas = ContentStore(assets)
    results, candidates, reviewed = [], [], []
    for digest, grouped in sorted(groups.items()):
        document, inspected, rows = _route_document(cas, digest, grouped, editions[digest], matches, cutoff, timezone)
        reviewed.append(document)
        results.extend(inspected)
        candidates.extend(rows)
    page_plan = {(route.sha256, page) for route in routes for page in route.pages}
    read_pages = {(row["source_sha256"], page) for row in results for page in row["physical_pages_inspected"]}
    row_types = Counter(row["kind"] for row in candidates)
    native_tokens = {
        (row["source_sha256"], row["locator"], token["start_column"], token["end_column"], token["token"])
        for row in candidates if row["kind"] == "NATIVE_TABLE_TOTAL_ROW" for token in row["native_numeric_tokens"]
    }
    reviewed_tokens = {
        (row["source_sha256"], row["locator"], token["token"])
        for row in candidates if row["kind"] == "REVIEWED_BINDING_ORIGINAL_TOKEN" for token in row["native_numeric_tokens"]
    }
    statuses = Counter(row["status"] for row in results)
    issues = Counter(issue["reason"] for row in results for issue in row["issues"])
    binding_status = Counter(item["status"] for row in results for item in row["reviewed_bindings"])
    outside = sorted(set(matches) - {route.key for route in routes})
    status = ("FAIL" if any(row["source_integrity"] == "FAIL" for row in reviewed)
              else "BLOCKED" if not routes or statuses["BLOCKED"] or len(read_pages) != len(page_plan)
              else "PASS")
    return _write_outputs(out, {
        "schema": SCHEMA, "status": status, "profile_id": identity[0], "cutoff": cutoff, "timezone": identity[2],
        "candidate_only": True, "formal_scientific_values_added": 0, "legacy_display_values_loaded": 0,
        "inputs": pins, "source_project_work_items": len(routes),
        "unique_source_sha": len(groups), "unique_projects": len({route.project_id for route in routes}),
        "exact_source_sha_read": sum(row["source_integrity"] == "EXACT_SHA" for row in reviewed),
        "unique_source_documents_parsed": sum(row["parse_status"] == "PARSED" for row in reviewed),
        "unique_source_pages_requested": len(page_plan), "unique_source_pages_inspected": len(read_pages),
        "total_native_pages_decoded": sum(row.get("native_pages_decoded", 0) for row in reviewed),
        "original_candidate_count_by_method": dict(row_types), "unique_native_structural_tokens": len(native_tokens),
        "unique_reviewed_binding_tokens": len(reviewed_tokens), "candidate_work_statuses": dict(statuses),
        "issues_by_work_item": dict(issues), "reviewed_binding_statuses": dict(binding_status),
        "reviewed_source_project_pairs_outside_queue": [
            {"source_sha256": digest, "project_id": project_id} for digest, project_id in outside
        ],
        "documents": reviewed,
    }, results, candidates)

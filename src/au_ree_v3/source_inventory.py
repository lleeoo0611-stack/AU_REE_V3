"""Inventory exact source bodies and rank evidence leads without admitting observations."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

from .contracts.temporal import evaluate_temporal
from .documents import DocumentCache, DocumentError, OCRConfig
from .registry import load_profile, load_registry
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_SCHEMA, validate_edition_proofs


SCHEMA = "source-content-inventory-v1"
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_ZIP_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ZIP_TABLE_BYTES = 16 * 1024 * 1024
MAX_ZIP_MEMBERS = 2048
MAX_PASSAGES = 5

NUMBER = re.compile(r"(?<!\w)[<>≤≥]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
UNITS = re.compile(r"\b(?:mt|kt|ppm|kg/t|g/t|tpa|t/yr|(?:million )?tonnes?)\b|%", re.I)
RESOURCE = re.compile(r"\b(?:mineral resources?|resource estimate|MRE)\b", re.I)
JORC = re.compile(r"\bJORC(?:\W+code)?\W*2012\b|\b2012\W+JORC\b", re.I)
TABLE = re.compile(r"\b(?:table\s+[\d.]+|tonnage|classification|cut[- ]?off)\b", re.I)
TOTAL = re.compile(r"^\s*(?:grand\s+)?(?:totals?|combined|subtotals?)\b", re.I | re.M)
YEAR = re.compile(r"\b(?:19|20)[0-9]{2}\b")
OXIDE = re.compile(
    r"\b(?:TREO|REO|rare earth oxides?|Nd\s*2\s*O\s*3|Pr\s*6\s*O\s*11|"
    r"Dy\s*2\s*O\s*3|Tb\s*4\s*O\s*7)\b", re.I
)
MAGNET_OXIDES = tuple(re.compile(term, re.I) for term in
                      (r"\bNd\s*2\s*O\s*3\b", r"\bPr\s*6\s*O\s*11\b",
                       r"\bDy\s*2\s*O\s*3\b", r"\bTb\s*4\s*O\s*7\b"))
COMPARABILITY_OBSTACLES = {
    "EXPLORATION_TARGET_TERM": re.compile(r"\bexploration targets?\b", re.I),
    "ORE_RESERVE_TERM": re.compile(r"\b(?:ore|mineral) reserves?\b", re.I),
    "SCANDIUM_OR_COMBINED_BASIS_TERM": re.compile(r"\bscandium\b|\bSc\s*2\s*O\s*3\b", re.I),
    "HEAVY_MINERAL_OR_CONCENTRATE_TERM": re.compile(
        r"\bheavy minerals?\b|\bHM\b|\bconcentrate\b|\bmineral sands?\b", re.I
    ),
    "CLASSIFICATION_COMPONENTS_PRESENT": re.compile(r"\b(?:measured|indicated|inferred)\b", re.I),
}
SIGNALS = {
    "JORC_MRE": RESOURCE,
    "ORE_RESERVE": COMPARABILITY_OBSTACLES["ORE_RESERVE_TERM"],
    "REE_OXIDES": OXIDE,
    "TECHNICAL_PROCESS": re.compile(
        r"\b(?:metallurg\w*|hydrometallurg\w*|beneficiation|flowsheets?|pilot plants?|"
        r"leach(?:ing)?|flotation|recovery rates?)\b", re.I
    ),
    "MILESTONE": re.compile(
        r"\b(?:pre[- ]?feasibility|definitive feasibility|bankable feasibility|"
        r"PFS|DFS|scoping stud(?:y|ies)|final investment decision)\b", re.I
    ),
    "COORDINATES": re.compile(
        r"\b(?:latitude|longitude|easting|northing|GDA(?:94|2020)|EPSG:\s*\d+)\b", re.I
    ),
    "PRODUCTION_PLAN": re.compile(
        r"\b(?:annual production|production target|tonnes per annum|nameplate|tpa|production schedule)\b", re.I
    ),
    "PORT_INFRASTRUCTURE": re.compile(r"\b(?:ports?|shipping|export terminal)\b", re.I),
}
DIRECT_OFFICIAL_HOSTS = {
    "announcements.asx.com.au", "www.asx.com.au", "www.londonstockexchange.com",
    "wcsecure.weblink.com.au", "hermes-cdn.investorhub.com", "api.investi.com.au",
}


@dataclass(frozen=True)
class _CheckedBody:
    """Share the already verified original with the capture-proof validator."""

    store: ContentStore
    digest: str
    body: bytes

    def read(self, digest: str) -> bytes:
        return self.body if digest == self.digest else self.store.read(digest)


def _excerpt(text: str) -> tuple[str, bool]:
    text = text.strip("\n\r")
    return text[:1200], len(text) > 1200


def _find_passages(lines: list[str], location, container: str) -> tuple[dict, set[str]]:
    """Locate keyword and table evidence for review on its actual page or record."""
    snippets = defaultdict(list)
    terms = set()
    joined = "\n".join(lines)
    if not joined.strip():
        return snippets, terms
    has_resource = bool(RESOURCE.search(joined))
    has_jorc = bool(JORC.search(joined))
    has_oxide = bool(OXIDE.search(joined))
    has_composition = has_oxide and all(pattern.search(joined) for pattern in MAGNET_OXIDES)
    heading = has_resource + has_jorc + bool(TABLE.search(joined))
    for i, line in enumerate(lines):
        found = [name for name, pattern in SIGNALS.items() if pattern.search(line)]
        total_row = bool(TOTAL.search(line) and NUMBER.search(line))
        if not found and not (total_row and has_resource and has_oxide):
            continue
        lo, hi = max(0, i - 2), min(len(lines), i + 3)
        window = "\n".join(lines[lo:hi])
        n_values = len(NUMBER.findall(window))
        has_units = bool(UNITS.search(window))
        obstacles = sorted(name for name, pattern in COMPARABILITY_OBSTACLES.items() if pattern.search(window))
        if total_row and heading >= 2 and "JORC_MRE" not in found:
            found.append("JORC_MRE")
        for signal in found:
            if signal == "JORC_MRE" and not (RESOURCE.search(window) or total_row):
                continue
            score = 1 + min(n_values, 4) + 2 * has_units
            signals = []
            if signal == "JORC_MRE":
                score += 2 * heading + (4 if total_row else 0)
                if has_oxide and (total_row or n_values > 1):
                    signals.append("R1")
            if signal == "REE_OXIDES" and has_composition:
                score += 5
                signals.append("S1")
            if signal == "TECHNICAL_PROCESS" and n_values:
                signals.append("T1")
            if signal == "MILESTONE" and YEAR.search(window):
                signals.append("D1")
            if signal in {"COORDINATES", "PORT_INFRASTRUCTURE"}:
                signals.append("I4")
            quote, truncated = _excerpt(window)
            row = {
                "locator": location(lo + 1, hi), "container": container, "signal": signal,
                "candidate_criteria": signals, "score": score, "quote": quote,
                "quote_truncated": truncated, "obstacle_terms": obstacles,
                "jorc_2012_marker_in_container": has_jorc,
            }
            if not any(item["locator"] == row["locator"] for item in snippets[signal]):
                snippets[signal].append(row)
            terms.update(obstacles)
    return snippets, terms


def _take_top(passages: dict[str, list[dict]]) -> dict[str, list[dict]]:
    return {
        kind: sorted(rows, key=lambda item: (-item["score"], item["container"], item["locator"]))[:MAX_PASSAGES]
        for kind, rows in sorted(passages.items()) if rows
    }


def _merge_passages(target: dict, source: dict, *, prefix: str = "") -> None:
    for key, rows in source.items():
        target[key].extend({**row, "locator": prefix + row["locator"]} for row in rows)


def _pdf_summary(cache: DocumentCache, body: bytes, digest: str) -> tuple[dict, dict, set[str]]:
    view = cache.pdf(body, digest)
    hits = defaultdict(list)
    obstacles = set()
    blank = []
    for page, text in enumerate(view.pages, start=1):
        if not text.strip():
            blank.append(page)
            continue
        found, terms = _find_passages(
            text.splitlines(), lambda start, end: f"PDF page {page}, lines {start}-{end}", str(page),
        )
        _merge_passages(hits, found)
        obstacles.update(terms)
    status = "NATIVE_TEXT" if not blank else "NATIVE_TEXT_PARTIAL" if len(blank) < len(view.pages) else "NATIVE_TEXT_EMPTY"
    return ({
        "status": status, "physical_pages": len(view.pages),
        "native_text_pages": len(view.pages) - len(blank), "empty_native_pages": blank,
        "runtime": view.runtime, "ocr_status": "NOT_RUN" if blank else "NOT_REQUIRED",
    }, hits, obstacles)


def _html_summary(cache: DocumentCache, body: bytes, digest: str) -> tuple[dict, dict, set[str]]:
    doc = cache.html(body, digest)
    found, obstacles = _find_passages(
        [item.text for item in doc.fragments],
        lambda start, end: f"{doc.locate(start).locator} through {doc.locate(end).locator}", "HTML",
    )
    return {"status": "TEXT_INDEXED" if doc.fragments else "TEXT_EMPTY", "fragments": len(doc.fragments)}, found, obstacles


def _csv_summary(cache: DocumentCache, body: bytes, digest: str) -> tuple[dict, dict, set[str]]:
    doc = cache.csv(body, digest)
    headers = doc.rows[0]
    hits, obstacles = _find_passages(
        list(headers), lambda start, end: f"CSV header fields {start}-{end}", "CSV",
    )
    for item in hits.get("COORDINATES", []):
        item["candidate_criteria"] = []
    for ordinal, values in enumerate(doc.rows[1:], start=2):
        joined = " ".join(values)
        if not any(pattern.search(joined) for pattern in SIGNALS.values()):
            continue
        line_start, line_end = doc.physical_lines[ordinal - 1]
        found, terms = _find_passages(
            list(values),
            lambda first, last: f"CSV record {ordinal}, fields {first}-{last}, lines {line_start}-{line_end}",
            f"CSV record {ordinal}",
        )
        _merge_passages(hits, found)
        obstacles.update(terms)
    return {
        "status": "TABLE_INDEXED", "records_including_header": len(doc.rows),
        "field_count": len(headers), "headers": list(headers),
        "first_data_line": doc.physical_lines[1][0] if len(doc.rows) > 1 else None,
    }, hits, obstacles


def _zip_members(archive: ZipFile):
    members = archive.infolist()
    if len(members) > MAX_ZIP_MEMBERS or sum(item.file_size for item in members) > MAX_ZIP_EXPANDED_BYTES:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ZIP_DECLARED_SIZE_LIMIT")
    names = set()
    for item in members:
        path = PurePosixPath(item.filename)
        mode = item.external_attr >> 16
        if (not item.filename or path.is_absolute() or ".." in path.parts or "\\" in item.filename
                or ":" in item.filename or str(path) in names or stat.S_ISLNK(mode)
                or item.file_size > MAX_ZIP_MEMBER_BYTES or item.flag_bits & 1):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ZIP_MEMBER_POLICY_REJECTED")
        names.add(str(path))
    return members


def _zip_summary(cache: DocumentCache, body: bytes, digest: str) -> tuple[dict, dict, set[str]]:
    hits, obstacles, rows = defaultdict(list), set(), []
    with ZipFile(io.BytesIO(body)) as archive:
        for member in _zip_members(archive):
            if member.is_dir():
                continue
            suffix = PurePosixPath(member.filename).suffix.lower()
            row = {"member": member.filename, "bytes": member.file_size, "kind": suffix.lstrip(".")}
            with archive.open(member) as stream:
                header = stream.read(128)
                # Read every member through CRC validation. Large binaries stay in the archive.
                if suffix in {".csv", ".pdf", ".prj"} and member.file_size <= MAX_ZIP_TABLE_BYTES:
                    payload = header + stream.read()
                else:
                    for _ in iter(lambda: stream.read(1024 * 1024), b""):
                        pass
                    payload = None
            row["integrity"] = "CRC_VERIFIED"
            if suffix == ".shp" and len(header) >= 100:
                row.update(shape_type=int.from_bytes(header[32:36], "little"), geometry_role="ARCHIVE_GEOMETRY_UNBOUND")
            elif suffix == ".dbf" and len(header) >= 32:
                row["declared_records"] = int.from_bytes(header[4:8], "little")
            elif suffix == ".prj" and payload:
                row["crs_wkt"] = payload.decode("ascii", errors="replace").strip()
            elif payload is not None and suffix in {".csv", ".pdf"}:
                member_sha = sha256(payload).hexdigest()
                decoder = _csv_summary if suffix == ".csv" else _pdf_summary
                try:
                    parsed, found, terms = decoder(cache, payload, member_sha)
                    row.update(member_sha256=member_sha, parse=parsed)
                    _merge_passages(hits, found, prefix=f"ZIP {member.filename} ({member_sha}) → ")
                    obstacles.update(terms)
                except DocumentError as error:
                    row.update(parse={"status": error.outcome, "reason": str(error)}, member_sha256=member_sha)
            rows.append(row)
    return {"status": "ARCHIVE_INDEXED", "members": rows, "source_sha256": digest}, hits, obstacles


DECODERS = {"pdf": _pdf_summary, "html": _html_summary, "csv": _csv_summary, "zip": _zip_summary}


def _verify_edition(edition: dict, object_reader: _CheckedBody, cutoff: str, timezone: ZoneInfo) -> dict:
    current_id = edition["receipt_sha256"]
    try:
        proofs = validate_edition_proofs(object_reader, edition, timezone)
        if not proofs:
            raise IntegrityError("EDITION_CAPTURE_PROOF_MISSING")
        recomputed = evaluate_temporal(
            source_sha256=object_reader.digest, cutoff=cutoff, observation_role="DOCUMENT",
            publication_date=edition["publication_date"], acquired_at=edition["acquired_at"],
            reacquired_at=edition["reacquired_at"], evidence=proofs,
        )
        if json.dumps(recomputed.to_dict(), sort_keys=True) != json.dumps(edition["temporal_decision"], sort_keys=True):
            raise IntegrityError("TEMPORAL_DECISION_RECOMPUTE_MISMATCH")
    except (KeyError, OSError, ValueError, TypeError) as error:
        return {"status": "BLOCKED", "reason": str(error), "receipt_sha256": current_id}
    return {
        "status": recomputed.status, "reason": recomputed.reason, "decision_id": recomputed.decision_id,
        "receipt_sha256": current_id, "matching_evidence_ids": list(recomputed.matching_evidence_ids),
        "earliest_proven_public_date": recomputed.earliest_proven_public_date,
    }


def _inventory_one(item, store: ContentStore, cutoff: str, timezone: ZoneInfo) -> dict:
    digest, editions = item
    kinds = {edition["kind"] for edition in editions}
    projects = sorted({p for edition in editions for p in edition["project_ids"]})
    row = {
        "source_sha256": digest, "edition_ids": sorted({edition["edition_id"] for edition in editions}),
        "project_ids": projects, "project_binding": "CAPTURE_ASSOCIATION_ONLY", "deposit_scope": "UNKNOWN",
        "source_urls": sorted({edition["url"] for edition in editions}),
        "document_kind": next(iter(kinds)) if len(kinds) == 1 else "CONFLICT",
        "integrity": "UNVERIFIED", "parse": {"status": "NOT_RUN"},
        "temporal_editions": [], "content_signals": {term: "UNKNOWN" for term in SIGNALS},
        "passages": {}, "obstacle_terms": [],
    }
    try:
        if store.path(digest).stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "BODY_SIZE_LIMIT")
        body = store.read(digest)
    except (OSError, IntegrityError, DocumentError) as error:
        return {**row, "integrity": "FAIL" if isinstance(error, IntegrityError) else "UNAVAILABLE",
                "parse": {"status": "BLOCKED", "reason": str(error)}}
    row.update(integrity="EXACT_SHA", bytes=len(body))
    checked = _CheckedBody(store, digest, body)
    row["temporal_editions"] = [
        _verify_edition(edition, checked, cutoff, timezone) for edition in sorted(editions, key=lambda e: e["edition_id"])
    ]
    decoder = DECODERS.get(row["document_kind"])
    if decoder is None:
        row["parse"] = {"status": "BLOCKED", "reason": "UNSUPPORTED_OR_CONFLICTING_DOCUMENT_KIND"}
        return row
    try:
        parsed, passages, obstacles = decoder(DocumentCache(), body, digest)
    except (DocumentError, UnicodeError, OSError, BadZipFile, RuntimeError, NotImplementedError) as error:
        row["parse"] = {"status": "BLOCKED", "reason": str(error)}
        return row
    row["parse"] = parsed
    row["passages"] = _take_top(passages)
    row["obstacle_terms"] = sorted(obstacles)
    row["content_signals"].update({kind: "CANDIDATE_PASSAGE_PRESENT" for kind in row["passages"]})
    return row


def _priority(sources: list[dict], known: set[tuple[str, str]]) -> list[dict]:
    work = []
    for row in sources:
        best = {}
        for passage in (p for group in row["passages"].values() for p in group):
            for criterion in passage["candidate_criteria"]:
                if criterion not in best or passage["score"] > best[criterion]["score"]:
                    best[criterion] = passage
        eligible = any(value["status"] == "ELIGIBLE" for value in row["temporal_editions"])
        for criterion, passage in best.items():
            newly_covered = sorted(project for project in row["project_ids"] if (project, criterion) not in known)
            work.append({
                "source_sha256": row["source_sha256"], "criterion_id": criterion,
                "project_ids": row["project_ids"], "potential_new_project_ids": newly_covered,
                "eligible_source_edition_seen": eligible, "association_only": True,
                "estimated_evidence_strength": passage["score"],
                "processing_cost": "ALREADY_NATIVE_INDEXED", "best_locator": passage["locator"],
                "obstacle_terms": passage["obstacle_terms"], "status": "REVIEW_REQUIRED",
            })
    work.sort(key=lambda item: (
        not item["eligible_source_edition_seen"], not item["potential_new_project_ids"],
        len(item["project_ids"]) != 1, -item["estimated_evidence_strength"], item["criterion_id"],
        item["source_sha256"],
    ))
    return [{"priority": index, **item} for index, item in enumerate(work, start=1)]


def _official(url: str, seed_index: dict) -> bool:
    host = urlsplit(url).hostname or ""
    channel = seed_index.get(url, {}).get("channel")
    return (host in DIRECT_OFFICIAL_HOSTS or host.endswith(".gov.au")
            or channel in {"OFFICIAL_EXCHANGE", "OFFICIAL_GOVERNMENT", "ISSUER_DISTRIBUTION"})


def _failures(events: list[dict], alternatives: list[dict], seed_index: dict,
              verified_captures: set[str]) -> list[dict]:
    failures = []
    tried = {event["requested_url"] for event in events + alternatives}
    for event in events:
        if event["status"] not in {"MIME_MISMATCH", "HTTP_404"}:
            continue
        projects = set(event["project_ids"])
        failed_url = event["requested_url"]
        historical = set(seed_index.get(failed_url, {}).get("historical_sha256", ()))
        official, retries = [], []
        for attempt in events + alternatives:
            overlap = sorted(projects.intersection(attempt["project_ids"]))
            url = attempt["requested_url"]
            if not overlap or url == failed_url or not _official(url, seed_index):
                continue
            match = {
                "project_ids": overlap, "url": url, "status": attempt["status"],
                "http_status": attempt.get("http_status"), "mime": attempt.get("response_mime"),
            }
            if attempt["status"] == "PUBLIC_CAPTURED":
                digest = attempt["sha256"]
                if attempt["receipt_sha256"] not in verified_captures:
                    continue
                official.append({
                    **match, "sha256": digest, "receipt_sha256": attempt["receipt_sha256"],
                    "relation": "EXACT_HISTORICAL_BYTES" if digest in historical else "SAME_PROJECT_LINK_ONLY",
                })
            elif attempt in alternatives:
                retries.append(match)
        untried = sorted(
            url for url, seed in seed_index.items()
            if url not in tried and projects.intersection(seed["project_ids"]) and _official(url, seed_index)
        )
        failures.append({
            "project_ids": sorted(projects), "failed_url": failed_url, "final_url": event.get("final_url"),
            "status": event["status"], "http_status": event.get("http_status"),
            "response_mime": event.get("response_mime"), "expected_kind": event.get("expected_kind"),
            "recorded_reason": event.get("reason"), "historical_reference_sha256": sorted(historical),
            "captured_official_alternatives": official, "failed_official_attempts": retries,
            "untried_official_locator_candidates": untried,
            "alternate_resolution": "EXACT_HISTORICAL_BYTES_CAPTURED" if any(
                item["relation"] == "EXACT_HISTORICAL_BYTES" for item in official
            ) else "RELATED_OFFICIAL_SOURCE_ONLY" if official else "NO_VERIFIED_ALTERNATE",
        })
    return failures


def _write(path: Path, payload) -> str:
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    return sha256(data).hexdigest()


def _ocr_diagnostic(sources: list[dict], store: ContentStore, model_path: Path,
                    inventory_sha256: str, out: Path) -> str:
    config = OCRConfig(model_path)
    pages = []
    for source in sources:
        empty = source["parse"].get("empty_native_pages", ())
        if not empty:
            continue
        digest = source["source_sha256"]
        shared = {"source_sha256": digest, "project_ids": source["project_ids"]}
        try:
            document = DocumentCache().pdf(store.read(digest), digest, max(empty), ocr=config)
            by_page = {result.page: asdict(result) for result in document.ocr_pages}
        except (OSError, IntegrityError, DocumentError) as error:
            by_page = {page: {"page": page, "status": "BLOCKED", "reason": str(error)} for page in empty}
        for page in empty:
            result = by_page.get(page, {"page": page, "status": "NATIVE_TEXT_NOW_AVAILABLE"})
            pages.append({**shared, "locator": f"physical PDF page {page}", **result})
    return _write(out / "source_ocr_candidates.json", {
        "schema": "source-ocr-diagnostic-v1", "status": "CANDIDATE_ONLY",
        "source_content_inventory_sha256": inventory_sha256, "source_ocr_pages": pages,
    })


def inventory_sources(manifest_path: Path, source_store: Path, out: Path, *,
                      profile_path: Path | None = None, baseline_criteria: Path | None = None,
                      alternate_manifests: tuple[Path, ...] = (), workers: int = 2,
                      ocr_model: Path | None = None) -> dict:
    """Rehash original bytes and write uncertainty-preserving evidence retrieval receipts."""
    manifest_path, out = Path(manifest_path), Path(out)
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    alternate_bytes = [Path(path).read_bytes() for path in alternate_manifests]
    bundles = [manifest, *(json.loads(data) for data in alternate_bytes)]
    if any(bundle["schema"] != SOURCE_SCHEMA or any(bundle[field] != manifest[field] for field in (
        "profile_id", "cutoff", "timezone",
    )) for bundle in bundles):
        raise ValueError("Incompatible source manifest schema, cohort, cutoff or timezone")
    cutoff = manifest["cutoff"]
    timezone = ZoneInfo(manifest["timezone"])
    seed_index = {}
    project_labels = {}
    if profile_path:
        profile = load_profile(profile_path)
        if (profile.profile_id, profile.research_cutoff.isoformat(), profile.timezone.key) != (
            manifest["profile_id"], cutoff, timezone.key,
        ):
            raise ValueError("Research profile context differs from the source manifest")
        project_labels = {project.project_id: project.name
                          for project in load_registry(profile.registry_path).projects}
        if set(project_labels) != {project["project_id"] for project in manifest["projects"]}:
            raise ValueError("Research profile cohort differs from the source manifest")
        seeds = json.loads(profile.source_catalog_path.read_bytes())["seed_urls"]
        seed_index = {seed["url"]: seed for seed in seeds}
    baseline, baseline_blob_sha = None, None
    if baseline_criteria:
        baseline_bytes = Path(baseline_criteria).read_bytes()
        baseline_blob_sha = sha256(baseline_bytes).hexdigest()
        baseline = json.loads(baseline_bytes)
        if baseline["profile_id"] != manifest["profile_id"] or baseline["research_cutoff"] != cutoff:
            raise ValueError("Baseline criteria context differs from the source manifest")
    known = {
        (project, criterion) for project, cells in baseline["criteria_by_project"].items()
        for criterion, cell in cells.items() if cell["state"] == "COMPARABLE"
    } if baseline else set()
    grouped = defaultdict(dict)
    for bundle in bundles:
        for edition in bundle["editions"]:
            grouped[edition["sha256"]][edition["edition_id"], edition["receipt_sha256"]] = edition
    baseline_shas = {edition["sha256"] for edition in manifest["editions"]}
    store = ContentStore(Path(source_store))
    tasks = sorted((digest, list(editions.values())) for digest, editions in grouped.items())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        sources = list(pool.map(lambda item: _inventory_one(item, store, cutoff, timezone), tasks))
    for row in sources:
        row["project_labels"] = {project: project_labels[project] for project in row["project_ids"]
                                 if project in project_labels}
    verified = {row["source_sha256"] for row in sources if row["integrity"] == "EXACT_SHA"}
    time_checks = [edition for row in sources for edition in row["temporal_editions"]]
    verified_captures = {edition["receipt_sha256"] for edition in time_checks if edition["status"] != "BLOCKED"}
    alternative_events = [event for bundle in bundles[1:] for event in bundle["events"]]
    failures = _failures(manifest["events"], alternative_events, seed_index, verified_captures)
    priority = _priority(sources, known)
    projects = {pid for row in sources for pid in row["project_ids"]}
    native_pdfs = sum(row["parse"]["status"] in {"NATIVE_TEXT", "NATIVE_TEXT_PARTIAL"}
                      for row in sources if row["document_kind"] == "pdf")
    parse_failures = sum(row["parse"]["status"] == "BLOCKED" for row in sources)
    status = "PASS"
    if not grouped or len(verified) != len(grouped) or parse_failures or any(
        edition["status"] == "BLOCKED" for edition in time_checks
    ):
        status = "BLOCKED"
    if any(row["integrity"] == "FAIL" for row in sources):
        status = "FAIL"
    report = {
        "schema": SCHEMA, "status": status,
        "source_manifest_sha256": sha256(raw).hexdigest(), "profile_id": manifest["profile_id"],
        "alternate_source_manifest_sha256": [sha256(data).hexdigest() for data in alternate_bytes],
        "cutoff": cutoff, "timezone": timezone.key, "candidate_status": "RETRIEVAL_ONLY",
        "baseline_criteria_sha256": baseline_blob_sha,
        "baseline_comparable_project_criteria": len(known) if baseline else None,
        "counts": {
            "baseline_editions": len(manifest["editions"]), "baseline_unique_source_sha256": len(baseline_shas),
            "additional_source_sha256": len(grouped.keys() - baseline_shas), "unique_source_sha256": len(grouped),
            "projects": len(projects), "source_exact_sha_readback": len(verified),
            "source_kinds": dict(sorted(Counter(row["document_kind"] for row in sources).items())),
            "source_parse_statuses": dict(sorted(Counter(row["parse"]["status"] for row in sources).items())),
            "source_parse_failures": parse_failures,
            "pdfs_with_native_text": native_pdfs,
            "physical_pdf_pages": sum(row["parse"].get("physical_pages", 0) for row in sources),
            "temporal_edition_statuses": dict(sorted(Counter(edition["status"] for edition in time_checks).items())),
            "prior_get_failures": dict(sorted(Counter(row["status"] for row in failures).items())),
            "prior_get_failure_resolutions": dict(sorted(Counter(row["alternate_resolution"] for row in failures).items())),
            "candidate_work_items": len(priority),
            "candidate_signal_sources": {
                signal: sum(signal in row["passages"] for row in sources) for signal in SIGNALS
            },
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    report["inventory_sha256"] = _write(out / "source_content_inventory.json", {**report, "sources": sources})
    report["priority_sha256"] = _write(out / "source_content_priorities.json", priority)
    report["failure_ledger_sha256"] = _write(out / "source_failure_ledger.json", failures)
    if ocr_model is not None:
        report["ocr_diagnostic_sha256"] = _ocr_diagnostic(sources, store, ocr_model, report["inventory_sha256"], out)
    _write(out / "source_content_inventory_receipt.json", report)
    return report

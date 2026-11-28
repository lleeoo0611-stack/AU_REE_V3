"""Index source-bound text for review with SQLite FTS5 and BM25."""

from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory

from .contracts import canonical_json
from .documents import DocumentCache, DocumentError
from .oxide_table_search import METHOD as OXIDE_TABLE_METHOD, oxide_table_rows
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_MANIFEST

SCHEMA = "candidate-evidence-index-v1"
SEARCH_SCHEMA = "candidate-evidence-search-v1"
PURPOSE_QUERIES = {
    "R1": '"mineral resource" AND (TREO OR "rare earth oxides")',
    "S1": '(TREO OR "rare earth oxides") AND ("magnet" OR "magnetic" OR "neodymium" OR "NdPr")',
    "D1": '("scoping study" OR "pre feasibility" OR "feasibility study" OR construction OR production)',
    "GIS": '(latitude OR longitude OR easting OR northing OR "port distance")',
    "T1a": '(recovery OR extraction) AND (leach OR beneficiation OR metallurgical OR flowsheet OR flotation)',
    "T1b": '("product purity" OR "product quality" OR "concentrate grade" OR "product specification")',
    "T1c": '("reagent consumption" OR "water use" OR "energy consumption" OR "acid consumption")',
    "T1d": '("pilot plant" OR "bench scale" OR "variability testwork" OR "locked cycle")',
}
PURPOSE_POLICY = "candidate-query-dictionary-v1"
PURPOSE_POLICY_SHA = sha256(canonical_json(PURPOSE_QUERIES).encode()).hexdigest()
PDF_LINES = re.compile(r"PDF page (\d+), lines (\d+)-(\d+)")
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
SHA = re.compile(r"[0-9a-f]{64}")
MAX_BODY_BYTES = 64 * 1024 * 1024
DEFAULT_LIMIT = 20
SEARCH_STRATEGIES = ("bm25", "oxide-table")

DDL = """
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources(
    source_sha256 TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
    detail TEXT, decoder TEXT, span_count INTEGER NOT NULL
);
CREATE TABLE source_projects(
    source_sha256 TEXT NOT NULL REFERENCES sources(source_sha256),
    project_id TEXT NOT NULL,
    PRIMARY KEY (source_sha256, project_id)
);
CREATE TABLE spans(
    rowid INTEGER PRIMARY KEY, span_id TEXT NOT NULL UNIQUE,
    source_sha256 TEXT NOT NULL REFERENCES sources(source_sha256),
    locator TEXT NOT NULL, page INTEGER, line_start INTEGER, line_end INTEGER,
    original_text TEXT NOT NULL
);
CREATE VIRTUAL TABLE spans_fts USING fts5(
    original_text, content='spans', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);
"""


@dataclass(frozen=True)
class TextSpan:
    locator: str
    original_text: str
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None

    def span_id(self, source_sha256: str, decoder: str) -> str:
        return sha256(canonical_json([
            SCHEMA, PURPOSE_POLICY_SHA, source_sha256, decoder,
            self.locator, self.original_text,
        ]).encode()).hexdigest()


def _write(path: Path, data: dict) -> str:
    payload = (canonical_json(data) + "\n").encode()
    path.write_bytes(payload)
    return sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    with path.open("rb") as body:
        return file_digest(body, "sha256").hexdigest()


def _group_editions(manifest: dict) -> dict:
    grouped = defaultdict(list)
    cohort = {row["project_id"] for row in manifest["projects"]}
    for edition in manifest["editions"]:
        digest = edition["sha256"]
        if not SHA.fullmatch(digest):
            raise ValueError("Invalid source SHA in manifest")
        if set(edition["project_ids"]) - cohort:
            raise ValueError("Manifest edition Project associations must be in the fixed cohort")
        grouped[digest].append(edition)
    return grouped


def _pdf_spans(cache: DocumentCache, body: bytes, digest: str) -> tuple[str, tuple[TextSpan, ...]]:
    view = cache.pdf(body, digest)
    spans = []
    for page in range(1, len(view.pages) + 1):
        for block in view.blocks(page):
            match = PDF_LINES.fullmatch(block.locator)
            if match is None:
                raise DocumentError("CODE_ERROR", "PDF_BLOCK_LOCATOR_SCHEMA_CHANGED")
            spans.append(TextSpan(block.locator, block.text, *map(int, match.groups())))
    return view.runtime, tuple(spans)


def _html_spans(cache: DocumentCache, body: bytes, digest: str) -> tuple[str, tuple[TextSpan, ...]]:
    view = cache.html(body, digest)
    return "html-fragments-v1", tuple(TextSpan(block.locator, block.text) for block in view.fragments)


def _csv_spans(cache: DocumentCache, body: bytes, digest: str) -> tuple[str, tuple[TextSpan, ...]]:
    view = cache.csv(body, digest)
    spans = []
    for record, row in enumerate(view.rows, start=1):
        for column, value in enumerate(row, start=1):
            if value.strip():
                cell = view.locate(record, column)
                spans.append(TextSpan(cell.locator, cell.text, None, *view.physical_lines[record - 1]))
    return "csv-fields-v1", tuple(spans)


DECODERS = {"pdf": _pdf_spans, "html": _html_spans, "csv": _csv_spans}


def _load_spans(cas: ContentStore, digest: str, kind: str) -> tuple[str, str | None, str | None, tuple]:
    try:
        if cas.path(digest).stat().st_size > MAX_BODY_BYTES:
            return "BLOCKED", "SOURCE_EXCEEDS_INDEX_BYTE_LIMIT", None, ()
        body = cas.read(digest)
    except IntegrityError:
        return "FAIL", "SOURCE_SHA_MISMATCH", None, ()
    except OSError:
        return "BLOCKED", "SOURCE_BYTES_UNAVAILABLE", None, ()
    if kind not in DECODERS:
        return "UNINDEXED_CONTAINER", "NO_NATIVE_TEXT_VIEW_FOR_CONTAINER_KIND", None, ()
    try:
        decoder, spans = DECODERS[kind](DocumentCache(), body, digest)
    except (DocumentError, UnicodeError) as error:
        return "BLOCKED", str(error), None, ()
    return ("INDEXED" if spans else "EMPTY_TEXT"), None, decoder, spans


def _insert_sources(connection: sqlite3.Connection, grouped: dict, cas: ContentStore) -> list[dict]:
    source_rows = []
    for digest, editions in sorted(grouped.items()):
        projects = sorted({project for entry in editions for project in entry["project_ids"]})
        kinds = {entry["kind"] for entry in editions}
        kind = next(iter(kinds)) if len(kinds) == 1 else "CONFLICT"
        if kind == "CONFLICT":
            state, detail, decoder, spans = "FAIL", "SOURCE_KIND_CONFLICT", None, ()
        else:
            state, detail, decoder, spans = _load_spans(cas, digest, kind)
        connection.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                           (digest, kind, state, detail, decoder, len(spans)))
        connection.executemany("INSERT INTO source_projects VALUES (?, ?)",
                               ((digest, project) for project in projects))
        connection.executemany(
            "INSERT INTO spans(span_id, source_sha256, locator, page, line_start, line_end, original_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((span.span_id(digest, decoder), digest, span.locator, span.page,
              span.line_start, span.line_end, span.original_text) for span in spans),
        )
        source_rows.append({
            "source_sha256": digest, "kind": kind, "status": state, "reason": detail,
            "decoder": decoder, "spans": len(spans), "project_ids": projects,
            "edition_ids": sorted({entry["edition_id"] for entry in editions}),
        })
    return source_rows


def _index_metadata(content: bytes, manifest: dict) -> dict:
    metadata = {
        "schema": SCHEMA, "purpose_policy": PURPOSE_POLICY, "purpose_policy_sha256": PURPOSE_POLICY_SHA,
        "source_manifest_sha256": sha256(content).hexdigest(),
        "profile_id": manifest["profile_id"], "cutoff": manifest["cutoff"], "timezone": manifest["timezone"],
        "candidate_only": True, "sqlite_version": sqlite3.sqlite_version,
    }
    return metadata


def build_candidate_index(manifest: Path, store: Path, out: Path) -> dict:
    """Build a disposable read-only research aid, separate from the authoritative Store."""
    manifest, out = Path(manifest), Path(out)
    content = manifest.read_bytes()
    source_manifest = json.loads(content)
    if source_manifest["schema"] != SOURCE_MANIFEST:
        raise ValueError("Unsupported source manifest for candidate index")
    grouped = _group_editions(source_manifest)
    metadata = _index_metadata(content, source_manifest)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "candidate_text.sqlite"
    if path.exists():
        raise FileExistsError(f"Use an unused output directory for candidate index: {path}")
    with TemporaryDirectory(dir=out) as temp:
        staged = Path(temp) / path.name
        with closing(sqlite3.connect(staged)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                connection.executescript(DDL)
            except sqlite3.OperationalError as error:
                if "no such module: fts5" not in str(error):
                    raise
                receipt = {**metadata, "status": "BLOCKED", "reason": "SQLITE_FTS5_UNAVAILABLE"}
                _write(out / "candidate_index_receipt.json", receipt)
                return receipt
            with connection:
                rows = _insert_sources(connection, grouped, ContentStore(Path(store)))
                connection.execute("INSERT INTO spans_fts(spans_fts) VALUES('rebuild')")
                connection.execute("INSERT INTO spans_fts(spans_fts, rank) VALUES('integrity-check', 1)")
                connection.executemany(
                    "INSERT INTO metadata VALUES (?, ?)",
                    ((key, canonical_json(value)) for key, value in sorted(metadata.items())),
                )
            connection.execute("VACUUM")
        # Publish only a complete build, without replacing a prior candidate database.
        path.hardlink_to(staged)
    states = Counter(row["status"] for row in rows)
    receipt = {
        **metadata, "sources": rows,
        "status": "FAIL" if states["FAIL"] else "BLOCKED" if states["BLOCKED"] or not grouped else "PASS",
        "source_count": len(rows), "index_statuses": dict(sorted(states.items())),
        "span_count": sum(row["spans"] for row in rows),
        "index_sha256": _file_sha256(path),
    }
    _write(out / "candidate_index_receipt.json", receipt)
    return receipt


def _expression(purpose: str, query: str | None) -> str:
    if purpose not in PURPOSE_QUERIES:
        raise ValueError(f"Unknown candidate search purpose: {purpose}")
    if query is None:
        return PURPOSE_QUERIES[purpose]
    terms = TOKEN.findall(query)
    if not terms or len(query) > 200 or len(terms) > 20:
        raise ValueError("Supply 1 to 20 literal words with no more than 200 characters")
    return " AND ".join(f'"{token}"' for token in terms)


def _bm25_rows(connection, expression, project_id, limit):
    query_sql = """
        SELECT spans.*, bm25(spans_fts) AS bm25
          FROM spans_fts
          JOIN spans ON spans.rowid = spans_fts.rowid
         WHERE spans_fts MATCH ?
           AND (? IS NULL OR EXISTS (
               SELECT 1 FROM source_projects p
                WHERE p.source_sha256 = spans.source_sha256 AND p.project_id = ?
           ))
         ORDER BY bm25, spans.source_sha256, spans.locator, spans.span_id
         LIMIT ?
    """
    return connection.execute(query_sql, (expression, project_id, project_id, limit))


def search_candidates(index: Path, purpose: str, out: Path, *, query: str | None = None,
                      project_id: str | None = None, limit: int = DEFAULT_LIMIT, strategy: str = "bm25") -> dict:
    """Rank review leads without turning retrieval scores into formal observations."""
    index, out = Path(index), Path(out)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Candidate result limit must be 1 to 200")
    if strategy not in SEARCH_STRATEGIES:
        raise ValueError(f"Unknown candidate search strategy: {strategy}")
    if strategy == "oxide-table" and (purpose != "S1" or query is not None):
        raise ValueError("Oxide table search requires S1 and uses its own fixed layout query")
    expression = _expression(purpose, query) if strategy == "bm25" else None
    with closing(sqlite3.connect(index.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        metadata = {row["key"]: json.loads(row["value"]) for row in connection.execute("SELECT * FROM metadata")}
        if metadata.get("schema") != SCHEMA or metadata.get("purpose_policy_sha256") != PURPOSE_POLICY_SHA:
            raise ValueError("Candidate index has a different schema or purpose policy; rebuild")
        rows = (_bm25_rows(connection, expression, project_id, limit) if strategy == "bm25"
                else oxide_table_rows(connection, project_id, limit))
        hits = []
        for rank, row in enumerate(rows, start=1):
            record = dict(row)
            record.pop("rowid", None)
            projects = [item[0] for item in connection.execute(
                "SELECT project_id FROM source_projects WHERE source_sha256=? ORDER BY project_id",
                (record["source_sha256"],),
            )]
            hits.append({**record, "project_ids": projects, "rank": rank, "status": "CANDIDATE"})
    receipt = {
        **metadata, "schema": SEARCH_SCHEMA, "status": "PASS", "purpose": purpose,
        "index_sha256": _file_sha256(index),
        "query": query, "fts5_expression": expression, "project_filter": project_id,
        "limit": limit, "retrieval_method": "SQLite FTS5 BM25" if strategy == "bm25" else OXIDE_TABLE_METHOD,
        "matches": hits,
        "scientific_admissions": 0,
    }
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "candidate_search.json", receipt)
    return receipt

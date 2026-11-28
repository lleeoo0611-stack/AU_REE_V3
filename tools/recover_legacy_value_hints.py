"""Locate V2 Census numeric evidence in a V3 source index; never admit V2 values.

This is a read-only, source-edition exact, Shadow/Candidate research utility. The
V2 CSV is a *search hint* only. The resulting file cannot be used as a formal
observation or independent source of truth.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import csv
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
import sqlite3
import subprocess

V2_REF = "2af6f611e51eb4ac9dda73419d1afeba70d1307e"
V2_PROVENANCE_PATH = "results/ree-project-census/coverage-locked-20261125/field_provenance.csv"
V2_PROVENANCE_SHA256 = "f0a963ef1479f53546c614205c251be0c365adc748ce446fd659c4d57d033c6d"
SCHEMA = "v2-resource-value-rediscovery-v1"
INDEX_SCHEMA = "candidate-evidence-index-v1"
SHA = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
OXIDE = re.compile(r"^(?:La|Ce|Pr|Nd|Sm|Eu|Gd|Tb|Dy|Ho|Er|Tm|Yb|Lu|Y)\d*O\d+_氧化[^_]+_kt$")
RESOURCE_COLS = {"礦石蘊藏量_Mt", "TREO品位_pct", "總TREO蘊藏量_kt"}
MAX_HITS = 1600
PAGE_BUDGET = 24
CONTEXT_BUDGET = 32
# Separate source-target discovery (value vs composition) from context discovery;
# source/page scores are hints, never evidence validation.
QUERIES = {
    "resource": '"mineral resource" OR TREO OR tonnage',
    "composition": 'NdPr OR Nd2O3 OR Pr6O11 OR Dy2O3 OR Tb4O7 OR "rare earth oxide"',
    "context": 'JORC OR cutoff OR "cut off" OR "effective date"',
}


def _json(path: Path, value: dict) -> str:
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(content)
    return sha256(content).hexdigest()


def _read_v2(root: Path) -> tuple[str, list[dict[str, str]]]:
    run = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", f"{V2_REF}:{V2_PROVENANCE_PATH}"],
        check=True, capture_output=True,
    )
    content = run.stdout
    digest = sha256(content).hexdigest()
    if digest != V2_PROVENANCE_SHA256:
        raise ValueError("V2 provenance does not match immutable reference SHA")
    reader = csv.DictReader(StringIO(content.decode("utf-8-sig"), newline=""))
    required = {"project_id", "column", "value", "source_sha256", "pdf_page", "locator", "method", "scope"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("V2 provenance fields changed")
    rows = list(reader)
    keys = [(row["project_id"], row["column"]) for row in rows]
    if len(rows) != 4743 or len(set(keys)) != 4743:
        raise ValueError("V2 provenance must cover exactly 4743 distinct Census positions")
    if len({row["project_id"] for row in rows}) != 93 or len({row["column"] for row in rows}) != 51:
        raise ValueError("V2 provenance project and column axes do not match the frozen Census")
    return digest, rows


def _target(column: str) -> str | None:
    if column in RESOURCE_COLS:
        return "RESOURCE"
    if OXIDE.fullmatch(column):
        return "OXIDE"
    return None


def _pages(raw: str) -> list[int]:
    if not raw.strip():
        return []
    parts = re.split(r"[;,\s]+", raw.strip())
    if any(not re.fullmatch(r"[0-9]{1,5}", v) or not 1 <= int(v) <= 10000 for v in parts):
        return []
    return sorted({int(x) for x in parts})


def _rank_pages(conn: sqlite3.Connection, source_sha: str, query: str) -> list[int]:
    sql = """
        SELECT s.page, bm25(spans_fts) AS rank
          FROM spans_fts JOIN spans s ON s.rowid=spans_fts.rowid
         WHERE spans_fts MATCH ? AND s.source_sha256=? AND s.page IS NOT NULL
         ORDER BY rank, s.page, s.span_id LIMIT ?
    """
    ordered, seen = [], set()
    for page, _ in conn.execute(sql, (query, source_sha, MAX_HITS)):
        if page not in seen:
            seen.add(page)
            ordered.append(page)
    return ordered


def _interleave(left: list[int], right: list[int]) -> list[int]:
    result, seen = [], set()
    for j in range(max(len(left), len(right))):
        for side in (left, right):
            if j < len(side) and side[j] not in seen:
                seen.add(side[j]); result.append(side[j])
    return result


def _page_plan(conn: sqlite3.Connection, sha: str, fixed: tuple[int, ...]) -> dict:
    ranks = {name: _rank_pages(conn, sha, query) for name, query in QUERIES.items()}
    value = list(dict.fromkeys([*fixed, *_interleave(ranks["resource"], ranks["composition"])]))
    context = list(dict.fromkeys([*fixed, *ranks["context"]]))
    return {
        "seed_pages": list(fixed),
        "value_pages": value[:max(PAGE_BUDGET, len(fixed))],
        "context_pages": context[:max(CONTEXT_BUDGET, len(fixed))],
        "value_pages_over_budget": max(0, len(value) - max(PAGE_BUDGET, len(fixed))),
        "context_pages_over_budget": max(0, len(context) - max(CONTEXT_BUDGET, len(fixed))),
        "ranked_page_counts": {key: len(pages) for key, pages in ranks.items()},
    }


def run(*, v2_root: Path, index_path: Path, out: Path) -> dict:
    """Create read-only, source/Page keyed leads, never numeric observations."""
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("Recovery output must be a new empty directory; immutable prior results cannot be overwritten")
    if out.resolve().is_relative_to(v2_root.resolve()):
        raise ValueError("Recovery output must be outside the read-only V2 checkout")
    out.mkdir(parents=True, exist_ok=True)
    provenance_sha, legacy = _read_v2(v2_root)
    payload = index_path.read_bytes()
    index_sha = sha256(payload).hexdigest()
    records = []
    with closing(sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        metadata = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key,value FROM metadata")}
        if (metadata.get("schema") != INDEX_SCHEMA or metadata.get("candidate_only") is not True
                or not re.fullmatch(r"[0-9a-f]{64}", metadata.get("source_manifest_sha256", ""))):
            raise ValueError("A V3 candidate index with a pinned source manifest fingerprint is required")
        sources = {row["source_sha256"]: dict(row) for row in conn.execute("SELECT * FROM sources")}
        associations = {(row["source_sha256"], row["project_id"])
                        for row in conn.execute("SELECT source_sha256,project_id FROM source_projects")}
        page_cache = {}
        for row in legacy:
            role = _target(row["column"])
            if role is None or not row["value"].strip():
                continue
            shas = sorted(set(SHA.findall(row["source_sha256"])))
            pages = _pages(row["pdf_page"])
            result = {
                "project_id": row["project_id"], "column": row["column"],
                "legacy_method": row["method"],
                "legacy_pages": pages, "legacy_source_shas": shas, "quantity_group": role,
                "source_role": "V2_SEARCH_HINT_ONLY", "candidate_only": True, "formal_observation_created": False,
                "original_value_confirmed": False, "source_science_use_validated": False,
            }
            if not shas:
                result.update(status="NO_LEGACY_SOURCE_SHA", reason="SOURCE_PROVENANCE_ABSENT")
            elif len(shas) != 1:
                result.update(status="MULTI_SOURCE_CONTEXT_REQUIRED", reason="DO_NOT_JOIN_DIFFERENT_SOURCE_EDITIONS")
            else:
                sha = shas[0]
                source = sources.get(sha)
                if source is None:
                    result.update(status="SOURCE_EDITION_NOT_IN_V3", reason="SAME_SHA_NOT_ACQUIRED")
                elif (sha, row["project_id"]) not in associations:
                    result.update(status="PROJECT_SOURCE_ASSOCIATION_MISSING", reason="DO_NOT_INFER_PROJECT_IDENTITY")
                elif source["status"] != "INDEXED":
                    result.update(status="SOURCE_TEXT_NOT_INDEXED", reason=source["detail"] or source["status"])
                else:
                    indexed_pages = {r[0] for r in conn.execute(
                        "SELECT DISTINCT page FROM spans WHERE source_sha256=? AND page IS NOT NULL", (sha,))}
                    fixed = tuple(p for p in pages if p in indexed_pages)
                    unindexed = sorted(set(pages) - indexed_pages)
                    cache_key = (sha, fixed)
                    if cache_key not in page_cache:
                        page_cache[cache_key] = _page_plan(conn, sha, fixed)
                    result["page_plan"] = page_cache[cache_key]
                    result["source_sha256"] = sha
                    result["unindexed_legacy_pages"] = unindexed
                    result["page_evidence_role"] = "TEXT_PAGE_LOCATED_NOT_VALUE_VALIDATED"
                    result.update(status=("SOURCE_AND_LEGACY_PAGES_LOCATED" if fixed
                                          else "SOURCE_MATCH_RETRIEVAL_ONLY"),
                                  reason=("FIXED_PAGE_MISSING_IN_NATIVE_INDEX" if unindexed
                                          else "NEEDS_NEW_SOURCE_BOUND_ROW_EXTRACTION"))
            records.append(result)
    counts = dict(sorted(Counter(r["status"] for r in records).items()))
    # Work on each unique (SHA, Project) once, rather than rescanning the same
    # complete report for each projected or derived Census column.
    grouped = {}
    for row in records:
        if row["status"] not in {"SOURCE_AND_LEGACY_PAGES_LOCATED", "SOURCE_MATCH_RETRIEVAL_ONLY"}:
            continue
        key = row["source_sha256"], row["project_id"]
        queue = grouped.setdefault(key, {"source_sha256": key[0], "project_id": key[1],
            "target_columns": set(), "value_pages": set(), "context_pages": set(),
            "legacy_seed_pages": set(), "value_hints": 0})
        queue["target_columns"].add(row["column"])
        queue["value_pages"].update(row["page_plan"]["value_pages"])
        queue["context_pages"].update(row["page_plan"]["context_pages"])
        queue["legacy_seed_pages"].update(row["page_plan"]["seed_pages"])
        queue["value_hints"] += 1
    queue_rows = [{**{k: sorted(v) if isinstance(v, set) else v for k,v in queue.items()},
        "status": "CANDIDATE", "read_original_cas_again": True, "formal_admission": False}
        for queue in grouped.values()]
    queue_rows.sort(key=lambda x: (-len(x["target_columns"]), -len(x["legacy_seed_pages"]),
                                   x["source_sha256"], x["project_id"]))
    report = {
        "schema": SCHEMA, "status": "PASS", "candidate_only": True,
        "v2_ref": V2_REF, "v2_field_provenance_sha256": provenance_sha,
        "v3_candidate_index_sha256": index_sha, "source_manifest_sha256": metadata["source_manifest_sha256"],
        "index_schema": metadata["schema"], "project_cohort": 93,
        "numeric_value_hints": len(records), "statuses": counts,
        "unique_source_project_work_items": len(queue_rows),
        "unique_eligible_source_sha": len({x["source_sha256"] for x in queue_rows}),
        "value_page_budget": PAGE_BUDGET, "context_page_budget": CONTEXT_BUDGET,
        "independent_source_validation": "NOT_RUN", "formal_scientific_values_added": 0,
        "notes": "Page budgets apply per historical Census position; grouped work items keep the union. Legacy numbers, locator and scope text are excluded. Independently verify the manifest, source membership and original bytes before extracting.",
    }
    with (out / "legacy_value_leads.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for rec in records:
            stream.write(json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    report["leads_sha256"] = sha256((out / "legacy_value_leads.jsonl").read_bytes()).hexdigest()
    report["work_queue_sha256"] = _json(out / "source_project_work_queue.json",
                                     {"schema": "v2-source-project-rediscovery-queue-v1", "rows": queue_rows,
                                      "candidate_only": True, "formal_scientific_values_added": 0})
    _json(out / "legacy_value_recovery_receipt.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-root", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(v2_root=args.v2_root, index_path=args.index, out=args.out),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

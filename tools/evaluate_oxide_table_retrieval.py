"""Compare candidate retrieval under one fixed budget and sealed exact-source page panel."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import resource
from time import perf_counter

from au_ree_v3.candidate_search import search_candidates
from au_ree_v3.documents import DocumentCache
from au_ree_v3.sources import ContentStore


def _measures(counts):
    tp, fp, fn = (counts[key] for key in ("TP", "FP", "FN"))
    return {**{key: counts[key] for key in ("TP", "FP", "FN", "TN")},
            "precision": tp / (tp + fp) if tp + fp else "UNMEASURED",
            "recall": tp / (tp + fn) if tp + fn else "UNMEASURED"}


def _scope_hits(case, result, limit):
    pages = {}
    for row in result["matches"]:
        if row["source_sha256"] == case["source_sha256"] and row["page"] is not None:
            pages.setdefault(row["page"], row)
    return dict(tuple(pages.items())[:limit])


def _page_scores(case, pages):
    expected = {row["page"] for row in case["positive_pages"]}
    observed = set(pages)
    total = len(observed | expected)
    if not (observed | expected) <= set(range(1, case["pdf_pages"] + 1)):
        raise ValueError("Physical page exceeds the annotation's decoded original")
    return Counter(TP=len(observed & expected), FP=len(observed - expected),
                   FN=len(expected - observed), TN=case["pdf_pages"] - total)


def _anchor_key(row):
    return row["species"], row["line"], row["column"], row["end_column"], row["token"]


def _anchored(case, hits, document):
    expected = {(row["page"], *_anchor_key(field)) for row in case["positive_pages"] for field in row["fields"]}
    anchors = set()
    for page, report in hits.items():
        for header in (report, *report["additional_headers"]):
            quote_line = header["line_start"]
            if document.locate(page, line=quote_line).text != header["original_text"]:
                raise ValueError("Header locator does not reproduce the original")
            for row in (*header["printed_unit_lines"], header["nearby_numeric_row"]):
                if document.locate(page, line=row["line"]).text != row["text"]:
                    raise ValueError("Nearby row does not reproduce the original")
            for field in header["oxide_header_anchors"]:
                if document.locate(page, line=field["line"], column=field["column"],
                                   end_column=field["end_column"]).text != field["token"]:
                    raise ValueError("Oxide header token does not reproduce the original")
                anchors.add((page, *_anchor_key(field)))
    return Counter(TP=len(expected & anchors), FP=len(anchors - expected), FN=len(expected - anchors))


def _measure(args, panel, strategy):
    start = perf_counter()
    first_cpu = resource.getrusage(resource.RUSAGE_SELF)
    rows = []
    for case in panel["cases"]:
        result = search_candidates(args.index, "S1", args.out / "private" / strategy / case["source_sha256"],
                                   project_id=case["project_id"], limit=200, strategy=strategy)
        if (result["source_manifest_sha256"] != panel["source_manifest_sha256"]
                or result["scientific_admissions"] != 0):
            raise ValueError("Search result does not bind the sealed input or exceeded its candidate purpose")
        if len(result["matches"]) == 200:
            raise ValueError("Project result reached its cap before the common page budget")
        rows.append((case, _scope_hits(case, result, panel["page_budget_per_source"])))
    wall = perf_counter() - start
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (usage.ru_utime + usage.ru_stime) - (first_cpu.ru_utime + first_cpu.ru_stime)
    costs = {"wall_seconds": wall, "cpu_seconds": cpu, "peak_process_rss_kib": usage.ru_maxrss}

    pages, fields = Counter(), Counter()
    cache = DocumentCache()
    store = ContentStore(args.store)
    for case, hits in rows:
        pages.update(_page_scores(case, hits))
        if strategy == "oxide-table":
            digest = case["source_sha256"]
            document = cache.pdf(store.read(digest), digest, last_page=max(hits)) if hits else None
            fields.update(_anchored(case, hits, document))
    return {
        "page_metrics": _measures(pages),
        "printed_component_anchor_metrics": (
            {**_measures(fields), "TN": "UNMEASURED"} if strategy == "oxide-table" else "UNMEASURED"),
        "query_cost": costs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("index", "store", "panel", "out"):
        parser.add_argument(f"--{field}", required=True, type=Path)
    parser.add_argument("--panel-sha256", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--strategy", choices=("bm25", "oxide-table"), required=True)
    args = parser.parse_args()
    data = args.panel.read_bytes()
    if sha256(data).hexdigest() != args.panel_sha256:
        raise ValueError("Pre-frozen annotation SHA differs")
    panel = json.loads(data)
    result = _measure(args, panel, args.strategy)
    receipt = {
        "schema": "oxide-table-retrieval-panel-evaluation-v1",
        "status": "PASS",
        "code_commit": args.code_commit,
        "strategy": args.strategy,
        "annotation_sha256": args.panel_sha256,
        "source_manifest_sha256": panel["source_manifest_sha256"],
        "page_budget_per_source": panel["page_budget_per_source"],
        "report_families": len(panel["cases"]),
        "physical_pages": sum(item["pdf_pages"] for item in panel["cases"]),
        "formal_admission": "UNMEASURED",
        **result,
    }
    with (args.out / f"{args.strategy}_metrics.json").open("x") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()

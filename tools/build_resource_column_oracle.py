"""Rebind source-reviewed resource column annotations to exact PDF positions."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from au_ree_v3.documents import DocumentCache
from au_ree_v3.observations import digest
from au_ree_v3.sources import ContentStore, validate_edition_proofs
from au_ree_v3.table_geometry import open_table_geometry

PLAN = "resource-column-annotation-plan-v1"
SCHEMA = "resource-column-oracle-v1"
HELD = {"HISTORICAL_HELD", "FIXED_HOLDOUT_EXCLUDED"}
FIELD = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _literal_fields(line):
    return tuple(field.removesuffix("%") for field in re.split(r"\s{2,}", line.strip()) if FIELD.fullmatch(field))


def _verify_header(spec, lines):
    numbers = spec["header_lines"]
    if not numbers or any(type(n) is not int or not 1 <= n <= len(lines) for n in numbers):
        raise ValueError("Printed header must identify valid lines on the exact original page")
    header = "\n".join(lines[n - 1] for n in numbers)
    if sha256(header.encode()).hexdigest() != spec["header_sha256"]:
        raise ValueError("Frozen printed annotation header differs: " + spec["table_scope"])


def _annotate_page(source, page, poppler, geometry, specs):
    rows = []
    lines = poppler.locate(page).text.splitlines()
    original_rows = defaultdict(list)
    for row in geometry.page(page):
        original_rows[tuple(cell.text for cell in row.cells)].append(row)
    for spec in specs:
        _verify_header(spec, lines)
        for line in spec["body_lines"]:
            if type(line) is not int or not 1 <= line <= len(lines):
                raise ValueError("Printed observation must identify a valid line on the original page")
            fields = _literal_fields(lines[line - 1])
            if len(fields) != spec["width"] or len(original_rows[fields]) != 1:
                raise ValueError(f"Printed row does not bind uniquely: {source}, {page}, {line}: {fields}")
            geometry_row, = original_rows[fields]
            cells = []
            for index, original in enumerate(geometry_row.cells):
                if annotation := spec["columns"].get(str(index)):
                    cells.append({**annotation, "literal": original.text, "bbox": original.bbox})
            rows.append({
                "source_sha256": source, "physical_page": page, "project_id": spec["project_id"],
                "group_sha256": spec["group_sha256"], "partition": spec["partition"],
                "row_bbox": geometry_row.bbox, "row_locator": f"PDF page {page}, line {line}",
                "table_scope": spec["table_scope"], "header_sha256": spec["header_sha256"], "cells": cells,
            })
    return rows


def _verify_source_scopes(sources, manifest, store):
    captures = defaultdict(list)
    timezone = ZoneInfo(manifest["timezone"])
    for edition in manifest["editions"]:
        if edition["kind"] == "pdf" and edition["temporal_decision"]["status"] == "ELIGIBLE":
            captures[edition["sha256"]].append(edition)
    for source, pages in sources.items():
        required_projects = {r["project_id"] for annotations in pages.values() for r in annotations}
        verified = set()
        for edition in captures[source]:
            projects = set(edition["project_ids"])
            if (required_projects - verified) & projects and validate_edition_proofs(store, edition, timezone):
                verified.update(projects)
        if not required_projects <= verified:
            raise ValueError("Printed annotation lacks a verified capture for its exact Project")


def build(*, plan, split, manifest, store):
    """Read fixed printed annotations, exact bytes and native boxes without predictions."""
    if plan["schema"] != PLAN:
        raise ValueError("Unsupported printed annotation plan")
    sources = defaultdict(lambda: defaultdict(list))
    for annotation in plan["tables"]:
        source = annotation["source_sha256"]
        group = split[source]
        if group["partition"] != annotation["partition"] or group["partition"] not in HELD:
            raise ValueError("Printed annotation falls outside the frozen issuer partition")
        sources[source][annotation["physical_page"]].append({**annotation, "group_sha256": group["group_sha256"]})
    _verify_source_scopes(sources, manifest, store)
    rows = []
    cache = DocumentCache()
    for source, pages in sorted(sources.items()):
        body = store.read(source)
        poppler = cache.pdf(body, source, max(pages))
        with open_table_geometry(body, "pymupdf") as geometry:
            for page, annotations in sorted(pages.items()):
                rows.extend(_annotate_page(source, page, poppler, geometry, annotations))
    if len({(r["source_sha256"], r["physical_page"], r["row_bbox"]) for r in rows}) != len(rows):
        raise ValueError("Repeated physical benchmark row")
    counts = Counter(c["role"] for row in rows for c in row["cells"])
    return {
        "schema": SCHEMA, "selection": plan["population"], "label_procedure": plan["label_procedure"],
        "plan_sha256": digest(plan), "rows": rows,
        "coverage": {
            "original_editions": len(sources), "held_groups": len({r["group_sha256"] for r in rows}),
            "physical_rows": len(rows), "labelled_positions": sum(counts.values()),
            "resource_role_rows": sum(any(c["role"] != "OTHER" for c in row["cells"]) for row in rows),
            "positions_by_role": dict(sorted(counts.items())),
        },
    }


def _read_pinned(argument):
    filename, expected = argument
    payload = Path(filename).read_bytes()
    if sha256(payload).hexdigest() != expected:
        raise ValueError("Frozen benchmark input disagrees with its SHA-256: " + filename)
    return json.loads(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("plan", "split", "manifest"):
        parser.add_argument(f"--{key}", nargs=2, required=True, metavar=("PATH", "SHA256"))
    for key in ("store", "out"):
        parser.add_argument(f"--{key}", type=Path, required=True)
    args = parser.parse_args()
    result = build(plan=_read_pinned(args.plan), split=_read_pinned(args.split)["split"],
                   manifest=_read_pinned(args.manifest), store=ContentStore(args.store))
    with args.out.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    print(json.dumps(result["coverage"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

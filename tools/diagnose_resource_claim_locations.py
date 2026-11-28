"""Diagnose previously missed V2 claim positions from exact original PDF editions."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
from resource import RUSAGE_SELF, getrusage
from time import perf_counter
import unicodedata

from au_ree_v3.documents import DocumentError
from au_ree_v3.migration.baseline import BASELINES, PinnedRepository
from au_ree_v3.sources import ContentStore, IntegrityError

if __package__:
    from .compare_resource_table_structure import OLD_FAILURE, _original_body, _pinned, _reference_document
    from .historical_claim_rows import _NUMBER, _fold, _marker_pattern, _table_row
    from .historical_claim_rows import _claim_hints, locate_claim_row
else:
    from compare_resource_table_structure import OLD_FAILURE, _original_body, _pinned, _reference_document
    from historical_claim_rows import _NUMBER, _claim_hints, _fold, _marker_pattern, _table_row, locate_claim_row


SCHEMA = "historical-resource-claim-location-diagnosis-v1"
_CAPTION = re.compile(r"^\s*(?:Table|Appendix)\s+[A-Z]?\d+(?:[-.:]\d+)*(?:[.:–—-]|\s)", re.I)
_CAPTION_REFERENCE = re.compile(r"^(?:Table|Appendix)\s+[A-Z]?\d+(?:[-.:]\d+)*(?=\W|$)", re.I)
_WORD = re.compile(r"\b[^\W\d_]{2,}\b")
_PROSE = re.compile(
    r"\b(?:is|are|was|were|will|has|have|had|would|could|announced|reported|estimated|"
    r"contains?|completed|concluded|entered|increased|comprising|expected|represents?|indicate[sd]?)\b", re.I,
)
_ANCHORS = ("table_label", "row_label", "column_label", "unit_label", "scope_anchor")


def _clean(text):
    return " ".join(unicodedata.normalize("NFKC", text).replace("−", "-").split())


def _decimal(literal):
    normalized = _clean(literal)
    if re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", normalized):
        return Decimal(normalized.replace(",", ""))
    return None


def _compare_printed(candidate, expected):
    if _clean(candidate) == _clean(expected):
        return "EXACT_PRINTED_TOKEN"
    observed, target = _decimal(candidate), _decimal(expected)
    if observed is None or target is None:
        return "TARGET_TOKEN_NOT_COMPARED"
    if observed == target:
        return "EQUAL_VALUE_DIFFERENT_FORMAT"
    # Compare the original printed resolutions. Report proximity separately
    # and never substitute the historical token for the observed literal.
    interval = sum(Decimal("0.5").scaleb(value.as_tuple().exponent) for value in (observed, target))
    if abs(observed - target) <= interval:
        return "DIFFERENT_VALUES_OVERLAPPING_PRINTED_PRECISION"
    return "DIFFERENT_PRINTED_VALUE"


def _coordinates(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(x) not in (float, int) or not math.isfinite(x) for x in value)):
        return None
    x1, y1, x2, y2 = value
    return tuple(value) if x1 < x2 and y1 < y2 else None


def _intersection(first, second):
    return _coordinates(tuple(func(left, right) for func, left, right in
                              zip((max, max, min, min), first, second)))


def _inside(inner, outer):
    return _intersection(inner, outer) == inner


def _bbox_record(page, box, original):
    return {"box_pt": list(box), "text": original,
            "locator": f"PDF page {page}, top-left points [{', '.join(map(str, box))}]"}


def _anchor(page, binding, field, read_box):
    box_name = "unit_bbox" if field == "unit_label" else f"{field}_bbox"
    box = _coordinates(binding.get(box_name))
    expected = binding.get(field)
    if box is None or not isinstance(expected, str) or not expected.strip():
        return None
    original = _clean(read_box(box))
    record = _bbox_record(page, box, original)
    record["matches_hint"] = bool(_marker_pattern(_clean(expected).casefold()).search(original.casefold()))
    return record


def recheck_native_cell(page, claim, read_box):
    """Return only same-edition text read at historical geometry hints.

    A reproduced value is a location result. Resource and scientific acceptance
    still belong to the evidence and purpose-specific admission paths.
    """
    binding = claim.get("cell_binding") or {}
    if binding.get("mode") != "native_cell":
        return None
    row, column, table = (_coordinates(binding.get(f"{role}_bbox")) for role in ("row", "column", "table"))
    if binding.get("page") != page or not all((row, column, table)):
        return {"status": "HISTORICAL_GEOMETRY_UNUSABLE"}
    cell = _intersection(row, column)
    if cell is None or not _inside(cell, table):
        return {"status": "HISTORICAL_GEOMETRY_UNUSABLE"}
    original = _clean(read_box(cell))
    hits = tuple(match[0] for match in _NUMBER.finditer(original))
    expected = claim.get("original_token")
    value = _bbox_record(page, cell, original)
    if len(hits) == 1 and isinstance(expected, str):
        value["printed_numeric_token"] = hits[0]
        value["comparison"] = _compare_printed(hits[0], expected)
    anchors = {role: item for role in _ANCHORS if (item := _anchor(page, binding, role, read_box)) is not None}
    confirmed = all(anchors.get(role, {}).get("matches_hint", False)
                    for role in ("table_label", "row_label", "column_label"))
    return {"status": "REPRODUCED_ROW_AND_COLUMN_ANCHORS" if confirmed else "HISTORICAL_ANCHOR_NOT_REPRODUCED",
            "table_box_pt": list(table), "row_box_pt": list(row), "column_box_pt": list(column),
            "anchors": anchors, "cell": value, "expected_token": expected, "numeric_matches_in_box": len(hits)}


def _marker_lines(number, lines, markers):
    text = []
    ends = []
    actual_lines = []
    length = 0
    for index, line in enumerate(lines, 1):
        normalized = _fold(line)
        if not normalized:
            continue
        text.append(normalized)
        length += len(normalized) + 1
        ends.append(length)
        actual_lines.append(index)
    page = " ".join(text)
    results = []
    for ordinal, marker in enumerate(dict.fromkeys(_fold(marker) for marker in markers if marker.strip()), 1):
        pattern = _marker_pattern(marker)
        found = {
            (actual_lines[bisect_right(ends, match.start())], actual_lines[bisect_right(ends, match.end() - 1)])
            for match in pattern.finditer(page)
        }
        results.append({
            "marker_index": ordinal, "marker_sha256": sha256(marker.encode()).hexdigest(),
            "numeric": bool(_NUMBER.search(marker)),
            "lines": [list(positions) for positions in sorted(found)],
            "locators": [f"PDF page {number}, line {a}" if a == b else f"PDF page {number}, lines {a}-{b}"
                         for a, b in sorted(found)],
            "caption_starts": sorted(a for a, b in found if a == b and _CAPTION_REFERENCE.match(marker)
                                     and pattern.match(_fold(lines[a - 1]))),
        })
    return results


def _printed_grids(lines):
    captions = [index for index, line in enumerate(lines, 1) if _CAPTION.match(line)]
    grids = {}
    for start, stop in zip(captions, (*captions[1:], len(lines) + 1)):
        verified_rows = [i for i in range(start + 1, min(stop, start + 26)) if _table_row(lines[i - 1])]
        if len(verified_rows) >= 2:
            # Page-level references can match numbered captions more than once.
            # Only literal caption and original multi-column data rows support
            # attribution; surrounding prose stays outside the table evidence.
            grids.update((line, start) for line in (start, *verified_rows))
    return grids


def _page_placement(lines, markers):
    grids = _printed_grids(lines)
    unique = [(positions[0][0], item["numeric"]) for item in markers
              if len(positions := item["lines"]) == 1 and positions[0][0] == positions[0][1]]
    tables = {grids[line] for line, _ in unique if line in grids}
    for item in markers:
        captions = [line for line in item["caption_starts"] if grids.get(line) == line]
        if len(captions) == 1:
            tables.update(captions)
    prose = {line for line, _ in unique if not _table_row(lines[line - 1])
             and not _CAPTION.match(lines[line - 1])
             and len(_WORD.findall(lines[line - 1])) >= 5 and _PROSE.search(lines[line - 1])}
    numbered = {line for line, numeric in unique if numeric and line in grids and _table_row(lines[line - 1])}
    return tables, prose, numbered


def _cell_marker_positions(markers, read_box, cell):
    binding = cell.get("anchors", {})
    row = _fold(read_box(cell["row_box_pt"]))
    table = _fold(read_box(cell["table_box_pt"]))
    proof_headers = [value["text"] for key, value in binding.items()
                     if key in {"table_label", "column_label", "unit_label"} and value["matches_hint"]]
    headers = _fold(" ".join(proof_headers))
    flags = Counter()
    for marker in dict.fromkeys(_fold(marker) for marker in markers if marker.strip()):
        regex = _marker_pattern(marker)
        if regex.search(row):
            flags["row"] += 1
        elif regex.search(headers):
            flags["printed_header"] += 1
        elif regex.search(table):
            flags["elsewhere_in_table"] += 1
        else:
            flags["outside_anchored_table"] += 1
    return dict(flags)


def diagnose_page(number, text, native_text, has_raster, claim, read_box):
    if not text.strip() and not native_text.strip():
        return {"status": "NO_NATIVE_TEXT_RASTER_PRESENT" if has_raster else "NO_EXTRACTABLE_PAGE_TEXT"}
    lines = text.splitlines()
    markers = _marker_lines(number, lines, claim["page_markers"])
    evidence = {"markers": markers}
    if not all(marker["lines"] for marker in markers):
        return {**evidence, "status": "PAGE_MARKER_NOT_REPRODUCED"}
    tables, prose, numeric_rows = _page_placement(lines, markers)
    evidence["printed_caption_locators"] = [f"PDF page {number}, line {line}" for line in sorted(tables)]
    evidence["prose_locators"] = [f"PDF page {number}, line {line}" for line in sorted(prose)]
    evidence["numeric_row_locators"] = [f"PDF page {number}, line {line}" for line in sorted(numeric_rows)]
    if (cell := recheck_native_cell(number, claim, read_box)) is not None:
        evidence["native_box_readback"] = cell
        if cell["status"] == "REPRODUCED_ROW_AND_COLUMN_ANCHORS":
            comparison = cell.get("cell", {}).get("comparison")
            evidence["marker_regions"] = placement = _cell_marker_positions(claim["page_markers"], read_box, cell)
            if comparison in {"EQUAL_VALUE_DIFFERENT_FORMAT", "DIFFERENT_VALUES_OVERLAPPING_PRINTED_PRECISION"}:
                return {**evidence, "status": comparison}
            if comparison == "EXACT_PRINTED_TOKEN":
                if len(tables) > 1:
                    status = "BOXED_CELL_FOUND_MARKERS_SPAN_LABELLED_TABLES"
                elif placement.get("outside_anchored_table"):
                    status = "BOXED_CELL_FOUND_MARKERS_INCLUDE_OTHER_PAGE_CONTEXT"
                elif set(placement).difference({"row"}):
                    status = "BOXED_CELL_FOUND_MARKERS_SPAN_TABLE_ZONES"
                else:
                    status = "BOXED_CELL_FOUND_ROW_MARKER_RULE_UNMET"
                return {**evidence, "status": status}
            if comparison == "DIFFERENT_PRINTED_VALUE":
                return {**evidence, "status": comparison}
            if cell["numeric_matches_in_box"] > 1:
                return {**evidence, "status": "AMBIGUOUS_VALUE_WITHIN_REPRODUCED_BOX"}
    if len(tables) > 1:
        status = "MARKERS_SPAN_SEPARATELY_LABELLED_TABLES"
    elif prose and tables:
        status = "MARKERS_SPAN_PROSE_AND_TABLE"
    elif len(numeric_rows) > 1:
        status = "MARKERS_SPAN_NUMERIC_ROWS"
    elif prose and all(marker["lines"] and all(a == b and a in prose for a, b in marker["lines"])
                       for marker in markers):
        status = "MARKERS_IN_NARRATIVE_TEXT"
    elif any(len(marker["lines"]) > 1 for marker in markers):
        status = "AMBIGUOUS_PAGE_MARKER_POSITIONS"
    elif sum(marker["numeric"] for marker in markers) <= 1:
        status = "INSUFFICIENT_NUMERIC_ROW_WITNESSES"
    else:
        status = "ROW_RELATIONSHIP_UNRESOLVED"
    return {**evidence, "status": status}


def _frozen_groups(args):
    roster = _pinned(*args.prior)["claims"]
    if len(roster) != 691:
        raise ValueError("Prior receipt must cover the 691 frozen historical observations")
    target = {(row["project_id"], row["claim_index"]): row for row in roster if row["previous_status"] == OLD_FAILURE}
    if len(target) != 260:
        raise ValueError("Prior receipt must contain the 260 frozen row failures")
    registry = {record["project_id"] for record in _pinned(*args.registry)["projects"]}
    raw = PinnedRepository(args.v2_root).read("science", "data/claims.json")
    if raw is None or sha256(raw).hexdigest() != args.claims_sha256:
        raise ValueError("Pinned V2 claims SHA differs")
    group = defaultdict(list)
    for claim_id, claim, error in _claim_hints(json.loads(raw), registry):
        prior = target.pop((claim_id["project_id"], claim_id["claim_index"]), None)
        if prior is None:
            continue
        if error or any(prior[key] != value for key, value in claim_id.items()):
            raise ValueError("Prior claim identity differs from frozen V2 source")
        group[claim_id["source_sha256"]].append((claim_id, claim))
    if target or sum(map(len, group.values())) != 260:
        raise ValueError("Claim diagnosis does not cover the frozen target cohort")
    return group


def _analyze_source(body, source, records):
    import pymupdf

    document = _reference_document(body, source, max(row["physical_page"] for row, _ in records))
    pages = {}
    outputs = []
    with pymupdf.open(stream=body, filetype="pdf") as geometry:
        for claim_id, claim in records:
            page = claim_id["physical_page"]
            if page > len(document.pages) or page > geometry.page_count:
                outputs.append({**claim_id, "status": "PHYSICAL_PAGE_MISSING"})
                continue
            if page not in pages:
                native_page = geometry[page - 1]
                pages[page] = native_page, native_page.get_textpage(), document.locate(page).text
            native_page, native_text, poppler = pages[page]

            def read_box(box):
                return native_page.get_textbox(pymupdf.Rect(box), textpage=native_text)

            detail = diagnose_page(page, poppler, native_text.extractText(), bool(native_page.get_images()),
                                   claim, read_box)
            baseline = locate_claim_row(poppler, claim["page_markers"], claim.get("original_token"))[1]
            outputs.append({**claim_id, **detail, "baseline_replayed_status": baseline or "UNIQUE_MARKER_ROW"})
    return outputs, len(pages), len(document.pages), document.runtime


def evaluate(args):
    started = perf_counter()
    store = ContentStore(args.store)
    group = _frozen_groups(args)
    claims, runtime = [], set()
    pages = decoded = documents = 0
    for source, records in sorted(group.items()):
        try:
            body = _original_body(source, store, args.historical_assets)
            result, reviewed, read_pages, parser = _analyze_source(body, source, records)
        except FileNotFoundError:
            reason = "EXACT_ORIGINAL_MISSING"
        except OSError:
            reason = "EXACT_ORIGINAL_UNREADABLE"
        except IntegrityError:
            reason = "EXACT_ORIGINAL_SHA_MISMATCH"
        except DocumentError as error:
            reason = str(error)
        else:
            claims.extend(result)
            documents += 1
            pages += reviewed
            decoded += read_pages
            runtime.add(parser)
            continue
        claims.extend({**row, "status": reason} for row, _ in records)
    return {
        "schema": SCHEMA, "code_commit": args.code_commit, "frozen_v2_ref": BASELINES["refs"]["science"],
        "input_sha256": {"claims": args.claims_sha256, "prior": args.prior[1], "registry": args.registry[1]},
        "scope": "Historical target locations only; all results remain separate from scientific admission.",
        "backend_versions": {"PyMuPDF": version("PyMuPDF"), "Poppler": sorted(runtime)},
        "workload": {"claims": len(claims), "exact_sources": documents, "target_pages": pages, "decoded_pages": decoded},
        "status_counts": dict(Counter(row["status"] for row in claims)),
        "reproduced_baseline_failures": sum(row.get("baseline_replayed_status") == OLD_FAILURE for row in claims),
        "elapsed_seconds": perf_counter() - started, "peak_rss_kib": getrusage(RUSAGE_SELF).ru_maxrss,
        "claims": sorted(claims, key=lambda row: (row["project_id"], row["claim_index"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("store", "historical-assets", "v2-root", "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    for name in ("prior", "registry"):
        parser.add_argument(f"--{name}", required=True, nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--claims-sha256", required=True)
    parser.add_argument("--code-commit", required=True)
    args = parser.parse_args()
    receipt = evaluate(args)
    with args.out.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()

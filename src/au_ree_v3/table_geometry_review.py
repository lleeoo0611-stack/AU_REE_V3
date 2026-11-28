"""Replay reviewed PDF geometry against the verified Census display editions."""

from collections import defaultdict
from importlib.metadata import version
import re

from .census_resource_display import _scope_phrase, historical_resource_reviews
from .observations import NUMBER, digest
from .resource_extraction import reported_table_cutoff
from .resource_table_binding import _line_span

SCHEMA = "resource-table-geometry-review-v1"
_DISTRIBUTIONS = {"pymupdf": "PyMuPDF", "pdfplumber": "pdfplumber"}
_ROLES = {"ore_mt", "grade", "contained_treo_kt"}
_TOKEN = re.compile(NUMBER)


def _span(page, bbox, text):
    box = ", ".join(f"{edge:.2f}" for edge in bbox)
    return {"locator": f"PDF page {page}, bbox ({box}) pt", "text": text, "physical_page": page}


def _matching_row(rows, case):
    matches = [row for row in rows if row.table_kind == "MINERAL_RESOURCE" and row.caption_bbox
               and row.caption == case["caption"] and row.row_label == case["row_label"]]
    if len(matches) != 1:
        raise ValueError("Reviewed Project table and row must replay uniquely")
    return matches[0]


def _matching_cell(row, witness):
    matches = [cell for cell in row.cells
               if (cell.role, cell.header, cell.unit) == (witness["role"], witness["header"], witness["unit"])
               and cell.header_bbox and cell.unit_bbox and cell.unit_text and _TOKEN.fullmatch(cell.text)]
    if len(matches) != 1:
        raise ValueError("Reviewed resource value, column header and printed unit must replay uniquely")
    return matches[0]


def _table_context(rows, target, text_document):
    page = target.physical_page
    lines = text_document.locate(page).text.splitlines()
    caption_lines = [i for i, line in enumerate(lines, start=1)
                     if " ".join(line.split()) == " ".join(target.caption.split())]
    context = {}
    if len(caption_lines) == 1:
        caption = (_line_span(lines, caption_lines[0], page),)
        if cutoff := reported_table_cutoff(text_document.pages, caption, target.row_label):
            context.update(reported_cutoff=cutoff[0], reported_cutoff_locator=cutoff[1])
    body = [row for row in rows if row.caption_bbox == target.caption_bbox and row.bbox[3] <= target.bbox[1]
            and row.category and row.category.endswith("_ONLY") and row.row_label]
    if body and target.category == "TOTAL_UNSPECIFIED":
        context.update(reported_component_labels="; ".join(row.row_label for row in body),
                       reported_component_locators="; ".join(_span(page, row.bbox, row.text)["locator"] for row in body))
    return context


def _cell_record(row, cell, edition, project, method, runtime, table_context):
    page = row.physical_page
    caption = _span(page, row.caption_bbox, row.caption)
    scope = _scope_phrase({"caption_anchors": [caption]}, project)
    if scope is None:
        raise ValueError("Reviewed geometry caption does not identify the registered Project scope")
    value = _span(page, cell.bbox, cell.text)
    context = {
        "geometry_method": method, "geometry_runtime": runtime, "geometry_coordinate_unit": "pt",
        "reported_table_locator": caption["locator"],
        "reported_header_locator": _span(page, cell.header_bbox, cell.header)["locator"],
        "reported_unit_locator": _span(page, cell.unit_bbox, cell.unit_text)["locator"],
        **table_context,
    }
    if row.category and row.category != "TOTAL_UNSPECIFIED":
        context.update(reported_resource_category=row.category, reported_category_locator=_span(page, row.bbox, row.text)["locator"])
    return {
        "project_id": project.project_id,
        "source_sha256": edition["sha256"], "source_url": edition["url"],
        "source_capture_sha256": edition["receipt_sha256"], "source_edition_id": edition["edition_id"],
        "temporal_decision_id": edition["temporal_decision"]["decision_id"],
        "physical_page": page, "variable": cell.role, "chemical_basis": cell.chemical_basis,
        "native_unit": cell.unit,
        "candidate_id": digest([SCHEMA, project.project_id, edition["sha256"], value["locator"]]),
        "value_anchor": value, "row_anchor": _span(page, row.bbox, row.text),
        "header_anchor": _span(page, cell.header_bbox, cell.header), "unit_anchor": _span(page, cell.unit_bbox, cell.unit_text),
        "caption_anchors": [caption], "scope_status": "NAMED_REPORTED_TABLE",
        "display_scope": scope.text, "display_context": context,
    }


def _review_cases(review, editions, projects, manifest_sha):
    if review.get("schema") != SCHEMA or review.get("source_manifest_sha256") != manifest_sha:
        raise ValueError("Geometry review does not match the verified source manifest")
    method = review["method"]
    if method not in _DISTRIBUTIONS:
        raise ValueError("Unknown reviewed geometry method")
    runtime = version(_DISTRIBUTIONS[method])
    if runtime != review["runtime"]:
        raise ValueError("Geometry runtime does not match the reviewed version")
    grouped = defaultdict(list)
    public_keys = set()
    for case in review["cases"]:
        edition = editions.get(case["capture_sha256"])
        project = case["project_id"]
        page = case["physical_page"]
        if (edition is None or edition["kind"] != "pdf" or project not in edition["project_ids"]
                or edition["temporal_decision"]["status"] != "ELIGIBLE"
                or project not in projects or type(page) is not int or page < 1):
            raise ValueError("Geometry review needs a verified Project, publication and PDF page")
        for witness in case["cells"]:
            role = witness["role"]
            if role not in _ROLES or (project, role) in public_keys:
                raise ValueError("Geometry review needs unique supported Census quantities")
            public_keys.add((project, role))
        grouped[edition["sha256"]].append((case, edition, projects[project]))
    return method, runtime, grouped


def geometry_resource_reviews(review, *, editions, registry, manifest_sha, content, cache, archived):
    """Produce display-only records from exact reviewed geometry in the source bytes."""
    from .table_geometry import open_table_geometry

    projects = {project.project_id: project for project in registry.projects}
    method, runtime, grouped = _review_cases(review, editions, projects, manifest_sha)
    records = []
    for source, cases in sorted(grouped.items()):
        body = content.read(source)
        text_document = cache.pdf(body, source, max(case["physical_page"] for case, _, _ in cases))
        with open_table_geometry(body, method) as document:
            for case, edition, project in cases:
                rows = document.page(case["physical_page"])
                row = _matching_row(rows, case)
                context = _table_context(rows, row, text_document)
                for witness in case["cells"]:
                    cell = _matching_cell(row, witness)
                    if cell.role != "ore_mt" and cell.chemical_basis != "TREO":
                        raise ValueError("Only the printed TREO basis can display in a TREO Census column")
                    records.append(_cell_record(row, cell, edition, project, method, runtime, context))
    return tuple(historical_resource_reviews({"schema": SCHEMA, "observations": records}, editions, archived))

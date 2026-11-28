"""Read source-reviewed numeric witnesses for the Census candidate display."""

from collections import defaultdict
from math import isfinite
import re

from .census_resource_display import _original_phrase, _scope_phrase
from .observations import NUMBER, digest, exact_value
from .table_geometry import _mupdf_fragments, _word_in

RULE = "positioned-resource-display-witness-v1"
_TOKEN = re.compile(NUMBER)
_CITATION = re.compile(r"\bTable\s+\d+\b", re.I)
_STATEMENT = re.compile(
    rf"\b(?P<class>Inferred) Mineral Resource, reported at a cut-off grade of (?P<cutoff>{NUMBER}) ppm TREO "
    rf"stands at (?P<ore_mt>{NUMBER})\s*Mt at (?P<grade>{NUMBER}) ppm TREO for "
    rf"(?P<contained_treo_kt>{NUMBER})\s*t of contained REO \((?P<citation>Table \d+(?: and Table \d+)?)\)",
    re.I,
)
# These are literal source layouts, independently checked against the page.
_ROLES = {
    ("tonnes", "million"): ("ore_mt", "Mt", None),
    ("material", "mt"): ("ore_mt", "Mt", None),
    ("treo", "grade (ppm)"): ("grade", "ppm", "TREO"),
    ("treo", "contained tonnes"): ("contained_treo_kt", "t", "TREO"),
}


def _box(page, spec):
    from pymupdf import Rect

    if len(spec) != 4 or any(type(part) not in {float, int} or not isfinite(part) for part in spec):
        raise ValueError("A reviewed source rectangle needs four point coordinates")
    rect = Rect(spec)
    if not rect.is_valid or rect.is_empty or not page.rect.contains(rect):
        raise ValueError("Review rectangle outside its physical source page")
    return rect


def _literal(words, rect):
    selected = sorted((word for word in words if _word_in(word, rect)), key=lambda word: (word.bbox[1], word.bbox[0]))
    return " ".join(word.text for word in selected)


def _anchor(page, rect, text):
    address = ", ".join(f"{edge:.2f}" for edge in rect)
    return {"physical_page": page, "locator": f"PDF page {page}, bbox ({address}) pt", "text": text}


def _verify_marker(words, marker, rect, name):
    text = _literal(words, rect)
    if text != marker:
        raise ValueError(f"Reviewed {name} differs from the same-edition printed source")
    return text


def _proof(pdf, case, project):
    scope = case["scope"]
    original = _original_phrase(pdf, scope["page"], scope["text"])
    if original is None:
        raise ValueError("Reviewed resource scope must occur uniquely in the original")
    scope_anchor = {"physical_page": original.page, "locator": original.locator, "text": original.text}
    if _scope_phrase({"caption_anchors": [scope_anchor]}, project) is None:
        raise ValueError("Reviewed resource scope does not name the registered Project")
    matches = tuple(_STATEMENT.finditer(original.text)) if case.get("cite_tables") else ()
    if case.get("cite_tables") and (len(matches) != 1 or scope["page"] in {c["physical_page"] for c in case["cells"]}):
        raise ValueError("A cross-page resource table must be cited from one unique resource statement")
    return scope_anchor, matches[0] if matches else None


def _cell_anchors(page, words, field, scope, statement, physical_page):
    spec = field["regions"]
    table, row, column, label, heading, unit, caption = (
        _box(page, spec[name]) for name in ("table", "row", "column", "row_label", "header", "unit", "caption")
    )
    if (not all(table.contains(rect) for rect in (row, column, heading, unit, caption))
            or not row.contains(label) or label.x1 > column.x0
            or not column.contains(heading) or heading.y1 > row.y0 or unit.y1 > row.y0 or caption.y1 > heading.y0):
        raise ValueError("Printed witnesses do not enclose one reviewed resource table cell")
    observed_caption = _verify_marker(words, field["caption"], caption, "table caption")
    observed_label = _verify_marker(words, field["row_label"], label, "row label")
    header = _verify_marker(words, field["header"], heading, "column header")
    printed_unit = _verify_marker(words, field["unit_label"], unit, "unit")
    try:
        role, quantity_unit, chemistry = _ROLES[(header.casefold(), printed_unit.casefold())]
    except KeyError as error:
        raise ValueError("Printed header and unit do not establish a supported resource quantity") from error
    if field["role"] != role:
        raise ValueError("Reviewed quantity conflicts with the original header and unit")
    value_box = row & column
    value = _literal(words, value_box)
    if not _TOKEN.fullmatch(value):
        raise ValueError("Reviewed cell does not have one literal original numeric token")
    if statement is None:
        if physical_page != scope["physical_page"] or not scope["text"].startswith(observed_caption):
            raise ValueError("Uncited candidate requires one local, named resource table")
    elif (observed_caption not in _CITATION.findall(statement["citation"])
          or not _TOKEN.fullmatch(observed_label)
          or exact_value(observed_label) != exact_value(statement["cutoff"])
          or exact_value(value) != exact_value(statement[role])):
        raise ValueError("Printed cutoff row does not corroborate its cited resource statement")
    return role, quantity_unit, chemistry, {
        "value_anchor": _anchor(physical_page, value_box, value),
        "row_anchor": _anchor(physical_page, row, _literal(words, row)),
        "header_anchor": _anchor(physical_page, heading, header),
        "unit_anchor": _anchor(physical_page, unit, printed_unit),
        "caption_anchors": [_anchor(physical_page, caption, observed_caption), scope],
    }


def positioned_resource_records(cases, *, editions, projects, content, cache):
    """Recheck source bytes, scope citation and each physical value before display."""
    import pymupdf

    groups = defaultdict(list)
    seen = set()
    for case in cases:
        edition = editions.get(case["capture_sha256"])
        project = projects.get(case["project_id"])
        if (edition is None or project is None or edition["kind"] != "pdf"
                or project.project_id not in edition["project_ids"]
                or edition["temporal_decision"]["status"] != "ELIGIBLE" or not case["cells"]):
            raise ValueError("Reviewed display requires a verified same-edition Project capture")
        if type(case["scope"]["page"]) is not int or case["scope"]["page"] < 1:
            raise ValueError("Reviewed scope requires a one-based physical page")
        for field in case["cells"]:
            if type(field["physical_page"]) is not int or field["physical_page"] < 1:
                raise ValueError("Reviewed positions require one-based physical pages")
            key = project.project_id, field["role"]
            if key in seen:
                raise ValueError("Repeated Project and reviewed resource quantity")
            seen.add(key)
        groups[edition["sha256"]].append((edition, project, case))

    records = []
    for source, eligible in sorted(groups.items()):
        body = content.read(source)
        highest = max(max(case["scope"]["page"], *(field["physical_page"] for field in case["cells"]))
                      for _, _, case in eligible)
        pdf = cache.pdf(body, source, highest)
        with pymupdf.open(stream=body, filetype="pdf") as document:
            words = {}
            for edition, project, case in eligible:
                scope, statement = _proof(pdf, case, project)
                for field in case["cells"]:
                    page = field["physical_page"]
                    if page not in words:
                        words[page] = tuple(_mupdf_fragments(document[page - 1]))
                    role, unit, chemistry, anchors = _cell_anchors(document[page - 1], words[page], field,
                                                                   scope, statement, page)
                    context = {
                        "positioned_review_rule": RULE,
                        "reported_scope_locator": scope["locator"],
                        "reported_header_locator": anchors["header_anchor"]["locator"],
                        "reported_unit_locator": anchors["unit_anchor"]["locator"],
                    }
                    if statement is not None:
                        context.update(reported_cutoff=f"{statement['cutoff']} ppm TREO",
                                       reported_cutoff_locator=scope["locator"],
                                       reported_resource_category=statement["class"],
                                       reported_category_locator=scope["locator"])
                    records.append({
                        "project_id": project.project_id, "source_sha256": source, "source_url": edition["url"],
                        "source_capture_sha256": edition["receipt_sha256"],
                        "temporal_decision_id": edition["temporal_decision"]["decision_id"],
                        "physical_page": page, "variable": role, "native_unit": unit, "chemical_basis": chemistry,
                        "candidate_id": digest([RULE, project.project_id, source, anchors["value_anchor"]["locator"]]),
                        "scope_status": "NAMED_REPORTED_TABLE", "display_scope": scope["text"],
                        "display_context": context, **anchors,
                    })
    return tuple(records)

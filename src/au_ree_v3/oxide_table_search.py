"""Locate native magnet-oxide grid headers for original-source review."""

from itertools import groupby, islice
import re

from .observations import NUMBER, OXIDE_VARIABLES


METHOD = "native-magnet-oxide-headers-v1"
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_SPECIES = {name.casefold(): name for name in OXIDE_VARIABLES}
_FORMULA = re.compile(r"(?<!\w)(?:" + "|".join(_SPECIES) + r")(?!\w)", re.I)
_GUTTER = re.compile(r"\S[ \t]{2,}\S")
_UNIT = re.compile(r"%|\b(?:ppm|ppb|kg/t|g/t)\b", re.I)
_MEASUREMENT = re.compile(rf"(?<!\S)[<>≤≥+-]?{NUMBER}%?(?!\S)")
_MAX_HEADER_TO_ROW = 8

_PAGE_BLOCKS = """
    SELECT source_sha256, page, line_start, original_text, span_id
      FROM spans
     WHERE page IS NOT NULL
       AND (? IS NULL OR EXISTS (
           SELECT 1
             FROM source_projects p
            WHERE p.source_sha256 = spans.source_sha256 AND p.project_id = ?
       ))
     ORDER BY source_sha256, page, line_start
"""


def _page_lines(blocks):
    for block in blocks:
        for number, text in enumerate(block["original_text"].splitlines(), start=block["line_start"]):
            yield number, text, block["span_id"]


def _located_line(page, line, text, span_id):
    return {"line": line, "span_id": span_id, "text": text, "locator": f"PDF page {page}, line {line}"}


def _row_witness(lines, header, page):
    """Require printed concentration units before the first nearby numeric row."""
    header_line = lines[header][0]
    unit_lines = []
    for line, text, span_id in lines[header:header + _MAX_HEADER_TO_ROW + 1]:
        if line - header_line > _MAX_HEADER_TO_ROW:
            break
        if line > header_line and len(_MEASUREMENT.findall(text)) >= len(_SPECIES):
            if unit_lines:
                return _located_line(page, line, text, span_id), unit_lines
            break
        if _UNIT.search(text):
            unit_lines.append(_located_line(page, line, text, span_id))
    return None


def _headers(lines, page):
    for position, (line, text, span_id) in enumerate(lines):
        if not _GUTTER.search(text) or "+" in text:
            continue
        formulas = tuple(_FORMULA.finditer(text.translate(_SUBSCRIPTS)))
        if {match.group().casefold() for match in formulas} != _SPECIES.keys():
            continue
        witness = _row_witness(lines, position, page)
        if witness is None:
            continue
        measurement_row, unit_lines = witness
        anchors = [{
            "species": _SPECIES[match.group().casefold()],
            "line": line, "column": match.start() + 1, "end_column": match.end(),
            "token": text[match.start():match.end()],
            "locator": f"PDF page {page}, line {line}, columns {match.start() + 1}-{match.end()}",
        } for match in formulas]
        yield {
            "span_id": span_id, "locator": f"PDF page {page}, line {line}",
            "line_start": line, "line_end": line, "original_text": text,
            "oxide_header_anchors": anchors, "nearby_numeric_row": measurement_row,
            "printed_unit_lines": unit_lines,
            "interpretation": "PRINTED_HEADERS_ONLY",
        }


def _matching_pages(connection, project_id):
    blocks = connection.execute(_PAGE_BLOCKS, (project_id, project_id))
    for (source, page), page_blocks in groupby(blocks, key=lambda row: (row["source_sha256"], row["page"])):
        headers = tuple(_headers(tuple(_page_lines(page_blocks)), page))
        if headers:
            yield {"source_sha256": source, "page": page, **headers[0], "additional_headers": list(headers[1:])}


def oxide_table_rows(connection, project_id, limit):
    """Return one candidate per exact PDF page, in stable SHA and page order."""
    return islice(_matching_pages(connection, project_id), limit)

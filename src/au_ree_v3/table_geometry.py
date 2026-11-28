"""Candidate-only row geometry and source-aligned PDF table columns."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from math import isfinite
import re
from typing import Iterator

from .documents import DocumentError
from .normalization import KNOWN_OXIDE_SPECIES
from .resource_table_binding import (
    _CANDIDATE_VALUE, _INCOMPATIBLE_PARENT, _NON_ORE_DENOMINATOR, _NON_ORE_HEADER, _PHYSICAL_HOLDS, _RESERVE, _RESOURCE,
    _TARGET, _candidate_header_unit, _candidate_role, _plain,
)

RULE = "resource-table-coordinate-layout-v1"
METHODS = ("pymupdf", "pdfplumber")
PDFBox = tuple[float, float, float, float]
_NUMBERED_TITLE = re.compile(r"^(?:table|appendix)\s+[A-Z]?\d+(?:[.-]\d+)*\b", re.I)
_NAMED_RESOURCE = re.compile(r"^(?:[\w’'/-]+\s+){0,8}mineral resources?"
                             r"(?:\s+(?:estimates?|statement|summary|table))?[:.]?$", re.I)
_RARE_EARTH = re.compile(r"\b(?:TREO|REO|rare[ -]earths?|REE|Nd2O3|Pr6O11)\b", re.I)
_TOTAL = re.compile(r"\b(?:total|overall|combined|global)\b", re.I)
_SUBTOTAL = re.compile(r"\bsub[ -]?total\b", re.I)
_CLASS = re.compile(r"\b(?:measured|indicated|inferred|ind|inf)\b", re.I)
_CATEGORY = {"measured": "MEASURED", "indicated": "INDICATED", "inferred": "INFERRED",
             "ind": "INDICATED", "inf": "INFERRED"}
_UNIT_ONLY = re.compile(r"\s*[([\s]*(?:ppm|(?:k|m)?g\s*/\s*t|Bt|Mt|kt|t|%|[‘’']?000|billion|million|thousand)"
                        r"(?:\s+tonnes?)?[\])\s]*", re.I)
_SCALE = re.compile(r"^\s*\(?(?:million|billion|thousand|[‘’']?000)\)?\s*$", re.I)
_GRAMS_PER_TONNE = re.compile(r"(?<!\w)(?:g|mg)\s*/\s*t(?!\w)", re.I)
_BODY = re.compile(r"\b(?:will|could|expects|has been|we have|released|announced|published)\b", re.I)
_FORMULAS = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(r"\s*".join(re.escape(char) for char in part) for part in sorted(KNOWN_OXIDE_SPECIES))
    + r")(?![A-Za-z0-9])", re.I,
)


@dataclass(frozen=True, slots=True)
class _Fragment:
    text: str
    bbox: PDFBox


@dataclass(frozen=True, slots=True)
class GeometryCell:
    """One original number with optional local, source-derived column witnesses."""

    physical_page: int
    text: str
    bbox: PDFBox
    raw_text: str
    role: str | None = None
    unit: str | None = None
    header: str | None = None
    header_bbox: PDFBox | None = None
    unit_text: str | None = None
    unit_bbox: PDFBox | None = None
    chemical_basis: str | None = None

    @property
    def locator(self) -> str:
        return _locator(self.physical_page, self.bbox)


@dataclass(frozen=True, slots=True)
class GeometryRow:
    """Source-only detection. A printed aggregate can have unspecified class."""

    physical_page: int
    bbox: PDFBox
    text: str
    cells: tuple[GeometryCell, ...]
    category: str | None = None
    row_label: str | None = None
    caption: str | None = None
    caption_bbox: PDFBox | None = None
    table_kind: str | None = None

    @property
    def locator(self) -> str:
        return _locator(self.physical_page, self.bbox)


def _locator(page, rect):
    return f"PDF page {page}, top-left points [{', '.join(map(str, rect))}]"


def _box(values):
    return tuple(round(float(part), 3) for part in values)


def _extent(fragments):
    boxes = [part.bbox for part in fragments]
    return min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)


def _join(parts):
    return _Fragment(" ".join(x.text for x in parts), _extent(parts))


def _mid(box, low, high):
    return (box[low] + box[high]) / 2


def _line_groups(words):
    """Merge nearly level native words independent of the PDF content order."""
    ordered = sorted(words, key=lambda word: (_mid(word.bbox, 1, 3), word.bbox[0], word.text))
    groups = []
    for word in ordered:
        center = _mid(word.bbox, 1, 3)
        # Center distance with a fixed maximum avoids chaining adjacent rows
        # when the first/last letters use small superscripts or varied fonts.
        if not groups or abs(center - _mid(groups[-1][0].bbox, 1, 3)) > 2.1:
            groups.append([])
        groups[-1].append(word)
    return tuple(tuple(sorted(line, key=lambda word: (word.bbox[0], word.bbox[1], word.text))) for line in groups)


def _pieces(line):
    groups = []
    for word in line:
        if groups:
            previous = groups[-1][-1]
            gap = word.bbox[0] - previous.bbox[2]
            if gap <= 6.0 and not (_number(previous) and _number(word)):
                groups[-1].append(word)
                continue
        groups.append([word])
    return tuple(_join(group) for group in groups)


def _mupdf_fragments(page):
    import pymupdf

    text = page.get_textpage(flags=pymupdf.TEXTFLAGS_WORDS)
    upright = {(block["number"], index) for block in text.extractDICT()["blocks"]
               for index, line in enumerate(block.get("lines", ()))
               if line["dir"][0] > .99 and abs(line["dir"][1]) < .01}
    for entry in text.extractWORDS():
        box = _box(entry[:4])
        if ((entry[5], entry[6]) in upright and entry[4].strip() and all(isfinite(part) for part in box)
                and box[0] < box[2] and box[1] < box[3]):
            yield _Fragment(entry[4], box)


def _plumber_fragments(page):
    for word in page.extract_words():
        box = _box((word["x0"], word["top"], word["x1"], word["bottom"]))
        if (word["upright"] and word["text"].strip() and all(isfinite(part) for part in box)
                and box[0] < box[2] and box[1] < box[3]):
            yield _Fragment(word["text"], box)


def _plumber_rows(page, lines):
    """Use pdfplumber's grid, assigning original glyphs by cell and row."""
    lattice = _plumber_grid_rows(page.find_tables(), lines)
    if any(len(_numbers(row)) >= 2 for row in lattice):
        return lattice
    settings = {"vertical_strategy": "text", "horizontal_strategy": "text"}
    return _plumber_grid_rows(page.find_tables(settings), lines)


def _plumber_grid_rows(tables, lines):
    groups = []
    for grid in tables:
        if len(grid.rows) < 2 or len(grid.columns) < 3:
            continue
        for row in grid.rows:
            row_words = [word for line in lines for word in line if _word_in(word, row.bbox)]
            for aligned in _line_groups(row_words):
                parts = []
                for cell in row.cells:
                    local = tuple(word for word in aligned if cell and _word_in(word, cell))
                    if local:
                        parts.append(_join(local))
                if parts:
                    groups.append(tuple(parts))
    unique = {tuple((piece.bbox, piece.text) for piece in row): row for row in groups}
    return tuple(sorted(unique.values(), key=lambda row: (row[0].bbox[1], row[0].bbox[0])))


def _word_in(word, rect):
    center_x = _mid(word.bbox, 0, 2)
    center_y = _mid(word.bbox, 1, 3)
    return rect[0] <= center_x < rect[2] and rect[1] <= center_y < rect[3]


def _number(piece):
    return _CANDIDATE_VALUE.fullmatch(_plain(piece.text))


def _numbers(parts):
    return tuple((part, match) for part in parts if (match := _number(part)))


def _category(label):
    if not label:
        return None
    names = {_CATEGORY[term.casefold()] for term in _CLASS.findall(label)}
    if re.search(r"\bm\s*\+\s*(?:ind|inf)", label, re.I):
        names.add("MEASURED")
    ordered = tuple(name for name in ("MEASURED", "INDICATED", "INFERRED") if name in names)
    if not ordered:
        if _SUBTOTAL.search(label):
            return "SUBTOTAL_UNSPECIFIED"
        return "TOTAL_UNSPECIFIED" if _TOTAL.search(label) else None
    return "_".join(ordered) + ("_ONLY" if len(ordered) == 1 else "_TOTAL")


def _table_kind(text):
    if _RESERVE.search(text) or _TARGET.search(text):
        return "NON_RESOURCE"
    return "MINERAL_RESOURCE" if _RESOURCE.search(text) else None


def _title(line):
    text = _plain(line.text)
    return bool(_NUMBERED_TITLE.match(text) or _NAMED_RESOURCE.fullmatch(text))


def _caption(lines, row_box):
    previous = [line for line in lines if line.bbox[3] <= row_box[1] and row_box[1] - line.bbox[3] <= 310 and _title(line)]
    if previous:
        return previous[-1]
    # Some printed captions immediately follow the bottom border. In that
    # case use the nearest heading below only within the local table region.
    return next((line for line in lines if 0 <= line.bbox[1] - row_box[3] <= 74 and _title(line)), None)


def _windows(line_parts, row_box, caption):
    start = min(row_box[1], caption.bbox[3]) if caption else row_box[1]
    if start >= row_box[1]:
        start = row_box[1] - 145
    headers = []
    for parts in reversed(line_parts):
        box = _extent(parts)
        center = _mid(box, 1, 3)
        if not start - 1 <= center < _mid(row_box, 1, 3) - 2:
            continue
        if len(_numbers(parts)) >= 2:
            if headers:
                break
            continue
        if _mid(row_box, 1, 3) - center > 145 or _title(_join(parts)):
            break
        # A second panel starts a fresh run of headings. A paragraph or a
        # disjoint one-cell note cannot shift columns across panel boundaries.
        if len(parts) >= 2 or any(_UNIT_ONLY.fullmatch(part.text) for part in parts):
            headers.append(parts)
        elif headers:
            break
    return tuple(reversed(headers))


def _rail_distance(piece, value):
    # Use overlap and center, bounded by the width of the value. This allows
    # left/centre/right-aligned text but withholds distant header assignments.
    box, other = piece.bbox, value.bbox
    overlap = min(box[2], other[2]) - max(box[0], other[0])
    center = abs(_mid(box, 0, 2) - _mid(other, 0, 2))
    return center if overlap > -3 or center <= 12 else None


def _tier_fragments(tiers, value, numeric):
    results = []
    for parts in tiers:
        if len(_numbers(parts)) >= 2:
            continue
        candidates = []
        for piece in parts:
            if _number(piece):
                continue
            distances = [(gap, number.bbox[0]) for number, _ in numeric if (gap := _rail_distance(piece, number)) is not None]
            distances.sort()
            if distances and distances[0][1] == value.bbox[0]:
                if len(distances) == 1 or distances[1][0] - distances[0][0] > 2.0:
                    candidates.append((distances[0][0], piece))
        candidates.sort(key=lambda item: item[0])
        if candidates and (len(candidates) == 1 or candidates[1][0] - candidates[0][0] > 2.0):
            results.append(candidates[0][1])
    return tuple(results)


def _unit_choice(pieces, match):
    inline = match["unit"] or match["percent"]
    found = [(piece, unit) for piece in pieces if (unit := _column_unit(piece.text))]
    if not found:
        return None
    piece, unit = found[-1]
    alternatives = {candidate for _, candidate in found}
    if len(alternatives) > 1 and not (alternatives == {"t", unit} and (unit != "t") and _UNIT_ONLY.fullmatch(piece.text)):
        return None
    if inline and _candidate_header_unit(inline) != unit:
        return None
    return piece, unit


def _column_unit(label):
    if rates := _GRAMS_PER_TONNE.findall(_plain(label)):
        distinct = {"".join(unit.lower().split()) for unit in rates}
        return next(iter(distinct)) if len(distinct) == 1 else None
    return _candidate_header_unit(label)


def _lexical_binding(pieces, match):
    if not (unit_match := _unit_choice(pieces, match)):
        return None
    anchor, unit = unit_match
    labels = tuple(part for part in pieces if not _UNIT_ONLY.fullmatch(part.text) or _SCALE.fullmatch(part.text))
    if not labels:
        return None
    headline = _join(labels)
    # pdfminer may split chemical subscripts into successive words. Resolve
    # the registered species for comparison while retaining the source text.
    semantic_text = _FORMULAS.sub(lambda part: "".join(part[0].split()), _plain(headline.text))
    role = None if any(keyword.search(semantic_text) for keyword, _ in _PHYSICAL_HOLDS) else _candidate_role(semantic_text, unit)
    return headline, anchor, unit, role


def _raw_cell(page, value, match):
    return GeometryCell(page, match["number"], value.bbox, value.text)


def _semantic_cell(page, value, match, numeric, tiers):
    fragments = _tier_fragments(tiers, value, numeric)
    if binding := _lexical_binding(fragments, match):
        header, unit_anchor, unit, recognized = binding
        role, chemistry = recognized or (None, None)
        return GeometryCell(page, match["number"], value.bbox, value.text, role, unit, header.text, header.bbox,
                            unit_anchor.text, unit_anchor.bbox, chemistry)
    return _raw_cell(page, value, match)


def _row_context(parts, numeric):
    first = numeric[0][0].bbox[0]
    label = " ".join(part.text for part in parts if part.bbox[2] <= first)
    return label or None, _category(label)


def _basis_blocked(text):
    return any(pattern.search(text) for pattern in (_NON_ORE_DENOMINATOR, _INCOMPATIBLE_PARENT, _NON_ORE_HEADER))


def _rows(page, groups, line_parts):
    lines = tuple(_join(parts) for parts in line_parts)
    for parts in groups:
        numbers = _numbers(parts)
        if len(numbers) < 2 or sum(len(part.text.split()) for part in parts) > 3 * len(numbers) + 12:
            continue
        rect = _extent(parts)
        label, category = _row_context(parts, numbers)
        caption = _caption(lines, rect)
        tiers = _windows(line_parts, rect, caption)
        header_text = " ".join(part.text for tier in tiers if len(_numbers(tier)) < 2 for part in tier)
        table_kind = _table_kind(caption.text) if caption else None
        allowed = (table_kind == "MINERAL_RESOURCE" and category is not None and _RARE_EARTH.search(header_text)
                   and not _basis_blocked(header_text) and not _BODY.search(label or ""))
        cells = tuple(_semantic_cell(page, value, match, numbers, tiers) if allowed else _raw_cell(page, value, match)
                      for value, match in numbers)
        yield GeometryRow(page, rect, " ".join(part.text for part in parts), cells, category, label,
                          caption.text if caption else None, caption.bbox if caption else None, table_kind)


@dataclass(frozen=True, slots=True)
class GeometryReader:
    method: str
    document: object
    page_count: int

    def page(self, number: int) -> tuple[GeometryRow, ...]:
        if type(number) is not int or not 1 <= number <= self.page_count:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "PHYSICAL_PAGE_MISSING")
        if self.method == "pymupdf":
            words = tuple(_mupdf_fragments(self.document[number - 1]))
            lines = tuple(_pieces(line) for line in _line_groups(words))
            return tuple(_rows(number, lines, lines))
        page = self.document.pages[number - 1]
        try:
            aligned = _line_groups(_plumber_fragments(page))
            groups = _plumber_rows(page, aligned)
            return tuple(_rows(number, groups, tuple(_pieces(line) for line in aligned)))
        finally:
            page.close()


@contextmanager
def open_table_geometry(body: bytes, method: str) -> Iterator[GeometryReader]:
    """Read original PDF pages with a locally installed geometry backend."""
    if method == "pymupdf":
        import pymupdf

        try:
            document = pymupdf.open(stream=body, filetype="pdf")
        except (RuntimeError, ValueError) as error:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "GEOMETRY_PDF_UNREADABLE") from error
        with document:
            if document.needs_pass:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "GEOMETRY_PDF_ENCRYPTED")
            yield GeometryReader(method, document, document.page_count)
    elif method == "pdfplumber":
        import pdfplumber
        from pdfminer.pdfdocument import PDFPasswordIncorrect
        from pdfminer.pdfparser import PDFSyntaxError

        try:
            document = pdfplumber.open(BytesIO(body))
        except (PDFSyntaxError, ValueError, PDFPasswordIncorrect) as error:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "GEOMETRY_PDF_UNREADABLE") from error
        with document:
            yield GeometryReader(method, document, len(document.pages))
    else:
        raise ValueError(f"Unknown PDF table geometry backend {method!r}")

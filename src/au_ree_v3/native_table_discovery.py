"""Find native resource table rows before any Project or science admission."""

from dataclasses import dataclass
import re

from .documents import DocumentError, PDFDocument
from .resource_table_binding import (
    CellBinding, Span, _CAPTION, _RESOURCE, _REVIEWED_RESOURCE_HEADING,
    _candidate_tokens, _caption_block, _classify_layout_row, _first_body, _header_start,
    _line_span, _overlap, _plain, _resource_type, _spans, bind_page_row,
)


DISCOVERY_RULE = "native-resource-table-discovery-v2"
_ROW_LABEL = re.compile(
    r"(?:total|(?:grand|global|combined) total|(?:total |global |combined |overall )?"
    r"(?:mineral )?resources?|(?:global|combined|overall)|total (?:mre|measured|indicated|inferred))"
    r"(?:\s*[*†‡]|\s*\([a-z0-9]\))?", re.I,
)
_SHORT_TABLE_TITLE = re.compile(
    r"(?:[\w’'/-]+\s+){0,8}(?:mineral resources?|(?:ore|mineral) reserves?|exploration targets?)"
    r"(?:\s+(?:estimates?|statement|summary|totals?|table))?", re.I,
)
_SECTION_PREFIX = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
_HEADING_CONTEXT = re.compile(
    r"\s*\((?:JORC(?: Code)?\s+20\d{2}|20\d{2}|[\w]+\s+20\d{2})\)\s*$", re.I,
)
_NAMED_TOTAL = re.compile(
    r"[\w-]+(?: [\w-]+){0,5}\s+mineral resource(?:\s+(?:inventory|estimate|total))?"
    r"\s*\((?:M|Measured)(?:\s*\+\s*(?:I|Ind|Inf|Indicated|Inferred)){1,2}\)", re.I,
)
_RESOURCE_WORD = re.compile(r"(?<![A-Za-z])mineral\s+resources?\b", re.I)
_RESOURCE_ALIAS = re.compile(r'\bMineral Resource\s*\([\"“]Resource[\"”]\)', re.I)
_CUTOFF = re.compile(r"\bcut[- ]?off\b|\bCOG\b", re.I)
_RESERVE_OR_TARGET = re.compile(r"\b(?:ore reserves?|exploration targets?)\b", re.I)
_CONTINUATION = re.compile(r"\b(?:cont\.?|continued|continuation)\b", re.I)
_HEADER_MASS = re.compile(r"\b(?:tonnage|tonnes?|Mt|kt|Bt|t)\b", re.I)
_HEADER_ANALYTE = re.compile(r"\b(?:TREO|rare earth oxides?|REO)\b", re.I)
_HEADER_CLASS = re.compile(r"\b(?:classification|category|jorc|(?:resource )?class|deposit|prospect)\b", re.I)
_CLASS_LABEL = re.compile(r"(?:measured|indicated|inferred|ind\s*[+]\s*inf)", re.I)
_PROSE = re.compile(r"[.!?;]\s|\b(?:will|could|expects|has been|we have|released|announced|published)\b", re.I)
_MAX_TABLE_LINES = 34
_MAX_CAPTION_GAP = 5


@dataclass(frozen=True, slots=True)
class NativeResourceRow:
    physical_page: int
    caption_line: int
    row_line: int
    strategy: str
    cells: tuple[CellBinding, ...]
    context_anchors: tuple[Span, ...] = ()

    @property
    def row_locator(self) -> str:
        return f"PDF page {self.physical_page}, line {self.row_line}"


def _short_caption(raw: str) -> bool:
    title = _HEADING_CONTEXT.sub("", _SECTION_PREFIX.sub("", _plain(raw).rstrip(":")))
    return bool(_REVIEWED_RESOURCE_HEADING.fullmatch(title)
                or _SHORT_TABLE_TITLE.fullmatch(title) and not _PROSE.search(title))


def _row_kind(raw: str, page: int, line: int) -> str | None:
    spans = _spans(raw, page, line)
    if len(spans) < 3:
        return None
    tokens = _candidate_tokens(raw, page, line)
    if len(tokens) < 2:
        return None
    label = _plain(spans[0].text)
    if _ROW_LABEL.fullmatch(label) or label.casefold() == "all" and _CLASS_LABEL.fullmatch(_plain(spans[1].text)):
        return "TOTAL"
    if _NAMED_TOTAL.fullmatch(label):
        return "NAMED_SCOPE"
    if _CLASS_LABEL.fullmatch(label):
        return "CLASSIFICATION"
    if tokens[0] == spans[0] and len(spans) >= 4:
        return "CUTOFF"
    return None


def _total_row(raw: str, page: int, line: int) -> bool:
    return _row_kind(raw, page, line) in {"TOTAL", "NAMED_SCOPE"}


def _registered_header(lines: tuple[str, ...], page: int, start: int, end: int) -> bool:
    if start >= end:
        return False
    header = " ".join(lines[line - 1] for line in range(start, end))
    return bool(_HEADER_MASS.search(header) and _HEADER_ANALYTE.search(header)
                and any(len(_spans(lines[i - 1], page, i)) >= 2 for i in range(start, end)))


def _numbered_row(document, page, row_line, caption_line, strategy):
    try:
        cells = bind_page_row(document, page=page, row_line=row_line, caption_line=caption_line)
    except DocumentError:
        return None
    # Continuations can use the previous grid. A local same-page structure
    # without bound roles is reported separately by the inventory review.
    if sum(cell.variable is not None for cell in cells) >= 2:
        return NativeResourceRow(page, caption_line, row_line, strategy, cells)
    return None


def _accepted_caption(local, start, numbered, caption):
    kind = _resource_type(caption)
    if kind is not None:
        return kind
    raw = " ".join(part.text for part in caption)
    if _RESOURCE_WORD.search(raw) and not _RESERVE_OR_TARGET.search(raw):
        return "MINERAL_RESOURCE"
    if numbered and _CUTOFF.search(raw) and re.search(r"\bresource\b", raw, re.I):
        preceding = " ".join(_plain(line) for line in local[max(0, start - 13):start - 1])
        if _RESOURCE_ALIAS.search(preceding) and not _RESERVE_OR_TARGET.search(raw):
            return "MINERAL_RESOURCE_CUTOFF_ALIAS"
    return None


def _preceding_grid(local, page, row, first, data):
    """Start a separately captioned table panel only after exact prior body rows."""
    prior = next((line for line in range(row - 1, data - 1, -1)
                  if len(_candidate_tokens(local[line - 1], page, line)) >= 2), None)
    if prior is None:
        return first, data
    new_header = _header_start(local, page, prior + 1, row)
    return (new_header, row) if _registered_header(local, page, new_header, row) else (first, data)


def _cutoff_anchors(local, page, first, data, row):
    cutoff = _candidate_tokens(local[row - 1], page, row)[0]
    headings = tuple(span for line in range(first, data) for span in _spans(local[line - 1], page, line)
                     if _overlap(span, cutoff))
    has_threshold = any(_CUTOFF.search(span.text) for span in headings)
    has_basis = any(_HEADER_ANALYTE.search(span.text) and re.search(r"%|\bppm\b", span.text, re.I) for span in headings)
    return (*headings, cutoff) if has_threshold and has_basis else ()


def _preceding_threshold(local, page, start, header):
    for line in range(header - 1, max(start - 1, header - 7), -1):
        label = local[line - 1]
        if _candidate_tokens(label, page, line) or _CAPTION.match(label):
            break
        if _CUTOFF.search(label) and re.search(r"\d\s*(?:%|ppm)\s+\w", label, re.I):
            return (_line_span(local, line, page),)
    return ()


def _row_candidates(local, page, first_data, end):
    kinds = [(line, kind) for line in range(first_data, end) if (kind := _row_kind(local[line - 1], page, line))]
    # A single classified inventory may be the only printed row of its table.
    # Keep multi-row estimates anchored to their explicit total or cutoff series.
    classified = sum(kind == "CLASSIFICATION" for _, kind in kinds)
    primary = any(kind in {"TOTAL", "NAMED_SCOPE"} for _, kind in kinds)
    return tuple((line, kind) for line, kind in kinds if kind != "CLASSIFICATION" or (not primary and classified == 1))


def _layout_row(local, document, page, start, first, data, caption, line, row_kind, numbered, kind, continuation):
    if continuation:
        return _numbered_row(document, page, line, start, "NUMBERED_TABLE_CONTINUED") if row_kind == "TOTAL" else None
    if row_kind == "CUTOFF":
        if not (context := _cutoff_anchors(local, page, first, data, line)):
            return None
        strategy = "PRINTED_RESOURCE_CUTOFF_GRID"
    elif row_kind == "NAMED_SCOPE":
        first, data = _preceding_grid(local, page, line, first, data)
        context, strategy = (), "NAMED_RESOURCE_SUBTABLE"
    elif numbered and kind == "MINERAL_RESOURCE" and _resource_type(caption) == kind:
        return _numbered_row(document, page, line, start, "NUMBERED_RESOURCE_TABLE")
    else:
        context, strategy = (), "UNNUMBERED_RESOURCE_HEADING" if not numbered else "NUMBERED_RESOURCE_TABLE"
    if not _registered_header(local, page, first, data):
        return None
    cells = _classify_layout_row(local, page, line, first, data, caption)
    count = sum(cell.variable is not None for cell in cells)
    named_incompatible_row = (row_kind == "NAMED_SCOPE" and count == 1
                              and any(cell.variable == "ore_mt" for cell in cells)
                              and any(cell.reason == "CHEMICALLY_MODIFIED_TREO_IS_NOT_STANDARD_TREO" for cell in cells))
    if count >= 2 or named_incompatible_row:
        return NativeResourceRow(page, start, line, strategy, cells, context)
    return None


def _leading(local, document, page, start, end, numbered):
    first_header = _header_start(local, page, start + 1, end)
    first_data = _first_body(local, page, first_header, end)
    caption = (_caption_block(local, page, start, first_header - 1) if numbered else
               (_line_span(local, start, page),))
    kind = _accepted_caption(local, start, numbered, caption)
    continuation = numbered and _CONTINUATION.search(" ".join(item.text for item in caption))
    if kind not in {"MINERAL_RESOURCE", "MINERAL_RESOURCE_CUTOFF_ALIAS"} and not (continuation and kind is None):
        return ()
    hits = []
    for line, row_kind in _row_candidates(local, page, first_data, end):
        item = _layout_row(local, document, page, start, first_header, first_data, caption, line,
                           row_kind, numbered, kind, continuation)
        if item:
            hits.append(item)
    return tuple(hits)


def _trailing(local, page, caption_line, start):
    # Some editions print the numbered caption below the table in layout text.
    if not _RESOURCE.search(local[caption_line - 1]):
        return ()
    caption: tuple[Span, ...] = (_line_span(local, caption_line, page),)
    if _resource_type(caption) != "MINERAL_RESOURCE":
        return ()
    earliest = max(start, caption_line - _MAX_TABLE_LINES)
    totals = [line for line in range(earliest, caption_line) if _total_row(local[line - 1], page, line)]
    if not totals or caption_line - totals[-1] > _MAX_CAPTION_GAP:
        return ()
    headings = [line for line in range(earliest, totals[-1])
                if _HEADER_CLASS.search(local[line - 1]) and len(_spans(local[line - 1], page, line)) >= 2]
    result = []
    for row_line in totals:
        header = next((line for line in reversed(headings) if line < row_line), None)
        if header is None or any(_PROSE.search(local[line - 1]) for line in range(header, row_line)):
            continue
        first_data = _first_body(local, page, header, row_line + 1)
        if not _registered_header(local, page, header, first_data):
            continue
        cells = _classify_layout_row(local, page, row_line, header, first_data, caption)
        if sum(cell.variable is not None for cell in cells) >= 2:
            context = _preceding_threshold(local, page, earliest, header)
            result.append(NativeResourceRow(page, caption_line, row_line, "RESOURCE_CAPTION_BELOW_GRID", cells, context))
    return tuple(result)


def discover_page_rows(document: PDFDocument, *, page: int) -> tuple[NativeResourceRow, ...]:
    """Discover unambiguous candidate rows, retaining their original page and line."""
    local = tuple(document.locate(page).text.splitlines())
    captions = [(line, bool(_CAPTION.match(raw))) for line, raw in enumerate(local, start=1)
                if _CAPTION.match(raw) or _short_caption(raw)]
    hits: dict[int, list[NativeResourceRow]] = {}
    for index, (caption_line, numbered) in enumerate(captions):
        previous = captions[index - 1][0] + 1 if index else 1
        following = captions[index + 1][0] if index + 1 < len(captions) else len(local) + 1
        end = min(following, caption_line + _MAX_TABLE_LINES + 1)
        for row in _leading(local, document, page, caption_line, end, numbered):
            hits.setdefault(row.row_line, []).append(row)
        if numbered:
            for row in _trailing(local, page, caption_line, previous):
                hits.setdefault(row.row_line, []).append(row)
    return tuple(items[0] for _, items in sorted(hits.items()) if len(items) == 1)

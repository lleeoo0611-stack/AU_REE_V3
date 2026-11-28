"""Find native resource table rows before any Project or science admission."""

from dataclasses import dataclass
import re

from .documents import DocumentError, PDFDocument
from .resource_table_binding import (
    CellBinding, Span, _CAPTION, _RESOURCE, _REVIEWED_RESOURCE_HEADING,
    _candidate_tokens, _caption_block, _classify_layout_row, _first_body, _header_start,
    _line_span, _plain, _resource_type, _spans, bind_page_row,
)


DISCOVERY_RULE = "native-resource-table-discovery-v1"
_ROW_LABEL = re.compile(
    r"(?:total|(?:grand|global|combined) total|(?:total |global |combined |overall )?"
    r"(?:mineral )?resources?|(?:global|combined|overall)|total (?:mre|measured|indicated|inferred))"
    r"(?:\s*[*†‡]|\s*\([a-z0-9]\))?", re.I,
)
_SHORT_TABLE_TITLE = re.compile(
    r"(?:[\w’'/-]+\s+){0,8}(?:mineral resources?|(?:ore|mineral) reserves?|exploration targets?)"
    r"(?:\s+(?:estimates?|statement|summary|totals?|table))?", re.I,
)
_CONTINUATION = re.compile(r"\b(?:cont\.?|continued|continuation)\b", re.I)
_HEADER_MASS = re.compile(r"\b(?:tonnage|tonnes?|Mt|kt|Bt)\b", re.I)
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

    @property
    def row_locator(self) -> str:
        return f"PDF page {self.physical_page}, line {self.row_line}"


def _short_caption(raw: str) -> bool:
    title = _plain(raw).rstrip(":")
    return bool(_REVIEWED_RESOURCE_HEADING.fullmatch(title)
                or _SHORT_TABLE_TITLE.fullmatch(title) and not _PROSE.search(title))


def _total_row(raw: str, page: int, line: int) -> bool:
    spans = _spans(raw, page, line)
    if len(spans) < 3 or len(_candidate_tokens(raw, page, line)) < 2:
        return False
    label = _plain(spans[0].text)
    return bool(_ROW_LABEL.fullmatch(label) or label.casefold() == "all" and _CLASS_LABEL.fullmatch(_plain(spans[1].text)))


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


def _leading(local, document, page, start, end, numbered):
    first_header = _header_start(local, page, start + 1, end)
    first_data = _first_body(local, page, first_header, end)
    caption = (_caption_block(local, page, start, first_header - 1) if numbered else
               (_line_span(local, start, page),))
    kind = _resource_type(caption)
    continuation = numbered and _CONTINUATION.search(" ".join(item.text for item in caption))
    if kind != "MINERAL_RESOURCE" and not (continuation and kind is None):
        return ()
    hits = []
    for line in range(first_data, end):
        if not _total_row(local[line - 1], page, line):
            continue
        if numbered:
            item = _numbered_row(document, page, line, start,
                                 "NUMBERED_TABLE_CONTINUED" if continuation else "NUMBERED_RESOURCE_TABLE")
        elif _registered_header(local, page, first_header, first_data):
            cells = _classify_layout_row(local, page, line, first_header, first_data, caption)
            item = NativeResourceRow(page, start, line, "UNNUMBERED_RESOURCE_HEADING", cells)
        else:
            item = None
        if item and sum(cell.variable is not None for cell in item.cells) >= 2:
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
            result.append(NativeResourceRow(page, caption_line, row_line, "RESOURCE_CAPTION_BELOW_GRID", cells))
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

"""Bind native PDF numeric cells to physically aligned resource table headers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata

from .documents import DocumentError, PDFDocument
from .normalization import KNOWN_OXIDE_SPECIES, reported_interval
from .observations import NUMBER

RULE = "native-resource-table-layout-v1"
_CAPTION = re.compile(r"^\s*(?:table|figure)\s+[A-Z]?\d+(?:[.-]\d+)*\b", re.I)
_RESOURCE = re.compile(r"\bmineral\s+resources?\b", re.I)
_REVIEWED_RESOURCE_HEADING = re.compile(r"\s*mineral resource estimate\s*", re.I)
_RESERVE = re.compile(r"\b(?:ore|mineral)\s+reserves?\b", re.I)
_TARGET = re.compile(r"\bexploration\s+targets?\b", re.I)
_CELL = re.compile(r"\S(?:.*?\S)?(?=\s{2,}|$)")
_VALUE = re.compile(
    rf"(?P<number>{NUMBER})(?:[ \t]+(?P<unit>Bt|Mt|kt|t|ppm|kg/t)|[ \t]*(?P<percent>%))?",
    re.I,
)
_UNIT = re.compile(
    r"(?<![\w.])(?:billion\s+tonnes?|million\s+tonnes?|(?-i:M)\s+tonnes?|thousand\s+tonnes?|kg\s*/\s*t|"
    r"ppm|Bt|Mt|kt|tonnes?|t)(?!\w)|%", re.I,
)
_OXIDES = {part.upper(): part for part in KNOWN_OXIDE_SPECIES}
_OXIDE = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, sorted(_OXIDES))) + r")(?![A-Za-z0-9])", re.I)
_TREO = re.compile(r"\b(?:TREO|total\s+rare\s+earth\s+oxides?)\b", re.I)
_TREO_MODIFIER = re.compile(r"\bTREO\s*(?:[+−-]|plus\b|minus\b|including\b|excluding\b)\s*(?:Y(?:2O3)?|Sc(?:2O3)?|Ce(?:O2)?)\b", re.I)
_NON_ORE_HEADER = re.compile(r"\b(?:HM|heavy[- ]mineral|concentrat(?:e|es|ion)|product|recoverable)\b", re.I)
_ORE = re.compile(r"\b(?:ore(?:\s+mass)?|tonnage|tonnes?)\b", re.I)
_CONTAINED = re.compile(r"\b(?:contained|content|mass|tonnage|tonnes?)\b", re.I)
_MASS = {"t", "kt", "Mt", "Bt"}
_UNITS = {
    "t": "t", "tonne": "t", "tonnes": "t", "kt": "kt", "mt": "Mt", "bt": "Bt",
    "thousand tonne": "kt", "thousand tonnes": "kt", "million tonne": "Mt", "million tonnes": "Mt",
    "billion tonne": "Bt", "billion tonnes": "Bt", "m tonne": "Mt", "m tonnes": "Mt", "%": "%", "ppm": "ppm", "kg/t": "kg/t",
}
_OXIDE_GRADE_UNITS = {"%", "ppm", "kg/t"}


@dataclass(frozen=True, slots=True)
class Span:
    physical_page: int
    line: int
    start_column: int
    end_column: int
    text: str

    @property
    def locator(self) -> str:
        return f"PDF page {self.physical_page}, line {self.line}, columns {self.start_column}-{self.end_column}"

    def to_dict(self) -> dict:
        return {**asdict(self), "locator": self.locator}


@dataclass(frozen=True, slots=True)
class Column:
    variable: str
    unit: str
    header: Span
    unit_label: Span | None
    chemical_basis: str | None
    parent_header: Span | None = None


@dataclass(frozen=True, slots=True)
class CellBinding:
    variable: str | None
    unit: str | None
    chemical_basis: str | None
    value: Span
    caption: tuple[Span, ...]
    header: Span | None
    unit_label: Span | None
    reason: str
    raw_rounding_interval: tuple[str, str] | None = None
    parent_header: Span | None = None

    def to_dict(self) -> dict:
        return {
            "rule": RULE, "variable": self.variable, "native_unit": self.unit, "chemical_basis": self.chemical_basis,
            "value_anchor": self.value.to_dict(), "caption_anchors": [anchor.to_dict() for anchor in self.caption],
            "header_anchor": self.header.to_dict() if self.header else None,
            "unit_anchor": self.unit_label.to_dict() if self.unit_label else None,
            "reason": self.reason, "raw_rounding_interval": self.raw_rounding_interval,
            **({"parent_header_anchor": self.parent_header.to_dict()} if self.parent_header else {}),
        }


def _plain(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _lines(document: PDFDocument, page: int) -> tuple[str, ...]:
    return tuple(document.locate(page).text.splitlines())


def _spans(line: str, page: int, index: int) -> tuple[Span, ...]:
    return tuple(Span(page, index, match.start() + 1, match.end(), match[0]) for match in _CELL.finditer(line))


def _line_span(lines: tuple[str, ...], index: int, page: int) -> Span:
    if type(index) is not int or not 1 <= index <= len(lines) or not lines[index - 1].strip():
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ORIGINAL_LINE_UNAVAILABLE")
    raw = lines[index - 1]
    return Span(page, index, len(raw) - len(raw.lstrip()) + 1, len(raw.rstrip()), raw.strip())


def _overlap(first: Span, second: Span) -> bool:
    return max(first.start_column, second.start_column) <= min(first.end_column, second.end_column)


def _contains(outer: Span, inner: Span) -> bool:
    return outer.start_column <= inner.start_column <= inner.end_column <= outer.end_column


def _units(label: str) -> set[str]:
    normalized = _plain(label)
    return {_UNITS[re.sub(r"\s*/\s*", "/", unit[0].casefold())] for unit in _UNIT.finditer(normalized)}


def _unit_label(label: str) -> str | None:
    text = _plain(label)
    parenthetical = re.search(r"\(([^()]*)\)\s*$", text)
    if parenthetical and len(units := _units(parenthetical[1])) == 1:
        return next(iter(units))
    normalized = text.strip("()[] ").casefold()
    if normalized in {"million", "thousand", "billion"}:
        return {"million": "Mt", "thousand": "kt", "billion": "Bt"}[normalized]
    return next(iter(units)) if len(units := _units(label)) == 1 else None


def _role(label: str, unit: str) -> tuple[str, str | None] | None:
    label = _plain(label)
    treo = bool(_TREO.search(label))
    species = {_OXIDES[item[0].upper()] for item in _OXIDE.finditer(label)}
    if _NON_ORE_HEADER.search(label) or _TREO_MODIFIER.search(label) or (treo and species):
        return None
    if unit in _MASS:
        if treo and (_CONTAINED.search(label) or not re.search(r"\b(?:grade|average)\b", label, re.I)):
            return "contained_treo_kt", "TREO"
        if not treo and not species and _ORE.search(label):
            return "ore_mt", None
    elif unit in _OXIDE_GRADE_UNITS:
        if treo:
            return "grade", "TREO"
        if len(species) == 1:
            oxide, = species
            return f"{oxide.casefold()}_ppm", oxide
    return None


def _numbers(line: str, page: int, index: int) -> tuple[Span, ...]:
    return tuple(span for span in _spans(line, page, index) if _VALUE.fullmatch(span.text))


def _parsed_value(span: Span) -> tuple[Span, Span | None]:
    match = _VALUE.fullmatch(span.text)
    values = []
    for name in ("number", "unit" if match["unit"] else "percent"):
        values.append(Span(span.physical_page, span.line, span.start_column + match.start(name),
                           span.start_column + match.end(name) - 1, match[name]) if match[name] else None)
    return tuple(values)


def _caption_block(lines, page, start, end, *, reviewed=False):
    if not _CAPTION.match(lines[start - 1]) and not (reviewed and _REVIEWED_RESOURCE_HEADING.fullmatch(lines[start - 1])):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "NATIVE_TABLE_CAPTION_UNBOUND")
    return tuple(_line_span(lines, line, page) for line in range(start, end + 1) if lines[line - 1].strip())


def _resource_type(caption: tuple[Span, ...]) -> str | None:
    text = " ".join(span.text for span in caption)
    if _RESERVE.search(text) or _TARGET.search(text):
        return "ORE_RESERVE" if _RESERVE.search(text) else "EXPLORATION_TARGET"
    return "MINERAL_RESOURCE" if _RESOURCE.search(text) else None


def _line_match(column: Column, value: Span, inline: Span | None) -> bool:
    return _overlap(column.header, value) and (
        _overlap(column.unit_label, value) if column.unit_label else inline is not None
    )


def _classified(columns, values, caption):
    parsed = tuple(_parsed_value(value) for value in values)
    for value, inline in parsed:
        matches = [column for column in columns if _line_match(column, value, inline)]
        if len(matches) == 1:
            column = matches[0]
            permitted_unit = (inline or column.unit_label)
            if (not inline or _unit_label(inline.text) == column.unit) and sum(
                _line_match(column, other, local_unit) for other, local_unit in parsed
            ) == 1:
                _, lower, upper = reported_interval(value.text)
                yield CellBinding(column.variable, column.unit, column.chemical_basis, value, caption,
                                  column.header, permitted_unit, "ORIGINAL_HEADER_UNIT_AND_ROW_BOUND",
                                  (format(lower, "f"), format(upper, "f")), column.parent_header)
                continue
        yield CellBinding(None, None, None, value, caption, None, inline,
                          "AMBIGUOUS_OR_CONFLICTING_COLUMN_ALIGNMENT" if matches else "HEADER_OR_UNIT_UNBOUND")


def _automatic_columns(lines, page, header_indices):
    entries = tuple(span for index in header_indices for span in _spans(lines[index - 1], page, index))
    columns = []
    for entry in entries:
        unit = _unit_label(entry.text)
        if not unit:
            continue
        aligned = [label for label in entries if label.line != entry.line and _overlap(label, entry)]
        # A bare printed mass unit needs a uniquely aligned ore/analyte header,
        # except an inline tonnes unit already provides the resource mass label.
        if len(aligned) > 1:
            continue
        header = aligned[0] if aligned else entry
        label = " ".join((header.text, entry.text)) if header != entry else entry.text
        if not _role(label, unit):
            continue
        variable, chemistry = _role(label, unit)
        columns.append(Column(variable, unit, header, entry, chemistry))
    return tuple(dict.fromkeys(columns))


def bind_page_row(document: PDFDocument, *, page: int, row_line: int, caption_line: int) -> tuple[CellBinding, ...]:
    """Type only physically resolved same-page original cells. No scientific admission."""
    lines = _lines(document, page)
    _line_span(lines, row_line, page)
    if not 1 <= caption_line < row_line or not _CAPTION.match(lines[caption_line - 1]):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "NATIVE_TABLE_CAPTION_UNBOUND")
    if any(_CAPTION.match(item) for item in lines[caption_line:row_line - 1]):
        raise DocumentError("SCIENTIFIC_CONFLICT", "ROW_OUTSIDE_SOURCE_TABLE")
    first_header = next((line for line in range(caption_line + 1, row_line)
                         if len(_spans(lines[line - 1], page, line)) >= 2), row_line)
    caption = _caption_block(lines, page, caption_line, first_header - 1)
    if _resource_type(caption) != "MINERAL_RESOURCE":
        raise DocumentError("SCIENTIFIC_CONFLICT", "MINERAL_RESOURCE_SCOPE_REQUIRED")
    first_data = next((index for index in range(first_header, row_line)
                       if len(_numbers(lines[index - 1], page, index)) >= 2), row_line)
    headers = range(first_header, first_data)
    values = _numbers(lines[row_line - 1], page, row_line)
    return tuple(_classified(_automatic_columns(lines, page, headers), values, caption))


def _selected_header(document, page, expected, *, after, through):
    lines = _lines(document, page)
    if not isinstance(expected, list) or not expected or any(not isinstance(item, str) or not _plain(item) for item in expected):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_HEADER_LINES_UNBOUND")
    result = []
    for quote in expected:
        matches = [index for index in range(after + 1, through + 1) if _plain(lines[index - 1]).casefold() == _plain(quote).casefold()]
        if len(matches) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_HEADER_LINE_NOT_UNIQUE")
        result.append(_line_span(lines, matches[0], page))
    if [row.line for row in result] != sorted({row.line for row in result}):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_HEADER_LINE_ORDER_MISMATCH")
    return tuple(result)


def _heading_terms(document, anchors, position, label):
    if type(position) is not int or not 1 <= position <= len(anchors) or not isinstance(label, str) or not label.strip():
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COLUMN_HEADER_TERM_UNBOUND")
    line = anchors[position - 1]
    text = document.locate(line.physical_page, line=line.line).text
    pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, label.split())) + r"(?!\w)"
    terms = [Span(line.physical_page, line.line, match.start() + 1, match.end(), match[0])
             for match in re.finditer(pattern, text, flags=re.I)]
    return [(term, segment) for term in terms for segment in _spans(text, line.physical_page, line.line)
            if _contains(segment, term)]


def _in_group(document, headers, entry, terms):
    if not entry.get("group_header"):
        return terms, None
    line = entry["group_line"]
    named = _heading_terms(document, headers, line, entry["group_header"])
    if len(named) != 1 or line >= entry["header_line"]:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_PARENT_HEADER_UNBOUND")
    _, requested = named[0]
    whole = headers[line - 1]
    raw = document.locate(whole.physical_page, line=whole.line).text
    groups = _spans(raw, whole.physical_page, whole.line)
    filtered = []
    for term, segment in terms:
        center = segment.start_column + segment.end_column
        distances = {group: abs(center - group.start_column - group.end_column) for group in groups}
        nearest = [group for group, distance in distances.items() if distance == min(distances.values())]
        if nearest == [requested]:
            filtered.append((term, segment))
    return filtered, requested


def _reviewed_columns(document, headers, entries):
    columns = []
    for entry in entries:
        header_terms = _heading_terms(document, headers, entry["header_line"], entry["header"])
        header_terms, parent = _in_group(document, headers, entry, header_terms)
        if entry.get("unit_inline") is True:
            units = [(term, None, segment, None) for term, segment in header_terms]
        else:
            unit_terms = _heading_terms(document, headers, entry["unit_line"], entry["unit_label"])
            units = [
                (header, unit_label, header_segment, unit_segment)
                for header, header_segment in header_terms for unit_label, unit_segment in unit_terms
                if _overlap(header_segment, unit_segment) and _unit_label(unit_label.text) == entry["unit"]
            ]
        if len(units) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COLUMN_OR_UNIT_NOT_UNIQUE")
        header, unit_label, header_segment, unit_segment = units[0]
        label = " ".join((header_segment.text, unit_segment.text)) if unit_segment and header_segment != unit_segment else header_segment.text
        role = _role(label, entry["unit"])
        if role is None or role[0] != entry["variable"]:
            raise DocumentError("SCIENTIFIC_CONFLICT", "REVIEWED_COLUMN_QUANTITY_OR_CHEMISTRY_MISMATCH")
        columns.append((Column(entry["variable"], entry["unit"], header_segment, unit_segment, role[1], parent), header, unit_label))
    if not columns or len({column.variable for column, _, _ in columns}) != len(columns):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COLUMN_VARIABLE_NOT_UNIQUE")
    return tuple(columns)


def _reviewed_caption(document, page, quote):
    lines = _lines(document, page)
    if not isinstance(quote, str) or not _plain(quote):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_TABLE_CAPTION_UNBOUND")
    matches = []
    for index, text in enumerate(lines, start=1):
        if not (_CAPTION.match(text) or _REVIEWED_RESOURCE_HEADING.fullmatch(text)):
            continue
        for end in range(index, min(index + 5, len(lines) + 1)):
            excerpt = _caption_block(lines, page, index, end, reviewed=True)
            if _plain(" ".join(item.text for item in excerpt)).casefold() == _plain(quote).casefold():
                matches.append(excerpt)
                break
    if len(matches) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_TABLE_CAPTION_NOT_UNIQUE")
    return matches[0]


def _row_label(line, page, index):
    first_number = next(iter(_numbers(line, page, index)), None)
    return _plain(line[:first_number.start_column - 1]) if first_number else ""


def reviewed_table_rows(document: PDFDocument, binding: dict):
    """Read a reviewed exact table arrangement, retaining physical offsets and membership."""
    table, page = binding["table"], binding["page"]
    header_page = table.get("header_page", page)
    caption = _reviewed_caption(document, header_page, table["caption"])
    header_lines = _lines(document, header_page)
    after = caption[-1].line
    through = next((index for index in range(after, len(header_lines)) if _CAPTION.match(header_lines[index])),
                   len(header_lines))
    header_spans = _selected_header(document, header_page, table["header_lines"],
                                    after=after, through=min(through, after + 30))
    header_line = header_spans[-1].line
    if (header_line - caption[-1].line > 30 or header_spans[0].line <= caption[-1].line
            or any(_CAPTION.match(line) for line in header_lines[caption[-1].line:header_line])):
        raise DocumentError("SCIENTIFIC_CONFLICT", "HEADERS_OUTSIDE_REVIEWED_CAPTION")
    columns = _reviewed_columns(document, header_spans, table["columns"])
    row_lines = _lines(document, page)
    start = header_line
    if page != header_page:
        if page != header_page + 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "HEADER_CONTINUATION_NOT_ADJACENT")
        marker = _reviewed_caption(document, page, table.get("continuation_quote"))
        table_name = _CAPTION.match(caption[0].text)
        continuation_name = _CAPTION.match(marker[0].text)
        if table_name is None or continuation_name is None or _plain(table_name[0]).casefold() != _plain(continuation_name[0]).casefold() or not re.search(
            r"\bcont(?:inued|inuation)?\b", " ".join(line.text for line in marker), re.I,
        ):
            raise DocumentError("SCIENTIFIC_CONFLICT", "CROSS_PAGE_TABLE_CONTINUATION_MISMATCH")
        start = marker[-1].line
    end = next((index for index in range(start, len(row_lines)) if _CAPTION.match(row_lines[index])), len(row_lines))
    boundary = table.get("end_quote")
    if boundary is not None:
        stop_lines = [line for line in range(start + 1, end + 1)
                      if _plain(row_lines[line - 1]).casefold() == _plain(boundary).casefold()]
        if len(stop_lines) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_TABLE_END_BOUNDARY_NOT_UNIQUE")
        end = stop_lines[0] - 1
    requested = table["rows"]
    if not isinstance(requested, list) or not requested or len({row["label"].casefold() for row in requested}) != len(requested):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_ROW_IDENTITY_NOT_UNIQUE")
    for request in requested:
        found = [index for index in range(start + 1, end + 1)
                 if _row_label(row_lines[index - 1], page, index).casefold() == _plain(request["label"]).casefold()]
        if len(found) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_TOTAL_OR_CLASSIFICATION_ROW_NOT_UNIQUE")
        index, = found
        values = _numbers(row_lines[index - 1], page, index)
        aligned = tuple(_classified([column for column, _, _ in columns], values, caption))
        by_variable = {cell.variable: cell for cell in aligned if cell.variable}
        if not by_variable:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_NUMERIC_COLUMN_ALIGNMENT_UNBOUND")
        anchors = {column.variable: (header, unit) for column, header, unit in columns}
        yield request, index, caption, tuple(
            (cell, anchors[cell.variable][0], anchors[cell.variable][1] or cell.unit_label) for cell in by_variable.values()
        ), tuple(cell for cell in aligned if cell.variable is None), row_lines[start:index], _resource_type(caption)


def bind_source_inventory(*, profile, inventory, inventory_receipt, sources, assets, out):
    """Produce reproducible typed-candidate and unbound ledgers from routed originals."""
    from collections import Counter, defaultdict
    from hashlib import sha256
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from .contracts import canonical_json
    from .documents import DocumentCache
    from .registry import SHA256, load_profile, load_registry
    from .source_inventory import MAX_DOCUMENT_BYTES, _CheckedBody, _verify_edition
    from .sources import ContentStore, IntegrityError

    out, assets = Path(out), Path(assets)
    if (out.is_symlink() or out.resolve().is_relative_to(assets.resolve())
            or assets.resolve().is_relative_to(out.resolve()) or (out.exists() and any(out.iterdir()))):
        raise ValueError("Native table binding requires a new output directory separate from the source store")
    settings = load_profile(profile)
    identity = (settings.profile_id, settings.research_cutoff.isoformat(), settings.timezone.key)
    cohort = {project.project_id for project in load_registry(settings.registry_path).projects}
    inventory_bytes, receipt_bytes, source_bytes = (Path(path).read_bytes() for path in (inventory, inventory_receipt, sources))
    receipt, manifest = map(json.loads, (receipt_bytes, source_bytes))
    hashes = {key: sha256(value).hexdigest() for key, value in (
        ("source_case_inventory.jsonl", inventory_bytes), ("source_case_inventory_receipt.json", receipt_bytes),
        ("source_manifest.json", source_bytes),
    )}
    if (receipt.get("schema") != "source-case-inventory-v1" or receipt.get("candidate_only") is not True
            or receipt.get("gates", {}).get("SY0") != "PASS"
            or receipt["output_sha256"]["source_case_inventory.jsonl"] != hashes["source_case_inventory.jsonl"]
            or receipt["input_sha256"]["source_manifest_sha256"] != hashes["source_manifest.json"]
            or any(tuple(data[key] for key in ("profile_id", "cutoff", "timezone")) != identity
                   for data in (manifest, receipt))):
        raise ValueError("Native binding inputs do not match the pinned source-case inventory and manifest")
    cases = [json.loads(line) for line in inventory_bytes.splitlines()]
    if len({row["case_id"] for row in cases}) != len(cases) or any(
        row.get("candidate_only") is not True or row.get("formal_admission") is not False or row.get("project_id") not in cohort
        or not isinstance(row.get("source_sha256"), str) or not SHA256.fullmatch(row["source_sha256"])
        for row in cases
    ):
        raise ValueError("Native binding inventory violates candidate or Project identity")
    native = defaultdict(list)
    lineage = []
    for case in cases:
        if case["kind"] == "NATIVE_TABLE_TOTAL_ROW":
            native[case["source_sha256"]].append(case)
        else:
            lineage.append({
                "case_id": case["case_id"], "project_id": case["project_id"], "source_sha256": case["source_sha256"],
                "candidate_only": True, "formal_admission": False, "source_position_id": case.get("source_position_id"),
                "status": "EXISTING_REVIEWED_TOKEN" if case["kind"] == "REVIEWED_BINDING_ORIGINAL_TOKEN" else "NO_TABLE_STRUCTURE",
            })
    catalog = defaultdict(list)
    for edition in manifest["editions"]:
        if edition["sha256"] in native:
            catalog[edition["sha256"]].append(edition)
    cas = ContentStore(assets)
    typed, unbound, sources_read = [], [], []
    def result(case, status, reason, **extra):
        return {
            "case_id": case["case_id"], "project_id": case["project_id"], "source_sha256": case["source_sha256"],
            "edition_id": case["edition_id"], "physical_page": case["physical_page"], "table": case["table"],
            "source_position_id": case.get("source_position_id"), "source_token": case["source_token"],
            "candidate_only": True, "formal_admission": False, "status": status, "reason": reason, **extra,
        }
    for sha, records in sorted(native.items()):
        try:
            if cas.path(sha).stat().st_size > MAX_DOCUMENT_BYTES:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "SOURCE_EXCEEDS_DOCUMENT_BYTE_LIMIT")
            content = cas.read(sha)
        except (OSError, IntegrityError, DocumentError) as error:
            issue = (error.outcome if isinstance(error, DocumentError) else
                     "SCIENTIFIC_CONFLICT" if isinstance(error, IntegrityError) else "SOURCE_UNAVAILABLE")
            for record in records:
                item = result(record, issue, "ORIGINAL_PDF_UNAVAILABLE_OR_INVALID")
                unbound.append(item)
                lineage.append(item)
            continue
        proof_reader = _CheckedBody(cas, sha, content)
        eligible = defaultdict(list)
        for edition in catalog[sha]:
            proof = _verify_edition(edition, proof_reader, identity[1], settings.timezone)
            if proof["status"] == "ELIGIBLE" and edition["kind"] == "pdf":
                for project_id in edition["project_ids"]:
                    eligible[project_id, edition["edition_id"]].append(edition)
        if not any(eligible[case["project_id"], case["edition_id"]] for case in records):
            for case in records:
                item = result(case, "EVIDENCE_INSUFFICIENT", "EXACT_ELIGIBLE_PROJECT_EDITION_UNBOUND")
                unbound.append(item)
                lineage.append(item)
            continue
        try:
            view = DocumentCache().pdf(content, sha)
        except DocumentError as error:
            for case in records:
                item = result(case, error.outcome, str(error))
                unbound.append(item)
                lineage.append(item)
            continue
        sources_read.append({"source_sha256": sha, "native_pages_decoded": len(view.pages), "runtime": view.runtime})
        table_cache = {}
        locator_pattern = re.compile(r"PDF page (\d+), line (\d+)")
        for case in records:
            try:
                page = case["physical_page"]
                row = locator_pattern.fullmatch(case["row"]["locator"])
                caption = locator_pattern.fullmatch(case["table"]["locator"])
                if not row or not caption or int(row[1]) != page or int(caption[1]) != page:
                    raise DocumentError("EVIDENCE_INSUFFICIENT", "INVENTORY_NATIVE_LOCATOR_UNBOUND")
                chosen = eligible[case["project_id"], case["edition_id"]]
                if not chosen:
                    raise DocumentError("EVIDENCE_INSUFFICIENT", "EXACT_ELIGIBLE_PROJECT_EDITION_UNBOUND")
                raw_line = view.locate(page, line=int(row[2]))
                if raw_line.text != case["row"]["text"]:
                    raise DocumentError("SCIENTIFIC_CONFLICT", "PINNED_NATIVE_ROW_DIFFERS_FROM_ORIGINAL")
                position = case["token_columns"]
                original = view.locate(page, line=int(row[2]), column=position["start"], end_column=position["end"])
                if original.text != case["source_token"]:
                    raise DocumentError("SCIENTIFIC_CONFLICT", "PINNED_NATIVE_TOKEN_DIFFERS_FROM_ORIGINAL")
                key = (page, int(row[2]), int(caption[2]))
                if key not in table_cache:
                    table_cache[key] = {
                        (cell.value.start_column, cell.value.end_column): cell
                        for cell in bind_page_row(view, page=page, row_line=int(row[2]), caption_line=int(caption[2]))
                    }
                bound = table_cache[key].get((position["start"], position["end"]))
                if bound is None or bound.variable is None:
                    raise DocumentError("EVIDENCE_INSUFFICIENT", bound.reason if bound else "NATIVE_TOKEN_NOT_A_UNIQUE_NUMERIC_CELL")
                details = bound.to_dict()
                details.pop("reason")
                identity_payload = [RULE, sha, case["project_id"], details["value_anchor"], bound.variable, bound.unit]
                candidate_id = sha256(canonical_json(identity_payload).encode()).hexdigest()
                urls = sorted({edition["url"] for edition in chosen})
                item = result(case, "TYPED_CANDIDATE", bound.reason, candidate_id=candidate_id, source_urls=urls,
                              quantity_kind=("ORE_MASS" if bound.variable == "ore_mt" else "CONTAINED_OXIDE_MASS"
                                             if bound.variable == "contained_treo_kt" else "GRADE"
                                             if bound.variable == "grade" else "OXIDE_COMPOSITION"),
                              material_basis=None, denominator_basis=None, estimate_effective_date=None,
                              reporting_standard=None, cutoff_context=None, **details)
                typed.append(item)
            except DocumentError as error:
                item = result(case, error.outcome, str(error), token_columns=case.get("token_columns"))
                unbound.append(item)
            lineage.append(item)
    outputs = {
        "typed_candidate_observations.jsonl": typed,
        "unbound_numeric_tokens.jsonl": unbound,
        "value_evidence_lineage.jsonl": lineage,
    }
    report = {
        "schema": RULE, "profile_id": identity[0], "cutoff": identity[1], "timezone": identity[2],
        "candidate_only": True, "formal_scientific_values_added": 0, "inputs": hashes,
        "status": "FAIL" if any(row["status"] == "SCIENTIFIC_CONFLICT" for row in unbound) else
                  "BLOCKED" if len(sources_read) != len(native) else "PASS",
        "source_documents_verified_and_parsed": len(sources_read), "source_documents": sources_read,
        "case_count": len(cases), "distinct_native_source_positions": len({row["source_position_id"]
            for rows in native.values() for row in rows}),
        "typed_candidate_sightings": len(typed), "distinct_typed_native_positions": len({row["source_position_id"] for row in typed}),
        "distinct_project_candidates": len({row["candidate_id"] for row in typed}),
        "unbound_candidate_sightings": len(unbound),
        "status_by_case": dict(Counter(row["status"] for row in lineage)),
        "issue_by_case": dict(Counter(row["reason"] for row in unbound)),
    }
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".table-binding-", dir=out) as temporary:
        stage = Path(temporary)
        for name, rows in outputs.items():
            (stage / name).write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")
        report["outputs"] = {name: sha256((stage / name).read_bytes()).hexdigest() for name in outputs}
        (stage / "table_binding_receipt.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
        for name in (*outputs, "table_binding_receipt.json"):
            (stage / name).replace(out / name)
    return report

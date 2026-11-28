"""Bind native PDF numeric cells to physically aligned resource table headers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re
import unicodedata

from .documents import DocumentError, PDFDocument
from .normalization import KNOWN_OXIDE_SPECIES, reported_interval
from .observations import NUMBER

RULE = "native-resource-table-layout-v1"
DETECTION_RULE = "native-resource-table-detection-v5"
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
_CANDIDATE_VALUE = re.compile(_VALUE.pattern + r"(?P<footnote>[*†‡])?", re.I)
_UNIT = re.compile(
    r"(?<![\w.])(?:(?:[‘’']\s*)?000\s+tonnes?|billion\s+tonnes?|million\s+tonnes?|(?-i:M)\s+tonnes?|thousand\s+tonnes?|kg\s*/\s*t|"
    r"ppm|Bt|Mt|kt|tonnes?|t)(?!\w)|%", re.I,
)
_OXIDES = {part.upper(): part for part in KNOWN_OXIDE_SPECIES}
_OXIDE = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, sorted(_OXIDES))) + r")(?![A-Za-z0-9])", re.I)
_TREO = re.compile(r"\b(?:TREO|total\s+rare\s+earth\s+oxides?)\b", re.I)
_TREO_MODIFIER = re.compile(r"\bTREO\s*(?:[+−-]|plus\b|minus\b|including\b|excluding\b)\s*(?:Y(?:2O3)?|Sc(?:2O3)?|Ce(?:O2)?)\b", re.I)
_NON_ORE_HEADER = re.compile(r"\b(?:HM|heavy[- ]mineral|concentrat(?:e|es|ion)|product|recoverable|annual|production|tpa|ktpa|per\s+year)\b", re.I)
_ORE = re.compile(r"\b(?:ore(?:\s+mass)?|tonnage|tonnes?)\b", re.I)
_CONTAINED = re.compile(r"\b(?:contained|content|mass|tonnage|tonnes?)\b", re.I)
_MASS = {"t", "kt", "Mt", "Bt"}
_UNITS = {
    "t": "t", "tonne": "t", "tonnes": "t", "kt": "kt", "mt": "Mt", "bt": "Bt",
    "thousand tonne": "kt", "thousand tonnes": "kt", "million tonne": "Mt", "million tonnes": "Mt",
    "billion tonne": "Bt", "billion tonnes": "Bt", "m tonne": "Mt", "m tonnes": "Mt", "%": "%", "ppm": "ppm", "kg/t": "kg/t",
}
_OXIDE_GRADE_UNITS = {"%", "ppm", "kg/t"}
_CATEGORY_LABEL = re.compile(
    r"(?:mineral resource |resource |jorc )?(?:classification|category|class)|"
    r"block(?: id)?|deposit|prospect|domain|formation|zone|area|resource type|material type", re.I,
)
_VINTAGE_LABEL = re.compile(r"\b(?:MRE|estimate|resource|update)\b.*\b(?:19|20)\d{2}\b", re.I)
_INCOMPATIBLE_PARENT = re.compile(
    r"(?:%\s*of\s*(?:total\s+)?HM|\bHEAVY[- ]MINERAL\b|"
    r"\b(?:HM|THM)\s+assemblage\b|\bconcentrate\b)", re.I,
)
_RATIO_HEADER = re.compile(r"\b(?:ratio|share|recovery|yield)\b|\b(?:MREO|HREO|LREO)\s*/\s*TREO\b", re.I)
_SAME_TABLE_CONTINUED = re.compile(r"\b(?:cont\.?|continued|continuation)\b", re.I)
_CELL_UNKNOWN = re.compile(r"(?:[-–—]|n/?a|not reported|\.\.)", re.I)
_BULK_PLACEHOLDER = re.compile(r"(?:material\s*)?(?:\(?(?:Bt|Mt|kt)\)?)", re.I)
_CLASSIFICATION = re.compile(r"(?:jorc )?(?:mineral )?(?:resource )?(?:classification|category|class)", re.I)
_UNIT_NOTE = re.compile(
    r"^\s*(?P<marker>[*†‡])\s*(?P<quantity>TREO(?: grade)?|(?:ore )?tonnage|tonnes?)\s+"
    r"(?:is |are )?(?:reported|stated|expressed)\s+in\s+(?P<unit>ppm|kg/t|%|Mt|kt|Bt|t)\.?\s*$", re.I,
)
_NOTE_HEADER = re.compile(r"\s*(?:[:\-–—]\s*)?(?:notes?|units?)\s*[:.]?\s*", re.I)
_THRESHOLD_HEADER = re.compile(r"\b(?:cut[ -]?offs?|thresholds?|COG)\b", re.I)
_CANDIDATE_RESOURCE = re.compile(r"(?<![A-Za-z])mineral\s+resources?\b", re.I)
_CLASSIFIED_RESOURCE = re.compile(r"\b(?:measured|indicated|inferred)\s+resources?\b", re.I)
_BARE_TONNES = re.compile(r"tonnes?", re.I)
_EXPLICIT_WEIGHT = re.compile(
    r"(?:[‘’']?000\s+tonnes?|(?:billion|million|thousand|M)\s+tonnes?|Bt|Mt|kt)", re.I,
)
_WEIGHT_TIER = re.compile(r"(?:billion(?:s)?|million(?:s)?|thousand(?:s)?|[‘’']?000s?)", re.I)
_SCALED_TONNES = re.compile(r"\btonnes?\s*\(([^()]*)\)\s*$", re.I)
_DENOMINATOR_PARENT = re.compile(r"^\s*(?:rare earth oxides?|(?:mineral )?assemblage)\s*\((\d{1,2})\)\s*$", re.I)
_NON_ORE_DENOMINATOR = re.compile(
    r"\bpercentage of\s+(?:in[ -]situ\s+)?(?:THM|HM|heavy minerals?|concentrate)\b", re.I,
)
_RESOURCE_MASS_HEADER = re.compile(r"(?:mineral )?resources?\s*\(?\s*(?:Bt|Mt|kt|t)\s*\)?", re.I)
_MATERIAL_MASS = re.compile(r"material\s*\(?(?:Bt|Mt|kt|t)\)?", re.I)
_IN_SITU = re.compile(r"\bin[- ]situ\b", re.I)
_ADJACENT_QUANTITIES = re.compile(r"(?P<first>[^ ]+:TREO)[ \t]+(?P<second>Contained\s+TREO)", re.I)
_PROJECT_TITLE = re.compile(
    r"\b(?P<name>[\w'’.-]+(?:\s+[\w'’.-]+){0,3}?)\s+(?:REE|Rare Earths?)\s+Project\b", re.I,
)
_COMPANION_MODIFIER = re.compile(r'\s*(?:[+−-]|(?:including|excluding)\b|\((?!["“]?TREO["”]?\s*\)))', re.I)
_PHYSICAL_HOLDS = (
    (_THRESHOLD_HEADER, "CUTOFF_THRESHOLD_IS_NOT_A_RESOURCE_GRADE"),
    (_TREO_MODIFIER, "CHEMICALLY_MODIFIED_TREO_IS_NOT_STANDARD_TREO"),
    (_RATIO_HEADER, "REPORTED_RATIO_IS_NOT_A_WHOLE_ORE_GRADE"),
    (_INCOMPATIBLE_PARENT, "NON_WHOLE_ORE_COLUMN_BASIS"),
    (_NON_ORE_HEADER, "NON_WHOLE_ORE_COLUMN_BASIS"),
    (re.compile(r"\b(?:MREO|HREO|LREO|NdPr|DyTb)\b|(?:Pr6O11|Nd2O3)\s*\+|\benrichment\b", re.I),
     "REPORTED_AGGREGATE_OR_ENRICHMENT_REQUIRES_OWN_CHEMICAL_CONTEXT"),
    (re.compile(r"\b(?:density|volume|P2O5|Nb2O5|ZrO2|U3O8|ThO2|Sc2O3|Ta2O5|ThU|BD)\b", re.I),
     "OTHER_ANALYTE_OR_PHYSICAL_QUANTITY"),
)


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
    header_tiers: tuple[Span, ...] = ()


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
    header_tiers: tuple[Span, ...] = ()
    layout_method: str | None = None
    alignment_offsets: tuple[int, ...] = ()
    footnote_marker: Span | None = None

    def to_dict(self) -> dict:
        return {
            "rule": RULE, "variable": self.variable, "native_unit": self.unit, "chemical_basis": self.chemical_basis,
            "value_anchor": self.value.to_dict(), "caption_anchors": [anchor.to_dict() for anchor in self.caption],
            "header_anchor": self.header.to_dict() if self.header else None,
            "unit_anchor": self.unit_label.to_dict() if self.unit_label else None,
            "reason": self.reason, "raw_rounding_interval": self.raw_rounding_interval,
            **({"parent_header_anchor": self.parent_header.to_dict()} if self.parent_header else {}),
            **({"detection_rule": DETECTION_RULE, "layout_method": self.layout_method,
                "header_tier_anchors": [span.to_dict() for span in self.header_tiers],
                "source_alignment_offsets": self.alignment_offsets} if self.layout_method else {}),
            **({"footnote_marker_anchor": self.footnote_marker.to_dict()} if self.footnote_marker else {}),
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
    units = set()
    for match in _UNIT.finditer(_plain(label)):
        text = re.sub(r"\s*/\s*", "/", match[0].casefold())
        units.add("kt" if re.fullmatch(r"[‘’']?\s*000\s+tonnes?", text) else _UNITS[text])
    return units


def _unit_label(label: str) -> str | None:
    text = _plain(label)
    parenthetical = re.search(r"\(([^()]*)\)\s*$", text)
    if parenthetical and len(units := _units(parenthetical[1])) == 1:
        return next(iter(units))
    normalized = text.strip("()[] ").casefold()
    if normalized in {"million", "thousand", "billion"}:
        return {"million": "Mt", "thousand": "kt", "billion": "Bt"}[normalized]
    return next(iter(units)) if len(units := _units(label)) == 1 else None


def _scale_tier(text):
    text = _plain(text).strip("()[] ").casefold()
    if not _WEIGHT_TIER.fullmatch(text):
        return None
    return {"billion": "Bt", "million": "Mt", "thousand": "kt"}.get(text.rstrip("s"), "kt")


def _candidate_header_unit(label):
    match = _SCALED_TONNES.search(_plain(label))
    return (_scale_tier(match[1]) if match else _scale_tier(label)) or _unit_label(label)


def _role(label: str, unit: str) -> tuple[str, str | None] | None:
    label = _plain(label)
    treo = bool(_TREO.search(label))
    species = {_OXIDES[item[0].upper()] for item in _OXIDE.finditer(label)}
    aggregate = re.search(r"\b(?:MREO|HREO|LREO|NdPr)\b", label, re.I)
    if (_NON_ORE_HEADER.search(label) or _TREO_MODIFIER.search(label) or _RATIO_HEADER.search(label)
            or treo and (species or aggregate)):
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


def _candidate_role(label: str, unit: str) -> tuple[str, str | None] | None:
    label = _plain(label)
    if _THRESHOLD_HEADER.search(label) or _ADJACENT_QUANTITIES.match(label):
        return None
    if unit in _MASS and _RESOURCE_MASS_HEADER.fullmatch(label):
        return "ore_mt", None
    return _role(label, unit)


def _parsed_value(span: Span) -> tuple[Span, Span | None]:
    match = _VALUE.fullmatch(span.text)
    values = []
    for name in ("number", "unit" if match["unit"] else "percent"):
        values.append(Span(span.physical_page, span.line, span.start_column + match.start(name),
                           span.start_column + match.end(name) - 1, match[name]) if match[name] else None)
    return tuple(values)


def _candidate_tokens(line: str, page: int, index: int) -> tuple[Span, ...]:
    return tuple(span for span in _spans(line, page, index) if _CANDIDATE_VALUE.fullmatch(span.text))


def _strip_marker(token: Span) -> tuple[Span, Span | None]:
    if token.text[-1] not in "*†‡":
        return token, None
    value = replace(token, text=token.text[:-1], end_column=token.end_column - 1)
    marker = replace(token, text=token.text[-1], start_column=token.end_column)
    return value, marker


def _caption_block(lines, page, start, end, *, reviewed=False):
    if not _CAPTION.match(lines[start - 1]) and not (reviewed and _REVIEWED_RESOURCE_HEADING.fullmatch(lines[start - 1])):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "NATIVE_TABLE_CAPTION_UNBOUND")
    return tuple(_line_span(lines, line, page) for line in range(start, end + 1) if lines[line - 1].strip())


def _resource_type(caption: tuple[Span, ...]) -> str | None:
    text = " ".join(span.text for span in caption)
    if _RESERVE.search(text) or _TARGET.search(text):
        return "ORE_RESERVE" if _RESERVE.search(text) else "EXPLORATION_TARGET"
    return "MINERAL_RESOURCE" if _RESOURCE.search(text) else None


def _resource_class_heading(headers):
    labels = [header for header in headers if _plain(header.text).casefold() == "resource"]
    classes = [header for header in headers if _plain(header.text).casefold() == "classification"]
    return any(_overlap(left, right) for left in labels for right in classes) or any(
        _plain(header.text).casefold() == "resource classification" for header in headers
    )


def _candidate_caption_type(lines, page, caption, first_header, first_data):
    existing = _resource_type(caption)
    if existing:
        return existing
    text = " ".join(span.text for span in caption)
    if _CANDIDATE_RESOURCE.search(text):
        return "MINERAL_RESOURCE"
    if not (re.match(r"^\s*table\s+\w", text, re.I) and _THRESHOLD_HEADER.search(text)
            and _TREO.search(text) and re.search(r"\bresources?\b", text, re.I)):
        return None
    headers = tuple(span for line in range(first_header, first_data)
                    for span in _spans(lines[line - 1], page, line))
    if not any(_unit_label(span.text) in _MASS for span in headers):
        return None
    if _CLASSIFIED_RESOURCE.search(text) or _resource_class_heading(headers):
        return "PRINTED_CLASSIFIED_RESOURCE_CUTOFF"
    return None


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
        unit = _candidate_header_unit(entry.text)
        if not unit:
            continue
        aligned = [label for label in entries if label.line != entry.line and _overlap(label, entry)]
        # A bare printed mass unit needs a uniquely aligned ore/analyte header,
        # except an inline tonnes unit already provides the resource mass label.
        if len(aligned) > 1:
            continue
        header = aligned[0] if aligned else entry
        label = " ".join((header.text, entry.text)) if header != entry else entry.text
        role = _candidate_role(label, unit)
        if not role:
            continue
        variable, chemistry = role
        columns.append(Column(variable, unit, header, entry, chemistry))
    return tuple(dict.fromkeys(columns))


def _center(span: Span) -> int:
    return span.start_column + span.end_column


def _lexical_role(text):
    return bool(_ORE.search(text) or _TREO.search(text) or _OXIDE.search(text))


def _nearest_group(rail, groups):
    distances = [(abs(_center(rail) - _center(group)), group) for group in groups]
    closest = min(distance for distance, _ in distances)
    nearest = [group for distance, group in distances if distance == closest]
    return nearest[0] if len(nearest) == 1 else None


def _native_column_unit(anchor: Span, tiers: tuple[Span, ...]):
    # Keep the exact unit-bearing source span. A preliminary class word such as
    # "Tonnes" cannot override a physically paired explicit "(Mt)".
    combined = _plain(" ".join(span.text for span in tiers))
    explicit = [(span, unit) for span in tiers if (unit := _candidate_header_unit(span.text)) is not None
                and not re.fullmatch(r"tonnes?", _plain(span.text), re.I)]
    if len({unit for _, unit in explicit}) > 1:
        return None
    if explicit:
        unit = explicit[0][1]
        return unit, explicit[-1][0]
    unit = _candidate_header_unit(combined)
    if unit:
        source = next((span for span in reversed(tiers) if _candidate_header_unit(span.text) == unit), anchor)
        return unit, source
    return None


def _footnote_unit(tiers, notes, label):
    if not notes:
        return None
    marks = {span.text[-1] for span in tiers if span.text[-1] in "*†‡"}
    if len(marks) != 1 or len(related := notes.get(next(iter(marks)), ())) != 1:
        return None
    unit, quantity, anchor = related[0]
    role = _role(label, unit)
    if role is not None and role == _role(quantity, unit):
        return unit, anchor
    return None


def _header_cells(raw: str, page: int, index: int) -> tuple[Span, ...]:
    separated = []
    for span in _spans(raw, page, index):
        match = _ADJACENT_QUANTITIES.fullmatch(span.text)
        if match:
            for name in ("first", "second"):
                separated.append(Span(page, index, span.start_column + match.start(name),
                                      span.start_column + match.end(name) - 1, match[name]))
        else:
            separated.append(span)
    return tuple(separated)


def _header_rail(span, rails, unit_tier):
    hits = tuple(rail for rail in rails if _overlap(span, rail))
    if not hits and unit_tier and (_UNIT.fullmatch(_plain(span.text).strip("()[] ")) or _scale_tier(span.text)):
        nearby = tuple(rail for rail in rails if 0 < max(span.start_column - rail.end_column,
                                                       rail.start_column - span.end_column) <= 2)
        return nearby[0] if len(nearby) == 1 else None
    return hits[0] if len(hits) == 1 else None


def _layered_columns(lines, page, header_indices, *, notes=None):
    rows = tuple(_header_cells(lines[index - 1], page, index) for index in header_indices)
    rows = tuple(row for row in rows if row)
    if not rows:
        return (), (), {}
    rails = max(rows, key=lambda row: (len(row), sum(_lexical_role(span.text) for span in row), -row[0].line))
    if len(rails) < 2:
        return (), (), {}
    stacks = {rail: [rail] for rail in rails}
    possible_mass_lanes = sum(_unit_label(span.text) in _MASS for span in rails)
    parents = next((row for row in rows if len(row) >= 2 and
                    sum(bool(_VINTAGE_LABEL.search(span.text)) for span in row) >= 2), ())
    for row in rows:
        if row == rails or row == parents:
            continue
        unit_tier = sum(bool(_UNIT.fullmatch(_plain(span.text).strip("()[] ")) or _scale_tier(span.text)) for span in row) >= 2
        for span in row:
            if rail := _header_rail(span, rails, unit_tier):
                stacks[rail].append(span)
    classified = any(_CLASSIFICATION.fullmatch(_plain(" ".join(span.text for span in sorted(group, key=lambda s: s.line))))
                     for group in stacks.values())
    in_situ = any(_IN_SITU.search(span.text) for span in rails)
    columns = []
    for rail, entries in stacks.items():
        tiers = tuple(sorted(entries, key=lambda span: (span.line, span.start_column)))
        label = " ".join(span.text for span in tiers)
        declared = _native_column_unit(rail, tiers) or _footnote_unit(tiers, notes, label)
        if not declared:
            continue
        unit, unit_span = declared
        role = _candidate_role(label, unit)
        if role is None and classified and possible_mass_lanes == 1 and _BULK_PLACEHOLDER.fullmatch(_plain(label)):
            role = "ore_mt", None
        if role is None and in_situ and unit in _MASS and _MATERIAL_MASS.fullmatch(_plain(label)):
            role = "ore_mt", None
        if not role:
            continue
        parent = _nearest_group(rail, parents) if parents else None
        if parents and (parent is None or not _VINTAGE_LABEL.search(parent.text)):
            continue
        if any(_INCOMPATIBLE_PARENT.search(note.text) and _overlap(note, rail)
               for row in rows for note in row):
            continue
        semantic = next((span for span in tiers if _lexical_role(span.text)), rail)
        # The leaf rail remains the geometry anchor. Preserve words separately
        # when the analyte or unit is printed on a different line.
        anchors = tiers if unit_span in tiers else (*tiers, unit_span)
        columns.append((rail, Column(role[0], unit, semantic, unit_span, role[1], parent, anchors)))
    multiplicity = {}
    for _, column in columns:
        key = column.variable, column.parent_header
        multiplicity[key] = multiplicity.get(key, 0) + 1
    return rails, tuple((rail, column) for rail, column in columns
                        if multiplicity[column.variable, column.parent_header] == 1), stacks


def _body_cells(raw, page, index):
    spans = _spans(raw, page, index)
    start = next((pos for pos, span in enumerate(spans) if _CANDIDATE_VALUE.fullmatch(span.text) or _CELL_UNKNOWN.fullmatch(span.text)), len(spans))
    label = _plain(" ".join(span.text for span in spans[:start])).casefold()
    cells = spans[start:]
    if not label or any(not (_CELL_UNKNOWN.fullmatch(span.text) or _CANDIDATE_VALUE.fullmatch(span.text)) for span in cells):
        return "", ()
    return label, cells


def _numeric_rails(rails):
    """Discard only explicit label columns, retaining unknown numeric lanes."""
    return tuple(rail for rail in rails if not _CATEGORY_LABEL.fullmatch(_plain(rail.text)))


def _columns_at_offset(rails, cells, offset):
    matches = []
    for span in cells:
        shifted_start, shifted_end = span.start_column - offset, span.end_column - offset
        eligible = [index for index, rail in enumerate(rails)
                    if max(shifted_start, rail.start_column) <= min(shifted_end, rail.end_column)]
        if len(eligible) != 1 or eligible[0] in matches:
            return None
        matches.append(eligible[0])
    return tuple(matches)


def _witnessed_alignment(rails, row, witnesses):
    if len(rails) < 3 or len(row) != len(rails) or len(witnesses) < 2:
        return None
    candidates = {}
    for offset in range(-14, 15):
        if all(_columns_at_offset(rails, values, offset) is not None for values in witnesses):
            found = _columns_at_offset(rails, row, offset)
            if found is not None:
                candidates.setdefault(found, []).append(offset)
    if len(candidates) != 1:
        return None
    assignment, offsets = next(iter(candidates.items()))
    return assignment, tuple(offsets)


def _fenced_lane(rails, row, witnesses):
    """Use witnessed physical lanes when header labels have varying widths."""
    if len(rails) < 3 or len(row) != len(rails) or len(witnesses) < 2:
        return None
    borders = tuple(left.end_column + right.start_column for left, right in zip(rails, rails[1:]))
    # A short extension only on the actual edge lanes accommodates the native
    # alignment of a shorter heading over a longer quantity token.
    lower, upper = 2 * (rails[0].start_column - 8), 2 * (rails[-1].end_column + 8)
    bounds = (lower, *borders, upper)
    for values in (row, *witnesses):
        for token, start, end in zip(values, bounds, bounds[1:]):
            if not start < _center(token) < end:
                return None
    return tuple(range(len(rails))), ()


def _layout_value(column, number, unit, caption, method, offsets=()):
    native_unit = _unit_label(unit.text) if unit else column.unit
    if unit and _unit_label(unit.text) != column.unit:
        # A header saying only “Tonnes” can label the quantity, while each
        # cell supplies its explicit scale. A printed header scale wins.
        if (column.variable != "ore_mt" or native_unit not in _MASS
                or _plain(column.unit_label.text).casefold() not in {"tonne", "tonnes"}):
            return None
    _, lower, upper = reported_interval(number.text)
    return CellBinding(
        column.variable, native_unit, column.chemical_basis, number, caption, column.header,
        unit or column.unit_label, "ORIGINAL_HEADER_UNIT_AND_ROW_BOUND",
        (format(lower, "f"), format(upper, "f")), column.parent_header,
        column.header_tiers, method, offsets,
    )


def _direct_layered_values(rails, columns, parsed, caption):
    by_rail = dict(columns)
    assigned = {}
    for number, inline in parsed:
        alternatives = [rail for rail in rails if _overlap(rail, number)]
        if len(alternatives) != 1:
            continue
        rail = alternatives[0]
        column = by_rail.get(rail)
        if column is None or sum(_overlap(rail, value) for value, _ in parsed) != 1:
            continue
        bound = _layout_value(column, number, inline, caption, "MULTI_TIER_HEADER_EXACT_ALIGNMENT")
        if bound:
            assigned[number.start_column] = bound
    return assigned


def _explicit_hold(tiers):
    ordered = tuple(sorted(tiers, key=lambda span: span.line))
    text = _plain(" ".join(span.text for span in ordered))
    return next(((reason, ordered) for keyword, reason in _PHYSICAL_HOLDS if keyword.search(text)), None)


def _layout_holds(rails, tiers, parsed, fit=None, body=()):
    if fit:
        assignments, _ = fit
        pairs = ((token, rails[index]) for token, index in zip(body, assignments, strict=True)
                 if _CANDIDATE_VALUE.fullmatch(token.text))
    else:
        matches = ((value, [rail for rail in rails if _overlap(rail, value)]) for value, _ in parsed)
        pairs = ((value, hits[0]) for value, hits in matches if len(hits) == 1)
    holds = {}
    for raw, rail in pairs:
        hold = _explicit_hold(tiers[rail])
        if hold:
            holds[raw.start_column] = hold
    return holds


def _layered_values(lines, page, row_line, first_header, first_data, caption, *, cross_page=False, notes=None):
    headers = range(first_header, first_data)
    rails, columns, tiers = _layered_columns(lines, page, headers, notes=notes)
    raw = lines[row_line - 1]
    tokens = _candidate_tokens(raw, page, row_line)
    parsed = [_parsed_value(_strip_marker(span)[0]) for span in tokens]
    direct = _direct_layered_values(rails, columns, parsed, caption)
    if cross_page:
        return direct, _layout_holds(rails, tiers, parsed)
    numeric_rails = _numeric_rails(rails)
    label, body = _body_cells(raw, page, row_line)
    measured = {}
    for line in range(first_data, row_line):
        name, values = _body_cells(lines[line - 1], page, line)
        if name and name != label and len(values) == len(numeric_rails):
            measured.setdefault(name, values)
    witnesses = tuple(measured.values())
    fit = _witnessed_alignment(numeric_rails, body, witnesses)
    method = "TABLE_WIDE_ROW_ALIGNMENT"
    if not fit:
        fit = _fenced_lane(numeric_rails, body, witnesses)
        method = "WITNESSED_NATIVE_COLUMN_LANES"
    if not fit:
        return direct, _layout_holds(rails, tiers, parsed)
    arrangement, offsets = fit
    by_rail = dict(columns)
    for span, number in zip(body, arrangement, strict=True):
        column = by_rail.get(numeric_rails[number])
        if not column or not _CANDIDATE_VALUE.fullmatch(span.text):
            continue
        value, inline = _parsed_value(_strip_marker(span)[0])
        item = _layout_value(column, value, inline, caption, method, offsets)
        if item:
            direct[value.start_column] = item
    return direct, _layout_holds(numeric_rails, tiers, parsed, fit, body)


def _header_start(lines, page, start, end):
    return next((line for line in range(start, end) if len(_spans(lines[line - 1], page, line)) >= 2), end)


def _first_body(lines, page, start, end):
    return next((index for index in range(start, end) if len(_candidate_tokens(lines[index - 1], page, index)) >= 2), end)


def _source_caption(document, lines, page, caption_line, first_header, first_data):
    local = _caption_block(lines, page, caption_line, first_header - 1)
    continued = _SAME_TABLE_CONTINUED.search(" ".join(span.text for span in local))
    if not continued and _candidate_caption_type(lines, page, local, first_header, first_data) in {
        "MINERAL_RESOURCE", "PRINTED_CLASSIFIED_RESOURCE_CUTOFF",
    }:
        return local, False
    marker = _CAPTION.match(lines[caption_line - 1])
    if page <= 1 or not continued or _resource_type(local) in {"ORE_RESERVE", "EXPLORATION_TARGET"}:
        raise DocumentError("SCIENTIFIC_CONFLICT", "MINERAL_RESOURCE_SCOPE_REQUIRED")
    earlier = _lines(document, page - 1)
    candidates = [index for index, line in enumerate(earlier, start=1) if _CAPTION.match(line)]
    if not candidates:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "CONTINUED_RESOURCE_CAPTION_UNAVAILABLE")
    start = candidates[-1]
    previous_marker = _CAPTION.match(earlier[start - 1])
    if _plain(previous_marker[0]).casefold() != _plain(marker[0]).casefold():
        raise DocumentError("SCIENTIFIC_CONFLICT", "CROSS_PAGE_TABLE_CONTINUATION_MISMATCH")
    prior = _caption_block(earlier, page - 1, start, _header_start(earlier, page - 1, start + 1, len(earlier) + 1) - 1)
    if _resource_type(prior) != "MINERAL_RESOURCE":
        raise DocumentError("SCIENTIFIC_CONFLICT", "MINERAL_RESOURCE_SCOPE_REQUIRED")
    return (*prior, *local), True


def _latest_header_panel(lines, page, first_header, first_data, row_line):
    """Do not carry the first grid's header into a printed sibling grid."""
    latest = first_header, first_data, first_header
    previous = first_data
    for line in range(first_data + 1, row_line + 1):
        if len(_candidate_tokens(lines[line - 1], page, line)) < 2:
            continue
        header = _header_start(lines, page, max(previous + 1, line - 12), line)
        if header < line:
            _, columns, tiers = _layered_columns(lines, page, range(header, line))
            shapes = len(columns) + sum(_explicit_hold(group) is not None for group in tiers.values())
            units = sum(any(_candidate_header_unit(span.text) for span in group) for group in tiers.values())
            if shapes >= 2 and units >= 2:
                latest = header, line, previous + 1
        previous = line
    return latest


def _scope_notes(lines, page, start, header_end, row_line):
    contexts = []
    for line in range(start, header_end):
        parent = _DENOMINATOR_PARENT.fullmatch(lines[line - 1])
        if not parent:
            continue
        notes = []
        for number in range(row_line + 1, min(row_line + 22, len(lines) + 1)):
            raw = lines[number - 1]
            if _CAPTION.match(raw):
                break
            if re.match(rf"^\s*{parent[1]}[.)]?\s+", raw) and _NON_ORE_DENOMINATOR.search(raw):
                notes.append(_line_span(lines, number, page))
        if len(notes) == 1:
            contexts.append((_line_span(lines, line, page), notes[0]))
    return contexts[0] if len(contexts) == 1 else ()


def _scope_hold(cells, witnesses):
    return tuple(replace(cell, variable=None, unit=None, chemical_basis=None, raw_rounding_interval=None,
                         reason="NON_WHOLE_ORE_DENOMINATOR_PROVED_BY_LINKED_NOTE",
                         header_tiers=(*cell.header_tiers, *witnesses), layout_method="EXPLICIT_PANEL_DENOMINATOR_HOLD")
                 if cell.reason != "CUTOFF_THRESHOLD_IS_NOT_A_RESOURCE_GRADE" else cell for cell in cells)


def _continued_values(document, page, row, row_line, caption):
    """Carry the immediate prior header only if the full native grid is unchanged."""
    earlier = _lines(document, page - 1)
    start = _header_start(earlier, page - 1, caption[0].line + 1, len(earlier) + 1)
    first_data = _first_body(earlier, page - 1, start, len(earlier) + 1)
    rails, columns, _ = _layered_columns(earlier, page - 1, range(start, first_data))
    numeric_rails = _numeric_rails(rails)
    _, selected = _body_cells(row, page, row_line)
    if len(numeric_rails) < 3 or len(selected) != len(numeric_rails):
        return {}
    witnessed = any(
        len(cells) == len(numeric_rails) and _columns_at_offset(numeric_rails, cells, 0) is not None
        for index in range(first_data, len(earlier) + 1)
        for _, cells in (_body_cells(earlier[index - 1], page - 1, index),)
    )
    if not witnessed or _columns_at_offset(numeric_rails, selected, 0) is None:
        return {}
    values = _direct_layered_values(
        rails, columns, [_parsed_value(span) for span in _numbers(row, page, row_line)], caption,
    )
    return {key: replace(value, layout_method="CONTINUED_TABLE_ORIGINAL_HEADER") for key, value in values.items()}


def _footnote_block(lines, page, start, end):
    for line in range(start, end):
        raw = lines[line - 1]
        if _CAPTION.match(raw):
            break
        note = _UNIT_NOTE.fullmatch(raw)
        if note:
            yield note["marker"], (_unit_label(note["unit"]), note["quantity"], _line_span(lines, line, page))


def _printed_unit_notes(document, lines, page, row_line, caption, header_uses_marker):
    if not header_uses_marker:
        return None
    local = list(_footnote_block(lines, page, row_line + 1, min(row_line + 9, len(lines) + 1)))
    marker = _CAPTION.match(caption[0].text)
    if marker and page < len(document.pages):
        adjacent = _lines(document, page + 1)
        for number, raw in enumerate(adjacent[:10], start=1):
            lead = _CAPTION.match(raw)
            if lead:
                if _plain(lead[0]).casefold() == _plain(marker[0]).casefold() and _NOTE_HEADER.fullmatch(raw[lead.end():]):
                    local.extend(_footnote_block(adjacent, page + 1, number + 1, min(number + 9, len(adjacent) + 1)))
                break
    grouped = {}
    for mark, note in local:
        grouped.setdefault(mark, []).append(note)
    return grouped


def _companion_unit(document, page, name, ore, grade):
    title = re.escape(name)
    scope = re.compile(rf"\b{title}\s+(?:REE|Rare Earths?)\s+Project(?:['’]s)?\s+(?:maiden |updated )?"
                       rf"Mineral Resource Estimate(?:\s*\([^)]{{1,12}}\))?\s+of\s+{re.escape(ore.value.text)}\s+{re.escape(ore.unit)}\s*$", re.I)
    measurement = re.compile(rf"(?<!\w)(?P<mass>{re.escape(ore.value.text)}\s+{re.escape(ore.unit)})\s+grading\s+"
                             rf"{re.escape(grade.value.text)}\s+(?P<unit>ppm|%)\s+(?:Total Rare Earth Oxides?|TREO)(?![\w+-])", re.I)
    matches = []
    for physical_page in range(max(1, page - 2), page):
        local = _lines(document, physical_page)
        for line, raw in enumerate(local, start=1):
            for found in measurement.finditer(raw):
                preceding = " ".join(_plain(text) for text in (*local[max(0, line - 4):line - 1], raw[:found.end("mass")]))
                if not scope.search(preceding) or _COMPANION_MODIFIER.match(raw, found.end()):
                    continue
                anchor = Span(physical_page, line, found.start("unit") + 1, found.end("unit"), found["unit"])
                matches.append((anchor, _line_span(local, line, physical_page)))
    return matches[0] if len(matches) == 1 else None


def _companion_grade(document, page, lines, row_line, header, data, caption, cells):
    """Bind an unmodified total TREO lane to a unique named narrative witness."""
    if not re.match(r"^\s*(?:(?:grand|global|combined) )?total\b", lines[row_line - 1], re.I):
        return cells
    amounts = [item for item in cells if item.variable == "ore_mt"]
    if len(amounts) != 1 or any(item.variable == "grade" for item in cells) or amounts[0].unit not in _MASS:
        return cells
    title = " ".join(item.text for item in caption)
    marker = _CAPTION.match(title)
    name = _PROJECT_TITLE.match(title[marker.end():].lstrip(". :—-")) if marker else None
    if not name:
        return cells
    treo = [span for line in range(header, data) for span in _spans(lines[line - 1], page, line)
            if _plain(span.text).casefold() == "treo"]
    if len(treo) != 1:
        return cells
    eligible = [cell for cell in cells if cell.variable is None and cell.unit_label is None and _overlap(treo[0], cell.value)]
    if len(eligible) != 1 or not (support := _companion_unit(document, page, name["name"], amounts[0], eligible[0])):
        return cells
    unit, quote = support
    selected = eligible[0]
    _, low, high = reported_interval(selected.value.text)
    candidate = replace(selected, variable="grade", chemical_basis="TREO", unit=_unit_label(unit.text), header=treo[0],
                        unit_label=unit, reason="ORIGINAL_HEADER_UNIT_AND_ROW_BOUND", header_tiers=(treo[0], quote),
                        raw_rounding_interval=(format(low, "f"), format(high, "f")),
                        layout_method="NAMED_SAME_EDITION_RESOURCE_COMPANION")
    return tuple(candidate if cell == selected else cell for cell in cells)


def bind_page_row(document: PDFDocument, *, page: int, row_line: int, caption_line: int) -> tuple[CellBinding, ...]:
    """Bind proven native heading tiers and value cells without research admission."""
    lines = _lines(document, page)
    _line_span(lines, row_line, page)
    if not 1 <= caption_line < row_line or not _CAPTION.match(lines[caption_line - 1]):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "NATIVE_TABLE_CAPTION_UNBOUND")
    if any(_CAPTION.match(item) for item in lines[caption_line:row_line - 1]):
        raise DocumentError("SCIENTIFIC_CONFLICT", "ROW_OUTSIDE_SOURCE_TABLE")
    first_header = _header_start(lines, page, caption_line + 1, row_line)
    first_data = _first_body(lines, page, first_header, row_line)
    caption, continued = _source_caption(document, lines, page, caption_line, first_header, first_data)
    if continued:
        panel_start = first_header
    else:
        first_header, first_data, panel_start = _latest_header_panel(lines, page, first_header, first_data, row_line)
    continuation = (_continued_values(document, page, lines[row_line - 1], row_line, caption)
                    if continued and first_header == first_data else None)
    marked_header = any(any(mark in raw for mark in "*†‡") for raw in lines[first_header - 1:first_data - 1])
    notes = _printed_unit_notes(document, lines, page, row_line, caption, marked_header)
    cells = _classify_layout_row(lines, page, row_line, first_header, first_data, caption,
                                 continued=continued, continuation=continuation, notes=notes)
    if scope := _scope_notes(lines, page, panel_start, first_data, row_line):
        return _scope_hold(cells, scope)
    return _companion_grade(document, page, lines, row_line, first_header, first_data, caption, cells)


def _classify_layout_row(lines, page, row_line, first_header, first_data, caption, *,
                         continued=False, continuation=None, notes=None):
    headers = range(first_header, first_data)
    values = _numbers(lines[row_line - 1], page, row_line)
    legacy = tuple(_classified(_automatic_columns(lines, page, headers), values, caption))
    layered, holds = (continuation, {}) if continuation is not None else _layered_values(
        lines, page, row_line, first_header, first_data, caption, cross_page=continued, notes=notes,
    )
    known = {cell.value.start_column: cell for cell in legacy}
    results = []
    for token in _candidate_tokens(lines[row_line - 1], page, row_line):
        number, marker = _strip_marker(token)
        value, inline = _parsed_value(number)
        cell = known.get(value.start_column, CellBinding(None, None, None, value, caption, None, inline, "HEADER_OR_UNIT_UNBOUND"))
        alternative = layered.get(cell.value.start_column)
        if alternative and _explicit_mass_scale_over_bare(cell, alternative):
            cell = alternative
        elif alternative and cell.variable and (alternative.variable, alternative.unit, alternative.chemical_basis) != (
            cell.variable, cell.unit, cell.chemical_basis,
        ):
            cell = replace(cell, variable=None, unit=None, chemical_basis=None, header=None,
                           unit_label=None, reason="NATIVE_LAYOUT_METHODS_DISAGREE")
        else:
            cell = alternative if alternative and (cell.variable is None or alternative.parent_header) else cell
        if cell.variable is None and (hold := holds.get(value.start_column)) and cell.reason == "HEADER_OR_UNIT_UNBOUND":
            reason, anchors = hold
            cell = replace(cell, reason=reason, header=anchors[0], header_tiers=anchors,
                           layout_method="UNBOUND_PRINTED_HEADER_CONTEXT")
        results.append(replace(cell, footnote_marker=marker) if marker else cell)
    return tuple(results)


def _explicit_mass_scale_over_bare(generic, candidate):
    if (generic.variable != "ore_mt" or candidate.variable != "ore_mt" or generic.unit != "t"
            or candidate.unit not in {"Bt", "Mt", "kt"} or generic.unit_label is None or candidate.unit_label is None):
        return False
    if not _BARE_TONNES.fullmatch(_plain(generic.unit_label.text)):
        return False
    qualifier = _plain(candidate.unit_label.text).strip("()[] ")
    return bool(_EXPLICIT_WEIGHT.fullmatch(qualifier) or _scale_tier(qualifier))


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


def _reviewed_column_options(document, headers, entry, header_terms):
    if entry.get("unit_inline") is True:
        return [(term, None, segment, None) for term, segment in header_terms]
    unit_terms = _heading_terms(document, headers, entry["unit_line"], entry["unit_label"])
    return [
        (header, unit_label, header_segment, unit_segment)
        for header, header_segment in header_terms for unit_label, unit_segment in unit_terms
        if _overlap(header_segment, unit_segment) and _unit_label(unit_label.text) == entry["unit"]
    ]


def _reviewed_columns(document, headers, entries):
    columns = []
    for entry in entries:
        header_terms = _heading_terms(document, headers, entry["header_line"], entry["header"])
        header_terms, parent = _in_group(document, headers, entry, header_terms)
        options = _reviewed_column_options(document, headers, entry, header_terms)
        compatible = []
        for header, unit_label, header_segment, unit_segment in options:
            label = (f"{header_segment.text} {unit_segment.text}"
                     if unit_segment and header_segment != unit_segment else header_segment.text)
            role = _role(label, entry["unit"])
            if role and role[0] == entry["variable"]:
                compatible.append((
                    Column(role[0], entry["unit"], header_segment, unit_segment, role[1], parent), header, unit_label,
                ))
        if len(options) == 1 and not compatible:
            raise DocumentError("SCIENTIFIC_CONFLICT", "REVIEWED_COLUMN_QUANTITY_OR_CHEMISTRY_MISMATCH")
        if len(compatible) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COLUMN_OR_UNIT_NOT_UNIQUE")
        columns.extend(compatible)
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


def _same_source_span(outer, inner):
    return (outer is not None and inner is not None
            and (outer.physical_page, outer.line) == (inner.physical_page, inner.line) and _contains(outer, inner))


def _reviewed_values(document, page, row_line, caption, columns, aligned):
    """Complete a reviewed row using an independently bound original header and unit."""
    bound = {cell.variable: cell for cell in aligned if cell.variable}
    if len(bound) == len(columns) or caption[0].physical_page != page or not _CAPTION.match(caption[0].text):
        return bound
    try:
        discovered = bind_page_row(document, page=page, row_line=row_line, caption_line=caption[0].line)
    except DocumentError:
        return bound
    occupied = {cell.value for cell in bound.values()}
    for column, header, unit in columns:
        if column.variable in bound or unit is None:
            continue
        matches = [cell for cell in discovered
                   if cell.variable == column.variable and cell.unit == column.unit
                   and cell.chemical_basis == column.chemical_basis and cell.parent_header == column.parent_header
                   and cell.value not in occupied and _same_source_span(cell.header, header)
                   and _same_source_span(cell.unit_label, unit)]
        if len(matches) == 1:
            bound[column.variable] = matches[0]
            occupied.add(matches[0].value)
    return bound


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
        by_variable = _reviewed_values(document, page, index, caption, columns, aligned)
        if not by_variable:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_NUMERIC_COLUMN_ALIGNMENT_UNBOUND")
        anchors = {column.variable: (header, unit) for column, header, unit in columns}
        used = {cell.value for cell in by_variable.values()}
        yield request, index, caption, tuple(
            (cell, anchors[cell.variable][0], anchors[cell.variable][1] or cell.unit_label) for cell in by_variable.values()
        ), tuple(cell for cell in aligned if cell.variable is None and cell.value not in used), row_lines[start:index], _resource_type(caption)


def _baseline_overlap(case, bound, reviewed):
    ids = tuple(case.get("existing_reviewed_row_lexeme_overlap") or ())
    if not ids:
        return None
    raw = case["row"]["text"]
    position_count = sum(
        _parsed_value(numeric)[0].text == bound.value.text
        for numeric in _numbers(raw, bound.value.physical_page, bound.value.line)
    )
    matches = []
    for observation_id in ids:
        old = reviewed.get(observation_id)
        if old is None:
            continue
        unit = old.get("source_unit_as_reported") or ""
        known_unit = _unit_label(unit)
        if (
            position_count == 1 and old.get("role_guess") == bound.variable and known_unit == bound.unit
            and all(old.get(key) == case[key] for key in ("source_sha256", "project_id", "physical_page", "source_token"))
            and _plain((old.get("row") or {}).get("text") or "") == _plain(raw)
        ):
            matches.append(old)
    if len(matches) == 1:
        old, = matches
        return {
            "status": "EXISTING_REVIEWED_CELL_OVERLAP", "reviewed_observation_ids": [old["existing_observation_id"]],
            "reviewed_validation_id": old.get("existing_validation_id"),
            "reviewed_acceptance_id": old.get("existing_formal_acceptance_id"),
        }
    return {"status": "BASELINE_ROW_LEXEME_MATCH_UNRESOLVED", "reviewed_observation_ids": list(ids)}

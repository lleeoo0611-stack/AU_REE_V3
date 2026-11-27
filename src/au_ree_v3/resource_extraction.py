"""Native PDF strategies for whole-ore TREO Mineral Resource declarations."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
import re

from .documents import DocumentError
from .observations import CATEGORY, NUMBER, exact_value, half_width, read_resource

VARIABLES = ("ore_mt", "grade", "contained_treo_kt")
CANONICAL_UNITS = {"ore_mt": "Mt", "grade": "%", "contained_treo_kt": "kt TREO"}


@dataclass(frozen=True)
class ResourceCell:
    variable: str
    token: str
    raw_unit: str
    page: int
    table: str
    row: str
    column: str
    quote: str
    conflict: str | None = None

    @property
    def value(self) -> str:
        value = exact_value(self.token)
        if self.raw_unit == "t":
            parts = value.as_tuple()
            value = Decimal((parts.sign, parts.digits, parts.exponent - 3))
        return format(value, "f")


@dataclass(frozen=True)
class ResourceStatement:
    scope: str
    cutoff: str
    effective_date: str | None
    primary: bool
    measurements: tuple[ResourceCell, ...]
    context: tuple[tuple[int, str], ...] = ()


def _flatten(text: str) -> str:
    return " ".join(text.split()).replace("ﬁ", "fi")


def _unique(pattern: str, text: str, reason: str) -> re.Match:
    matches = list(re.finditer(pattern, text, flags=re.I))
    if len(matches) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", reason)
    return matches[0]


def _cells(tokens, page, table, row, quote, *, mass_unit="t"):
    return tuple(
        ResourceCell(variable, token, unit, page, table, row, column, quote)
        for variable, token, unit, column in zip(
            VARIABLES, tokens, ("Mt", "%", mass_unit), ("Tonnage", "TREO grade", "Contained TREO"), strict=True,
        )
    )


def _check_estimate(cells: tuple[ResourceCell, ...]) -> None:
    ore, grade, contained = [Decimal(cell.value) for cell in cells]
    with localcontext() as context:
        context.prec = sum(len(value.as_tuple().digits) for value in (ore, grade, contained)) + 12
        lower = (ore - half_width(ore)) * (grade - half_width(grade)) * 10
        upper = (ore + half_width(ore)) * (grade + half_width(grade)) * 10
        compatible = lower - half_width(contained) <= contained <= upper + half_width(contained)
    if ore <= 0 or not 0 < grade <= 100 or not compatible:
        raise DocumentError("SCIENTIFIC_CONFLICT", "TONNAGE_GRADE_CONTAINED_MISMATCH")


def _check_jorc_and_disclosure(pages: tuple[str, ...], publication_date: str) -> None:
    publication = date.fromisoformat(publication_date)
    first = _flatten(pages[0])
    printed_day = re.escape(f"{publication.day} {publication:%B %Y}")
    if not re.search(rf"\bASX Announcement\s+{printed_day}\b", first, flags=re.I):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ISSUER_DISCLOSURE_DATE_UNBOUND")
    if not re.search(r"\bJORC(?: Code)?\)?[,]?\s+2012\b", first, flags=re.I):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "JORC_2012_UNBOUND")


def narrative_table(pages: tuple[str, ...], binding: dict, publication_date: str) -> tuple[ResourceStatement, ...]:
    """Join an explicit narrative measurement to its cited same-page resource total."""
    settings = dict(
        binding, source={"publication_date": publication_date},
        material_basis="WHOLE_ORE", chemical_basis="TREO", category=CATEGORY,
    )
    evidence = read_resource(pages, settings)
    page = binding["page"]
    measurements = tuple(
        ResourceCell(variable, token, unit, page, table, row, column, quote)
        for variable, token, unit, table, row, column, quote in zip(
            VARIABLES, evidence.tokens, ("million tonnes", "%", "‘000 tonnes"),
            ("Narrative", "Narrative", evidence.table), ("Resource estimate", "Resource estimate", "Total"),
            ("Tonnage", "TREO grade", "Contained TREO"), evidence.quotes, strict=True,
        )
    )
    return (ResourceStatement(binding["scope_name"], evidence.cutoff_grade, binding["effective_date"], True,
                              measurements, ((page, evidence.title), (page, evidence.definition))),)


def classified_sections(pages: tuple[str, ...], binding: dict, publication_date: str) -> tuple[ResourceStatement, ...]:
    """Keep classification totals separate from component deposit subtotals."""
    _check_jorc_and_disclosure(pages, publication_date)
    page_number = binding["page"]
    page = pages[page_number - 1]
    deposit = re.escape(binding["deposit_name"])
    period = date.fromisoformat(binding["effective_date"])
    effective = re.escape(f"{period.day} {period:%B %Y}")
    title = _unique(
        rf"(Table [0-9]+): Mineral Resource estimate for the {deposit} deposit as at {effective} "
        rf"reported above a ({NUMBER})\s*% TREO cut-off grade\.",
        _flatten(page), "ESTIMATE_TITLE_OR_EFFECTIVE_DATE_UNBOUND",
    )
    headers = _unique(
        r"Tonnage\s+TREO\s+Dy2O3\s+Y2O3\s+Tb4O7\s+TREO\s+Classification\s+TREO\s+Mt\s+%\s+kg/t\s+kg/t\s+kg/t\s+%\s+t",
        _flatten(page), "CLASSIFICATION_TABLE_UNITS_UNBOUND",
    )
    definition = _unique(r"TREO = Total Rare Earth Oxides [–-] [^.]+\bY2O3\.", _flatten(page), "TREO_DEFINITION_UNBOUND")
    section_pattern = re.compile(rf"(?mi)^\s*{deposit} (.*?) Mineral Resource estimate[ \t]*$")
    sections = list(section_pattern.finditer(page))
    chosen = binding["section"]
    if sum(section[1] == chosen for section in sections) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "APPROVED_RESOURCE_SCOPE_UNBOUND")
    observations = []
    totals_pattern = re.compile(rf"(?mi)^[ \t]*(Total|Subtotal)[ \t]+({NUMBER})(?:[ \t]+({NUMBER})){{6}}[ \t]*$")
    tail = re.split(r"(?mi)^\s*Notes:", page, maxsplit=1)[0]
    for index, section in enumerate(sections):
        start = section.end()
        end = sections[index + 1].start() if index + 1 < len(sections) else len(tail)
        window = page[start:end]
        classification = all(re.search(rf"(?mi)^\s*{category}\s+{NUMBER}", window)
                             for category in ("Measured", "Indicated", "Inferred"))
        matches = list(totals_pattern.finditer(window))
        if not classification or len(matches) != 1:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "CLASSIFICATION_SECTION_TOTAL_UNBOUND")
        total = matches[0]
        cells = re.findall(NUMBER, total[0])
        primary = section[1] == chosen
        label = binding["scope_name"] if primary else f"{binding['deposit_name']} {section[1]} Mineral Resource"
        values = _cells((cells[0], cells[1], cells[6]), page_number, title[1], section[1] + " " + total[1], total[0].strip())
        _check_estimate(values)
        observations.append(ResourceStatement(
            label, title[2], binding["effective_date"], primary, values,
            ((page_number, title[0]), (page_number, headers[0]), (page_number, definition[0])),
        ))
    return tuple(observations)


def cutoff_series(pages: tuple[str, ...], binding: dict, publication_date: str) -> tuple[ResourceStatement, ...]:
    """Validate cutoff rows against their matching detailed JORC tables."""
    _check_jorc_and_disclosure(pages, publication_date)
    summary_page = binding["page"]
    details_page = binding["details_page"]
    page = pages[summary_page - 1]
    detailed = pages[details_page - 1]
    deposit = re.escape(binding["deposit_name"])
    title = _unique(
        rf"(Table [0-9]+): {deposit} Resource as adjusted for different cut-off grades\.",
        _flatten(page), "CUTOFF_SERIES_SCOPE_UNBOUND",
    )
    table = page[page.index(title[0]):].splitlines()
    if len(table) < 4 or not re.search(
        r"Cut-Off\s+Resource\s+TREO\s+NdPr:TREO\s+Contained TREO\s+Contained NdPr "
        r"\(%TREO\)\s+\(Mt\)\s+\(%\)\s+\(%\)\s+\(t\)\s+\(t\)",
        _flatten(" ".join(table[:3])), flags=re.I,
    ):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "CUTOFF_SERIES_UNITS_UNBOUND")
    summary_row = re.compile(rf"\s*({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+{NUMBER}%\s+({NUMBER})\s+{NUMBER}")
    result = []
    for row in table[3:]:
        match = summary_row.fullmatch(row)
        if not match:
            break
        threshold = match[1]
        prefix = rf"(Table [0-9]+): Summary of {deposit} Resources at {re.escape(threshold)}% TREO%? Cut-off\."
        detail_heading = _unique(prefix, detailed, "CUTOFF_DETAIL_SCOPE_UNBOUND")
        segment = re.split(r"(?mi)^\s*Table [0-9]+:", detailed[detail_heading.end():], maxsplit=1)[0]
        if not all(re.search(rf"(?mi)^\s*{category}\s+Subtotal\s+", segment)
                   for category in ("Measured", "Indicated", "Inferred")):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "CUTOFF_DETAIL_CLASSIFICATIONS_UNBOUND")
        if not re.search(r"\(Mt\)\s+\(%\)\s+Ratio \(%\)\s+TREO \(t\)", _flatten(segment), flags=re.I):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "CUTOFF_DETAIL_UNITS_UNBOUND")
        detail_total = _unique(
            rf"(?m)^[ \t]*TOTAL[ \t]+({NUMBER})[ \t]+({NUMBER})[ \t]+{NUMBER}[ \t]+({NUMBER})[ \t]+{NUMBER}[ \t]*$",
            segment, "CUTOFF_DETAIL_TOTAL_UNBOUND",
        )
        summary_values = _cells(match.groups()[1:], summary_page, title[1], f"{threshold}% cutoff", row.strip())
        detail_values = _cells(detail_total.groups(), details_page, detail_heading[1], "Total", detail_total[0].strip())
        _check_estimate(summary_values)
        _check_estimate(detail_values)
        values = []
        for summary, detail in zip(summary_values, detail_values, strict=True):
            if Decimal(summary.value) == Decimal(detail.value):
                values.append(summary)
            else:
                conflict = "CONFLICTING_CONTAINED_TREO_IN_SAME_STATEMENT" if summary.variable == "contained_treo_kt" else "CONFLICTING_SUMMARY_DETAIL_VALUE"
                for item in (summary, detail):
                    values.append(ResourceCell(item.variable, item.token, item.raw_unit, item.page, item.table,
                                               item.row, item.column, item.quote, conflict))
        primary = exact_value(threshold) == exact_value(binding["primary_cutoff"])
        result.append(ResourceStatement(
            binding["scope_name"], threshold, None, primary, tuple(values),
            ((summary_page, title[0]), (details_page, detail_heading[0]), (details_page, detail_total[0].strip())),
        ))
    if sum(item.primary for item in result) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "APPROVED_CUTOFF_UNBOUND")
    return tuple(result)


STRATEGIES = {
    "narrative-and-cited-table-v1": narrative_table,
    "classified-section-totals-v1": classified_sections,
    "cutoff-series-and-detail-tables-v1": cutoff_series,
}

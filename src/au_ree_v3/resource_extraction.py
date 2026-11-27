"""Native PDF strategies for whole-ore TREO Mineral Resource declarations."""

from dataclasses import dataclass, replace
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
    chemical_basis: str | None = None
    evidence_issue: str | None = None

    @property
    def normalized_unit(self) -> str:
        if self.variable.endswith("_ppm") or (self.variable == "grade" and self.raw_unit == "ppm"):
            return "ppm"
        return CANONICAL_UNITS[self.variable]

    @property
    def factor(self) -> str:
        if self.raw_unit == "t":
            return "0.000001" if self.variable == "ore_mt" else "0.001"
        return "1"

    @property
    def value(self) -> str:
        value = exact_value(self.token)
        shift = Decimal(self.factor).as_tuple().exponent
        parts = value.as_tuple()
        return format(Decimal((parts.sign, parts.digits, parts.exponent + shift)), "f")


@dataclass(frozen=True)
class ResourceStatement:
    scope: str
    cutoff: str
    effective_date: str | None
    primary: bool
    measurements: tuple[ResourceCell, ...]
    context: tuple[tuple[int, str], ...] = ()
    category: str = CATEGORY
    cutoff_unit: str = "%"
    cutoff_analyte: str = "TREO"
    reported_chemical_label: str | None = None


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
        ppm = cells[1].normalized_unit == "ppm"
        factor = Decimal("0.001") if ppm else Decimal(10)
        lower = (ore - half_width(ore)) * (grade - half_width(grade)) * factor
        upper = (ore + half_width(ore)) * (grade + half_width(grade)) * factor
        compatible = lower - half_width(contained) <= contained <= upper + half_width(contained)
    if ore <= 0 or not 0 < grade <= (1_000_000 if ppm else 100) or not compatible:
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


def _declared_context(pages: tuple[str, ...], binding: dict) -> tuple[tuple[int, str], ...]:
    context = []
    for item in binding["context"]:
        actual = _flatten(pages[item["page"] - 1])
        quote = _flatten(item["quote"])
        if quote.casefold() not in actual.casefold():
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_STATEMENT_CONTEXT_UNBOUND")
        context.append((item["page"], quote))
    declared = " ".join(quote for _, quote in context)
    for key, reason in (("effective_date", "ESTIMATE_DATE_EVIDENCE_UNBOUND"),
                        ("publication_date", "ISSUER_DISCLOSURE_DATE_UNBOUND")):
        token = binding.get(key)
        if token:
            day_precision = len(token) == 10
            estimate = date.fromisoformat(token if day_precision else token + "-01")
            date_pattern = f"{estimate.day} {estimate:%B},? {estimate.year}" if day_precision else f"{estimate:%B %Y}"
            if not re.search(rf"\b{date_pattern}\b", declared, re.I):
                raise DocumentError("EVIDENCE_INSUFFICIENT", reason)
    cutoff = binding["cutoff"]
    reported_cutoff = re.search(NUMBER, cutoff["reported"])
    if (cutoff["reported"].casefold() not in declared.casefold() or reported_cutoff is None
            or exact_value(reported_cutoff[0]) != exact_value(cutoff["value"])
            or cutoff["unit"].casefold() not in cutoff["reported"].casefold()):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "CUTOFF_EVIDENCE_UNBOUND")
    return tuple(context)


def _chemical_holds(cells, binding):
    if binding.get("requires_chemical_review"):
        return tuple(replace(cell, evidence_issue="TREO_CHEMICAL_DEFINITION_UNBOUND") for cell in cells)
    return cells


def positioned_total(pages: tuple[str, ...], binding: dict, publication_date: str | None) -> tuple[ResourceStatement, ...]:
    """Bind a fixed-column native total row to its scoped caption and original units."""
    context = _declared_context(pages, binding)
    page = binding["page"]
    table = binding["table"]
    raw_page = pages[page - 1]
    caption = _unique(re.escape(table["caption"]), raw_page, "POSITIONED_TABLE_SCOPE_UNBOUND")
    if "resource" not in caption[0].casefold() or re.search(r"ore reserve|exploration target", caption[0], re.I):
        raise DocumentError("SCIENTIFIC_CONFLICT", "MINERAL_RESOURCE_SCOPE_REQUIRED")
    relative = raw_page[caption.end():]
    # Restrict the numeric join to the caption's table, excluding following
    # reserve, prospect, and nested section tables on the physical page.
    window = re.split(r"(?mi)^[ \t]*(?:Table|Figure) [0-9]+[:. ]", relative, maxsplit=1)[0]
    row_label = table["row"]
    column_count = table["column_count"]
    numeric = r"(?P<tokens>" + NUMBER + (rf"[ \t]+{NUMBER}" * (column_count - 1)) + r")"
    row = _unique(rf"(?m)^[ \t]*{re.escape(row_label)}[ \t]+{numeric}[ \t]*$", window, "POSITIONED_TOTAL_ROW_UNBOUND")
    if _flatten(table["header"]).casefold() not in _flatten(window[:row.start()]).casefold():
        raise DocumentError("EVIDENCE_INSUFFICIENT", "POSITIONED_TABLE_UNITS_UNBOUND")
    tokens = re.findall(NUMBER, row["tokens"])
    cells = tuple(
        ResourceCell(variable, tokens[column["index"]], column["unit"], page, table["caption"], row_label,
                     column["column"], row[0].strip())
        for variable, column in zip(VARIABLES, table["columns"], strict=True)
    )
    _check_estimate(cells)
    cutoff = binding["cutoff"]
    return (ResourceStatement(
        binding["scope_name"], cutoff["value"], binding.get("effective_date"), True,
        _chemical_holds(cells, binding), context,
        binding["category"], cutoff["unit"], cutoff["analyte"], binding.get("reported_chemical_label"),
    ),)


def narrative_ppm_total(pages: tuple[str, ...], binding: dict, publication_date: str | None) -> tuple[ResourceStatement, ...]:
    """Require exact ore tonnage and grade agreement between an issuer narrative and table."""
    context = _declared_context(pages, binding)
    page = binding["page"]
    summary_page = binding["summary_page"]
    text = _flatten(pages[summary_page - 1])
    summary = _unique(
        rf"{re.escape(binding['narrative_lead'])} ({NUMBER}) Mt grading ({NUMBER}) ppm "
        rf"Total Rare Earth Oxides \(\"TREO\"\) for ({NUMBER}) tonnes of contained TREO\.",
        text, "PPM_NARRATIVE_QUANTITIES_UNBOUND",
    )
    raw_page = pages[page - 1]
    table = binding["table"]
    heading = _unique(re.escape(table["caption"]), raw_page, "PPM_TABLE_CAPTION_UNBOUND")
    window = raw_page[heading.end():]
    if _flatten(table["header"]).casefold() not in _flatten(window).casefold():
        raise DocumentError("EVIDENCE_INSUFFICIENT", "PPM_TABLE_HEADER_UNBOUND")
    row = _unique(
        rf"(?m)^[ \t]*Total[ \t]+({NUMBER})[ \t]+({NUMBER})(?:[ \t]+{NUMBER}){{3}}[ \t]*$",
        window.split("Notes on Mineral Resource Estimation", 1)[0], "PPM_RESOURCE_TOTAL_ROW_UNBOUND",
    )
    if tuple(map(exact_value, row.groups())) != tuple(exact_value(summary[i]) for i in (1, 2)):
        raise DocumentError("SCIENTIFIC_CONFLICT", "NARRATIVE_AND_PPM_TOTAL_MISMATCH")
    cells = (
        ResourceCell("ore_mt", row[1], "Mt", page, heading[0], "Total", "Mt", row[0].strip()),
        ResourceCell("grade", row[2], "ppm", page, heading[0], "Total", "TREO", row[0].strip()),
        ResourceCell("contained_treo_kt", summary[3], "t", summary_page, "Issuer summary", "Maiden estimate",
                     "Contained TREO", summary[0]),
    )
    _check_estimate(cells)
    cutoff = binding["cutoff"]
    return (ResourceStatement(
        binding["scope_name"], cutoff["value"], binding.get("effective_date"), True,
        _chemical_holds(cells, binding), context + ((summary_page, summary[0]),),
        binding["category"], cutoff["unit"], cutoff["analyte"], binding.get("reported_chemical_label"),
    ),)


STRATEGIES = {
    "narrative-and-cited-table-v1": narrative_table,
    "classified-section-totals-v1": classified_sections,
    "cutoff-series-and-detail-tables-v1": cutoff_series,
    "positioned-mineral-resource-total-v1": positioned_total,
    "corroborated-ppm-narrative-total-v1": narrative_ppm_total,
}

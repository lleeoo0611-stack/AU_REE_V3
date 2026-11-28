"""Native PDF strategies for whole-ore TREO Mineral Resource declarations."""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext
import re
import unicodedata

from .documents import DocumentError, PDFDocument
from .normalization import KNOWN_OXIDE_SPECIES, exact_unit_exponent, reported_interval
from .resource_table_binding import reviewed_table_rows
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
    evidence_context: dict | None = None
    material_basis: str | None = None
    denominator_basis: str | None = None

    @property
    def normalized_unit(self) -> str:
        if self.variable.endswith("_ppm") or (self.variable == "grade" and self.raw_unit in {"ppm", "kg/t"}):
            return "ppm"
        return CANONICAL_UNITS[self.variable]

    @property
    def factor(self) -> str:
        exponent = exact_unit_exponent(self.raw_unit, self.normalized_unit)
        return format(Decimal((0, (1,), exponent)), "f")

    @property
    def value(self) -> str:
        value = exact_value(self.token)
        shift = exact_unit_exponent(self.raw_unit, self.normalized_unit)
        parts = value.as_tuple()
        return format(Decimal((parts.sign, parts.digits, parts.exponent + shift)), "f")


@dataclass(frozen=True)
class ResourceStatement:
    scope: str
    cutoff: str | None
    effective_date: str | None
    primary: bool
    measurements: tuple[ResourceCell, ...]
    context: tuple[tuple[int, str], ...] = ()
    category: str = CATEGORY
    cutoff_unit: str | None = "%"
    cutoff_analyte: str | None = "TREO"
    reported_chemical_label: str | None = None
    treo_members: tuple[str, ...] = ()
    reporting_standard: str | None = None
    resource_class: str | None = None


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


def _check_estimate(cells: tuple[ResourceCell, ...], *, native_precision: bool = False) -> None:
    ore, grade, contained = [Decimal(cell.value) for cell in cells]
    with localcontext() as context:
        context.prec = sum(len(value.as_tuple().digits) for value in (ore, grade, contained)) + 15
        ppm = cells[1].normalized_unit == "ppm"
        factor = Decimal("0.001") if ppm else Decimal(10)
        if native_precision:
            intervals = [
                tuple(Decimal(bound) * Decimal(cell.factor)
                      for bound in cell.evidence_context["native_table_binding"]["raw_rounding_interval"])
                for cell in cells
            ]
        else:
            intervals = [(amount - half_width(amount), amount + half_width(amount)) for amount in (ore, grade, contained)]
        (ore_low, ore_high), (grade_low, grade_high), (contained_low, contained_high) = intervals
        lower = ore_low * grade_low * factor
        upper = ore_high * grade_high * factor
        compatible = lower <= contained_high and contained_low <= upper
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


def _positioned_oxide_columns(table, binding) -> None:
    """Validate the mapped value columns before interpreting a table as TREO."""
    labelled = {
        "treo": ("treo", "treo"),
        "% treo": ("% treo", "treo t"),
        "in-situ treo + y2o3": ("in-situ treo + y2o3 grade", "in-situ treo + y2o3"),
    }
    label = _flatten(binding.get("reported_chemical_label") or "").casefold()
    grade, amount = table["columns"][1:]
    text = tuple(_flatten(column["column"]).casefold() for column in (grade, amount))
    header = _flatten(table["header"]).casefold()
    if (labelled.get(label) != text or any(column not in header for column in text)
            or re.search(r"\btreo\s*(?:\+|and)\s*(?:sc2o3|scandium|sc)\b", header)):
        raise DocumentError("SCIENTIFIC_CONFLICT", "NON_CANONICAL_TREO_VALUE_COLUMNS")


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
    _positioned_oxide_columns(table, binding)
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


_NATIVE_CONTEXT_ROLES = {
    "EFFECTIVE_DATE", "PUBLICATION_DATE", "CUTOFF", "JORC", "MATERIAL_BASIS", "CHEMICAL_DEFINITION",
    "SCOPE", "PRECISION",
}
_JORC_2012 = re.compile(
    r"\b(?:JORC(?:\s+Code)?[^.;]{0,30}2012|2012[^.;]{0,35}JORC(?:\s+Code)?|"
    r"2012 Edition of the Australasian Code for Reporting of Exploration Results, Mineral Resources and Ore Reserves)\b",
    re.I,
)
_BULK_MATERIAL = re.compile(
    r"\b(?:whole[- ]ore|(?:dry|undiluted|in[- ]situ)\s+(?:ore|tonnage|tonnes|resource)|"
    r"tonnages?[^.]{0,48}dry\s+basis|dry\s+tonnage\s+basis|contained\s*\(in[- ]situ\)\s+resources)\b", re.I,
)
_NON_BULK = re.compile(r"\b(?:heavy\s+mineral|concentrat(?:e|es|ion)|wet\s+tonnage)\b", re.I)
_CONTEXT_DATE_ROLE = re.compile(r"\b(?:as\s+at|as\s+of|effective(?:\s+date)?|(?:mineral\s+resources?|estimate)\s+at)\b", re.I)
_CLASSIFICATIONS = ("MEASURED", "INDICATED", "INFERRED")
_OXIDE_NAMES = {species.upper(): species for species in KNOWN_OXIDE_SPECIES}


def _native_quote(pages, page, quote):
    if type(page) is not int or not 1 <= page <= len(pages) or not isinstance(quote, str):
        return None
    original = _flatten(pages[page - 1])
    quote = _flatten(quote)
    return quote if quote and original.casefold().count(quote.casefold()) == 1 else None


def _date_present(quote, expected, *, effective):
    if not isinstance(expected, str) or not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", expected):
        return False
    parsed = date.fromisoformat(expected + "-01" if len(expected) == 7 else expected)
    month_year = rf"\b{parsed:%B},?\s+{parsed.year}\b"
    if len(expected) == 7:
        return bool(re.search(month_year, quote, re.I) and (not effective or "resourc" in quote.casefold()))
    return bool(re.search(rf"\b{parsed.day:02d}\s+{month_year}|\b{parsed.day}\s+{month_year}", quote, re.I)
                and (not effective or _CONTEXT_DATE_ROLE.search(quote)))


def _chemical_species(quotes):
    definitions = []
    quotes = tuple(unicodedata.normalize("NFKC", quote) for quote in quotes)
    all_quotes = " ".join(quotes)
    if re.search(r"\bTREO and MREO estimates were derived by summing the individual kriged REO grades\b", all_quotes, re.I):
        for quote in quotes:
            match = re.search(r"\bTREO,\s+MREO,\s+([^.]*)", quote, re.I)
            if match:
                labels = re.split(r"\s*,\s*|\s+and\s+", match[1].strip())
                if len(labels) == 15 and len(set(part.upper() for part in labels)) == 15 and all(
                    label.upper() in _OXIDE_NAMES for label in labels
                ):
                    definitions.append(tuple(sorted(_OXIDE_NAMES[label.upper()] for label in labels)))
    for quote in quotes:
        match = re.fullmatch(r"TREO\s*=\s*(?:Total Rare Earth Oxides\s*)?(.*)", quote.rstrip("."), re.I)
        if not match:
            continue
        labels = [part.strip().upper() for part in re.split(r"[, +]+", match[1].strip("() "))]
        if not labels or any(label not in _OXIDE_NAMES for label in labels) or len(labels) != len(set(labels)):
            return ()
        definitions.append(tuple(sorted(_OXIDE_NAMES[label] for label in labels)))
    return definitions[0] if definitions and len(set(definitions)) == 1 else ()


def _dated_jorc_annex(pages, caption, binding, link, first, last):
    """Require a citation from the statement and one continuous dated JORC annex."""
    heading = " ".join(span.text for span in caption)
    same_page = caption[0].physical_page
    start, end = link["start"]["page"], link["end"]["page"]
    reference = link.get("reference", {})
    citation = _native_quote(pages, reference.get("page"), reference.get("quote"))
    scope = link["scope_reference"].casefold()
    marker = re.match(r"(?:table|figure)\s+\d+\b", heading, re.I)
    dated_title = "JORC TABLE 1 ASSESSMENT CRITERIA FOR "
    if (not marker or not citation or reference["page"] != same_page or start <= same_page
            or not re.search(rf"\b{re.escape(marker[0])}\s+and\s+(?:the\s+)?attached JORC Table 1\b", citation, re.I)
            or not first.upper().startswith(dated_title) or scope not in first.casefold() or scope not in last.casefold()):
        return False
    effective = binding.get("effective_date")
    if not (_date_present(heading, effective, effective=True) and _date_present(first, effective, effective=True)):
        return False
    if sum(page.upper().count(dated_title) for page in pages) != 1:
        return False
    return all("JORC TABLE 1 ASSESSMENT CRITERIA" in _flatten(page).upper() for page in pages[start - 1:end])


def _native_sections(pages, binding, caption):
    heading = " ".join(span.text for span in caption)
    routes = []
    for link in binding.get("context_links", ()):
        phrase = link.get("scope_reference", "")
        start, end = link.get("start", {}), link.get("end", {})
        first = _native_quote(pages, start.get("page"), start.get("quote"))
        last = _native_quote(pages, end.get("page"), end.get("quote"))
        dated = link.get("kind") == "dated-jorc-annex-v1"
        if not first or not last or len(phrase.split()) < (2 if dated else 3) or phrase.casefold() not in heading.casefold():
            continue
        if not 1 <= start["page"] <= end["page"] <= len(pages):
            continue
        if dated:
            if _dated_jorc_annex(pages, caption, binding, link, first, last):
                routes.append((start["page"], end["page"]))
            continue
        bounded_statement = all(phrase.casefold() in item.casefold() for item in (first, last))
        reference = link.get("reference", {})
        cited = _native_quote(pages, reference.get("page"), reference.get("quote")) if reference else None
        appendix = bool(cited and phrase.casefold() in cited.casefold() and _JORC_2012.search(first)
                        and re.search(r"^Appendix\s+(?:A|One|1)\b", first, re.I)
                        and re.search(r"\bJORC Code Table 1,? included as Appendix\s+(?:A|One|1)\b", cited, re.I)
                        and sum(bool(re.search(r"(?im)^\s*Appendix\s+(?:A|One|1)\b[^\n]*JORC", body))
                                for body in pages) == 1)
        if bounded_statement or appendix:
            routes.append((start["page"], end["page"]))
    return tuple(routes)


def _native_link(binding, page, caption, quote, sections):
    if page in {span.physical_page for span in caption} or any(first <= page <= last for first, last in sections):
        return True
    reference = binding.get("statement_reference")
    heading = " ".join(span.text for span in caption)
    return bool(isinstance(reference, str) and len(reference.split()) >= 3
                and reference.casefold() in heading.casefold() and reference.casefold() in quote.casefold())


def _native_context(pages, binding, caption):
    context = []
    roles = {}
    disconnected = []
    sections = _native_sections(pages, binding, caption)
    for item in binding.get("context", ()):
        role, page = item.get("role"), item.get("page")
        quote = _native_quote(pages, page, item.get("quote"))
        if role not in _NATIVE_CONTEXT_ROLES or quote is None:
            continue
        context.append((page, quote))
        if role in {"PUBLICATION_DATE", "JORC"} or _native_link(binding, page, caption, quote, sections):
            roles.setdefault(role, []).append(quote)
        else:
            disconnected.append({"role": role, "physical_page": page, "quote": quote})
    effective = binding.get("effective_date")
    publication = binding.get("publication_date")
    date_ok = any(_date_present(quote, effective, effective=True) for quote in roles.get("EFFECTIVE_DATE", ()))
    publication_ok = any(_date_present(quote, publication, effective=False) for quote in roles.get("PUBLICATION_DATE", ()))
    jorc = any(_JORC_2012.search(quote) for quote in roles.get("JORC", ()))
    material = "WHOLE_ORE" if (
        any(_BULK_MATERIAL.search(quote) for quote in roles.get("MATERIAL_BASIS", ()))
        and not any(_NON_BULK.search(quote) for quote in roles.get("MATERIAL_BASIS", ()))
    ) else None
    if any(_NON_BULK.search(quote) for quote in roles.get("MATERIAL_BASIS", ())):
        material = "HEAVY_MINERAL_OR_CONCENTRATE"
    treo_members = _chemical_species(roles.get("CHEMICAL_DEFINITION", ()))
    cutoff = binding.get("cutoff") or {}
    reported = cutoff.get("reported", "")
    found = list(re.finditer(NUMBER, reported))
    cutoff_bound = (reported and len(found) == 1
                    and cutoff.get("unit") and cutoff["unit"].casefold() in reported.casefold()
                    and cutoff.get("analyte") and cutoff["analyte"].casefold() in reported.casefold()
                    and exact_value(found[0][0]) == exact_value(cutoff.get("value", ""))
                    and any(reported.casefold() in quote.casefold() for quote in roles.get("CUTOFF", ())))
    issues = [
        reason for ready, reason in (
            (jorc, "JORC_2012_UNBOUND"), (date_ok, "ESTIMATE_DATE_EVIDENCE_UNBOUND"),
            (publication_ok, "ISSUER_DISCLOSURE_DATE_UNBOUND"), (cutoff_bound, "CUTOFF_EVIDENCE_UNBOUND"),
            (material == "WHOLE_ORE", "WHOLE_ORE_MATERIAL_BASIS_UNBOUND"),
            (treo_members, "TREO_CHEMICAL_DEFINITION_UNBOUND"),
        ) if not ready
    ]
    return tuple(context), roles, disconnected, bool(jorc), effective if date_ok else None, (cutoff if cutoff_bound else {}), material, treo_members, issues


def _category_from_original(label, earlier, caption):
    direct = {word for word in _CLASSIFICATIONS if re.search(rf"\b{word}\b", label, re.I)}
    if direct == {"INFERRED"}:
        return "INFERRED_ONLY"
    if direct == {"MEASURED"}:
        return "MEASURED_ONLY"
    if direct == {"INDICATED"}:
        return "INDICATED_ONLY"
    source_rows = " ".join(line for line in earlier if re.search(r"\d", line))
    observed = {word for word in _CLASSIFICATIONS if re.search(rf"\b{word}\b", source_rows, re.I)}
    if not observed:
        heading = " ".join(span.text for span in caption)
        observed = {word for word in _CLASSIFICATIONS if re.search(rf"\b{word}\b", heading, re.I)}
    aggregates = {
        frozenset(_CLASSIFICATIONS): CATEGORY,
        frozenset({"MEASURED", "INDICATED"}): "MEASURED_INDICATED_TOTAL",
        frozenset({"INDICATED", "INFERRED"}): "INDICATED_INFERRED_TOTAL",
        frozenset({"INFERRED"}): "INFERRED_ONLY",
    }
    return aggregates.get(frozenset(observed))


def _publisher_precision(token, interval, quotes):
    figures = {int(match[1]) for quote in quotes
               for match in re.finditer(r"\brounded to (\d+) significant figures\b", quote, re.I)}
    if len(figures) != 1:
        return interval, None
    digits, = figures
    if not 1 <= digits <= 12:
        return interval, None
    _, low, high = reported_interval(token, significant_figures=digits)
    return (format(low, "f"), format(high, "f")), digits


def _vintage_gap(mapped, expected_columns, effective):
    relevant = [entry for entry in expected_columns if entry["variable"] in VARIABLES]
    groups = {(entry.get("group_header"), entry.get("group_line")) for entry in relevant}
    if any(label for label, _ in groups) and len(groups) != 1:
        return "INCOMPATIBLE_NATIVE_MEASUREMENT_PARENT_HEADERS"
    years = {year for entry in relevant for year in re.findall(r"\b(?:19|20)\d{2}\b", entry.get("group_header", ""))}
    if years and (effective is None or years != {effective[:4]}):
        return "NATIVE_PARENT_HEADER_ESTIMATE_VINTAGE_CONFLICT"
    source_groups = {cell.parent_header for cell, _, _ in mapped if cell.variable in VARIABLES}
    if len(source_groups) > 1:
        return "INCOMPATIBLE_NATIVE_MEASUREMENT_PARENT_HEADERS"
    return None


def native_resource_table(pages: tuple[str, ...], binding: dict, publication_date: str | None) -> tuple[ResourceStatement, ...]:
    """Extract original numeric cells from proven native layout, without selecting research values."""
    source_sha = binding.get("source_sha256")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", source_sha):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_SOURCE_SHA_UNBOUND")
    if publication_date != binding.get("publication_date"):
        raise DocumentError("SCIENTIFIC_CONFLICT", "ISSUER_DISCLOSURE_DATE_MISMATCH")
    document = PDFDocument(source_sha, pages, "native-pdf-layout")
    results = []
    for request, index, caption, mapped, unbound, earlier, kind in reviewed_table_rows(document, binding):
        context, roles, disconnected, jorc, effective, cutoff, material, members, context_issues = _native_context(
            pages, binding, caption,
        )
        category = _category_from_original(request["label"], earlier, caption)
        issues = list(context_issues)
        vintage_gap = _vintage_gap(mapped, binding["table"]["columns"], effective)
        if vintage_gap:
            issues.append(vintage_gap)
        aliases = {cell.variable for cell, _, _ in mapped}
        if aliases != {entry["variable"] for entry in binding["table"]["columns"]}:
            issues.append("REVIEWED_NUMERIC_COLUMN_ALIGNMENT_UNBOUND")
        if category != request["category"]:
            issues.append("JORC_CLASSIFICATION_EVIDENCE_UNBOUND")
        if kind != "MINERAL_RESOURCE":
            issues.append("MINERAL_RESOURCE_SCOPE_REQUIRED")
        if not binding.get("scope_name"):
            issues.append("SITE_RESOURCE_SCOPE_UNBOUND")
        row_anchor = document.locate(binding["page"], line=index)
        cells = []
        for cell, header, unit in mapped:
            evidence = cell.to_dict()
            interval, significant_figures = _publisher_precision(
                cell.value.text, cell.raw_rounding_interval, roles.get("PRECISION", ()),
            )
            evidence.update(header_anchor=header.to_dict(), unit_anchor=unit.to_dict(), row_anchor=row_anchor.locator,
                            raw_rounding_interval=interval, reported_significant_figures=significant_figures,
                            unbound_native_numeric_tokens=[item.value.to_dict() for item in unbound],
                            contextual_holds=issues, context_links_not_established=disconnected)
            issue = issues[0] if issues else None
            chemistry = cell.chemical_basis or ("TREO" if members and material == "WHOLE_ORE" else None)
            cells.append(ResourceCell(
                cell.variable, cell.value.text, cell.unit, cell.value.physical_page, binding["table"]["caption"],
                request["label"], header.text, row_anchor.text,
                conflict=vintage_gap or ("MATERIAL_BASIS_NOT_WHOLE_ORE"
                                          if material == "HEAVY_MINERAL_OR_CONCENTRATE" else None),
                chemical_basis=chemistry, evidence_issue=issue, evidence_context={"native_table_binding": evidence},
                material_basis=material, denominator_basis=material if cell.variable == "grade" or cell.variable.endswith("_ppm") else None,
            ))
        bundle = {item.variable: item for item in cells}
        if all(variable in bundle for variable in VARIABLES):
            try:
                _check_estimate(tuple(bundle[variable] for variable in VARIABLES), native_precision=True)
            except DocumentError as error:
                cells = [replace(cell, conflict=str(error)) for cell in cells]
        results.append(ResourceStatement(
            binding.get("scope_name") or "Unbound resource scope", cutoff.get("value"), effective,
            request.get("primary") is True and kind == "MINERAL_RESOURCE" and category == request["category"],
            tuple(cells), context,
            category or "UNKNOWN_CLASSIFICATION", cutoff.get("unit"), cutoff.get("analyte"),
            binding.get("reported_chemical_label"), members, "JORC 2012" if jorc else None, kind,
        ))
    return tuple(results)


STRATEGIES = {
    "native-resource-table-v1": native_resource_table,
    "narrative-and-cited-table-v1": narrative_table,
    "classified-section-totals-v1": classified_sections,
    "cutoff-series-and-detail-tables-v1": cutoff_series,
    "positioned-mineral-resource-total-v1": positioned_total,
    "corroborated-ppm-narrative-total-v1": narrative_ppm_total,
}

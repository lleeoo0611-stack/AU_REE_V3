"""Native PDF evidence binding for reviewed whole-ore TREO resource disclosures."""

from datetime import date
from decimal import Decimal, localcontext
import hashlib
import re
from dataclasses import dataclass

from .contracts import canonical_json
from .documents import DocumentError

NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
VARIABLES = {
    "ore_mt": ("Mt", "million tonnes"),
    "grade": ("%", "%"),
    "contained_treo_kt": ("kt TREO", "‘000 tonnes"),
}
CATEGORY = "MEASURED_INDICATED_INFERRED_TOTAL"


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResourceEvidence:
    tokens: tuple[str, str, str]
    table_tokens: tuple[str, str, str]
    quotes: tuple[str, str, str]
    title: str
    table: str
    definition: str
    date_quote: str
    cutoff_grade: str


def read_resource(pages: tuple[str, ...], binding: dict) -> ResourceEvidence:
    """Require a unique named narrative and tied total table on the same page."""
    deposit = binding["deposit_name"]
    if binding["scope_name"] != f"{deposit} Mineral Resource":
        raise DocumentError("SCIENTIFIC_CONFLICT", "SCOPE_BINDING_MISMATCH")
    name = re.escape(deposit)
    page = pages[binding["page"] - 1]
    lines = [line.strip() for line in page.splitlines()]
    text = " ".join(page.split())
    try:
        public = date.fromisoformat(binding["source"]["publication_date"])
        period = date.fromisoformat(binding["effective_date"])
    except (TypeError, ValueError) as error:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_BINDING_REQUIRES_DAY_PRECISION") from error
    public_text = f"{public.day} {public:%B %Y}"
    effective_text = f"{period.day} {period:%B %Y}"
    release_lines = pages[0].strip().splitlines()
    if not release_lines or release_lines[0].strip() != public_text or effective_text not in pages[0]:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "SOURCE_DATE_EVIDENCE_MISSING")
    if (binding["material_basis"], binding["chemical_basis"], binding["category"]) != (
        "WHOLE_ORE", "TREO", CATEGORY
    ):
        raise DocumentError("SCIENTIFIC_CONFLICT", "BASIS_MISMATCH")
    narrative = list(re.finditer(
        rf"The {name} Rare Earth Mineral Resources estimate is now ({NUMBER}) million tonnes "
        rf"at an average grade of ({NUMBER})% Total Rare Earth Oxide \(TREO\) "
        rf"for a total of ({NUMBER}) million tonnes of contained TREO\. "
        rf"A Mineral Resource cut-off grade of ({NUMBER})% TREO was applied\. "
        rf"The resource has been estimated in accordance with JORC 2012 guidelines "
        rf"and is summarised in (Table [0-9]+)\.", text,
    ))
    if len(narrative) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "STATEMENT_EVIDENCE_MISSING")
    statement = narrative[0]
    table = statement[5]
    title_pattern = (
        rf"{re.escape(table)}: {name} Rare Earth Deposit Mineral Resource summary "
        rf"{period:%B %Y} at {re.escape(statement[4])}% TREO cut-off grade"
    )
    index = [i for i, line in enumerate(lines) if re.fullmatch(title_pattern, line)]
    if len(index) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "STATEMENT_EVIDENCE_MISSING")
    table_lines = [line for line in lines[index[0] + 1:] if line][:5]
    if len(table_lines) != 5 or not re.fullmatch(
        r"JORC Classification\s+Million Tonnes\s+TREO\* \(%\)\s+TREO\* \([‘’']000 tonnes\)", table_lines[0]
    ):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "TABLE_SCHEMA_MISMATCH")
    row_pattern = rf"\s+{NUMBER}\s+{NUMBER}\s+{NUMBER}"
    for category, line in zip(("Measured", "Indicated", "Inferred"), table_lines[1:4]):
        if not re.fullmatch(category + row_pattern, line):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "RESOURCE_CATEGORY_MISMATCH")
    total = re.fullmatch(rf"Total\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})", table_lines[4])
    definition = re.search(r"\*TREO = total Rare Earth Oxides \(.*?\) \+ Yttrium \(Y2O3\)\.", text)
    if not total or not definition:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "STATEMENT_EVIDENCE_MISSING")
    _verify_totals(statement, total)
    return ResourceEvidence(
        (statement[1], statement[2], total[3]), (total[1], total[2], total[3]),
        (statement[0], statement[0], table_lines[4]), lines[index[0]], table, definition[0], effective_text, statement[4],
    )


def exact_value(token: str) -> Decimal:
    if not re.fullmatch(NUMBER, token):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REPORTED_DECIMAL_REQUIRED")
    return Decimal(token.replace(",", ""))


def half_width(value: Decimal) -> Decimal:
    return Decimal((0, (5,), value.as_tuple().exponent - 1))


def _verify_totals(statement: re.Match, total: re.Match) -> None:
    ore, grade, narrative_mass = (exact_value(statement[i]) for i in (1, 2, 3))
    table_ore, table_grade, contained = (exact_value(total[i]) for i in (1, 2, 3))
    with localcontext() as context:
        context.prec = sum(len(x.as_tuple().digits) for x in (ore, grade, narrative_mass, table_ore, table_grade, contained)) + 12
        expected_low = (ore - half_width(ore)) * (grade - half_width(grade)) * 10
        expected_high = (ore + half_width(ore)) * (grade + half_width(grade)) * 10
        if (
            ore != table_ore or not 0 < grade <= 100
            or abs(grade - table_grade) > half_width(grade) + half_width(table_grade)
            or abs(narrative_mass * 1000 - contained) > half_width(narrative_mass) * 1000 + half_width(contained)
            or contained + half_width(contained) < expected_low
            or contained - half_width(contained) > expected_high
        ):
            raise DocumentError("SCIENTIFIC_CONFLICT", "TABLE_NARRATIVE_CONFLICT")


def legacy_claim(binding: dict, evidence: ResourceEvidence, variable: str, token: str) -> dict:
    """Rebuild the V2 authored declaration from PDF tokens for the legacy ID."""
    value = exact_value(token)
    # V2's declaration used JSON integers for contained tonnes and binary floats
    # for ore and grade. This conversion is for legacy identity only.
    number = int(value) if variable == "contained_treo_kt" else float(value)
    return {
        "variable": variable,
        "value": number,
        "unit": VARIABLES[variable][0],
        "basis": "TREO",
        "scope": binding["scope_name"],
        "statement_id": binding["statement_id"],
        "category": binding["category"],
        "effective_date": binding["effective_date"],
        "source_key": binding["source"]["source_key"],
        "source_sha256": binding["source"]["sha256"],
        "locator": binding["locator"],
        "pdf_page": binding["page"],
        "page_markers": list(evidence.tokens),
        "extraction_method": "EXPLICIT_TRANSCRIPTION_CHECKED_AGAINST_RENDERED_PAGE_NOT_INDEPENDENT_APPROVAL",
        "rounding_half_width": float(half_width(value)) if variable != "contained_treo_kt" else None,
    }

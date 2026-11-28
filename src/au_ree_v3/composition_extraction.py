"""Bind oxide component cells to the total row of a reviewed JORC statement."""

from datetime import date
from decimal import Decimal
import re

from .documents import DocumentError
from .normalization import reported_interval
from .observations import NUMBER, OXIDE_VARIABLES, exact_value, half_width
from .resource_extraction import ResourceCell, ResourceStatement

_COLUMNS = (
    "La2O3", "CeO2", "Pr6O11", "Nd2O3", "Sm2O3", "Eu2O3", "Gd2O3", "Tb4O7",
    "Dy2O3", "Ho2O3", "Er2O3", "Tm2O3", "Yb2O3", "Lu2O3", "Y2O3",
)
_NEW_SPECIES = set(_COLUMNS) - OXIDE_VARIABLES.keys() - {"Y2O3"}


def _published_closure(values: dict[str, str], grade: ResourceCell) -> dict:
    """Check the printed table as a whole, without deriving an accepted aggregate."""
    intervals = [reported_interval(value) for value in values.values()]
    ppm_sum = tuple(sum(values) for values in zip(*intervals, strict=True))
    treo = tuple(amount * 10_000 for amount in reported_interval(grade.token))
    if max(ppm_sum[1], treo[1]) > min(ppm_sum[2], treo[2]):
        raise DocumentError("SCIENTIFIC_CONFLICT", "PRINTED_OXIDE_SUM_OUTSIDE_TREO_PRECISION")
    return {"printed_species_count": len(intervals), "printed_sum_ppm": str(ppm_sum[0]),
            "printed_sum_interval_ppm": [str(bound) for bound in ppm_sum[1:]],
            "reported_treo_interval_ppm": [str(bound) for bound in treo[1:]],
            "use": "TABLE_CHECK_ONLY"}


def _yttria_conflict(grade: ResourceCell, token: str) -> dict:
    # classified_sections bound this exact aggregate row and its kg/t headers.
    companion = re.findall(NUMBER, grade.quote)[3]
    thin = tuple(amount * 1000 for amount in reported_interval(companion))
    printed = reported_interval(token)
    if max(thin[1], printed[1]) <= min(thin[2], printed[2]):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_YTTRIA_PRECISION_CONFLICT_NOT_REPRODUCED")
    return {"physical_page": grade.page, "table": grade.table, "row": grade.row, "column": "Y2O3",
            "row_quote": " ".join(grade.quote.split()), "token": companion, "unit": "kg/t",
            "companion_interval_ppm": [str(bound) for bound in thin[1:]],
            "printed_interval_ppm": [str(bound) for bound in printed[1:]]}


def _reviewed_oxides(review, source_sha256, values, statement, table_context):
    if review is None or source_sha256 is None or review["source_sha256"] != source_sha256:
        return ()
    species = review["species"]
    if sorted(species) != sorted(_NEW_SPECIES):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "UNAPPROVED_COMPANION_OXIDE_MEMBERS")
    grade = next(cell for cell in statement.measurements if cell.variable == "grade")
    definition = next((quote for _, quote in statement.context if quote.startswith("TREO = Total Rare Earth Oxides")), "")
    members = re.findall(r"[A-Z][a-z]?\d*O\d+", definition)
    if grade.raw_unit != "%" or sorted(members) != sorted(_COLUMNS):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "COMPANION_OXIDE_DENOMINATOR_UNBOUND")
    evidence = {"source_sha256": source_sha256, **table_context, "material_basis": "WHOLE_ORE",
                "denominator_reported": {"physical_page": grade.page, "locator": grade.table, "quote": definition},
                "nominal_precision": _published_closure(values, grade)}
    y_conflict = _yttria_conflict(grade, values["Y2O3"])
    return tuple(ResourceCell(
        f"{oxide.lower()}_ppm", values[oxide], "ppm", table_context["physical_page"],
        table_context["table"], "Total", oxide, table_context["row_quote"],
        chemical_basis=oxide, evidence_context={
            "reviewed_oxide_table": {**evidence, "token_interval_ppm": [
                str(value) for value in reported_interval(values[oxide])[1:]
            ]},
            **({"same_statement_precision_conflict": y_conflict} if oxide == "Y2O3" else {}),
        },
        conflict="COMPOSITION_SOURCE_PRECISION_CONFLICT" if oxide == "Y2O3" else None,
    ) for oxide in (*species, "Y2O3"))


def classified_total_oxides(
    pages: tuple[str, ...], binding: dict, statement: ResourceStatement, *, source_sha256: str | None = None,
) -> tuple[ResourceCell, ...]:
    """Extract the approved whole-ore oxide columns of a same-statement companion table."""
    table = binding.get("composition_table")
    if table is None or not statement.primary:
        return ()
    page_number, number = table["page"], table["number"]
    if type(page_number) is not int or not 1 <= page_number <= len(pages):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "OXIDE_TABLE_PAGE_UNAVAILABLE")
    if not statement.effective_date or binding["section"] != "Total":
        raise DocumentError("EVIDENCE_INSUFFICIENT", "OXIDE_TOTAL_SCOPE_UNBOUND")

    period = date.fromisoformat(statement.effective_date)
    effective = f"{period.day} {period:%B %Y}"
    name = re.escape(binding["deposit_name"])
    page = pages[page_number - 1]
    parts = re.split(r"(?m)^\s*(Table [0-9]+):\s*", page)
    windows = [text for label, text in zip(parts[1::2], parts[2::2], strict=True)
               if label == f"Table {number}"]
    if len(windows) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "OXIDE_COMPANION_TABLE_UNBOUND")

    window = re.split(r"(?mi)^\s*Notes:", windows[0], maxsplit=1)[0]
    heading = " ".join(window.split())
    title = re.match(
        rf"{name} Total Mineral Resource estimate at {effective}\s*reported above a "
        rf"({NUMBER})\s*% TREO cut-off grade [–-] all TREO variables\.",
        heading, flags=re.I,
    )
    if not title or exact_value(title[1]) != exact_value(statement.cutoff):
        raise DocumentError("SCIENTIFIC_CONFLICT", "OXIDE_SCOPE_DATE_OR_CUTOFF_MISMATCH")

    columns = r"[ \t]+".join(map(re.escape, _COLUMNS))
    header = re.search(
        rf"(?mi)^\s*(?:Tonnes|Tonnage)[ \t]+{columns}[ \t]*$",
        window,
    )
    units = None if header is None else re.match(
        rf"\s*Class\s*\(kt\)(?:[ \t]+ppm){{{len(_COLUMNS)}}}[ \t]*(?:\n|$)",
        window[header.end():], flags=re.I,
    )
    if units is None:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "OXIDE_PPM_COLUMNS_UNBOUND")

    for category in ("Meas", "Ind", "Inf"):
        if not re.search(rf"(?mi)^\s*{category}[ \t]+{NUMBER}\b", window):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "OXIDE_RESOURCE_CLASSIFICATIONS_UNBOUND")
    rows = re.findall(r"(?mi)^([ \t]*Total[ \t]+[^\n]+)$", window)
    if len(rows) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "OXIDE_TOTAL_ROW_UNBOUND")
    values = rows[0].split()[1:]
    if len(values) != len(_COLUMNS) + 1 or not all(re.fullmatch(NUMBER, value) for value in values):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "OXIDE_TOTAL_HAS_UNREPORTED_COMPONENT")
    total_kt = exact_value(values[0])
    tonnage = next(cell for cell in statement.measurements if cell.variable == "ore_mt")
    ore_mt = Decimal(tonnage.value)
    if abs(total_kt - ore_mt * 1000) > half_width(total_kt) + half_width(ore_mt) * 1000:
        raise DocumentError("SCIENTIFIC_CONFLICT", "OXIDE_COMPANION_RESOURCE_TONNAGE_MISMATCH")

    by_species = dict(zip(_COLUMNS, values[1:], strict=True))
    legacy = tuple(
        ResourceCell(variable, by_species[species], "ppm", page_number, f"Table {number}",
                     "Total", species, rows[0].strip(), chemical_basis=species)
        for species, variable in OXIDE_VARIABLES.items()
    )
    return legacy + _reviewed_oxides(
        table.get("reviewed_nonmagnetic_species"), source_sha256, by_species, statement,
        {
            "physical_page": page_number, "table": f"Table {number}", "caption": title[0], "row_quote": rows[0].strip(),
            "header": " ".join(header[0].split()), "units": " ".join(units[0].split()),
        },
    )

"""Bind oxide component cells to the total row of a reviewed JORC statement."""

from datetime import date
from decimal import Decimal
import re

from .comparability import OXIDE_VARIABLES
from .documents import DocumentError
from .observations import NUMBER, exact_value, half_width
from .resource_extraction import ResourceCell, ResourceStatement

_COLUMNS = (
    "La2O3", "CeO2", "Pr6O11", "Nd2O3", "Sm2O3", "Eu2O3", "Gd2O3", "Tb4O7",
    "Dy2O3", "Ho2O3", "Er2O3", "Tm2O3", "Yb2O3", "Lu2O3", "Y2O3",
)


def classified_total_oxides(
    pages: tuple[str, ...], binding: dict, statement: ResourceStatement,
) -> tuple[ResourceCell, ...]:
    """Extract four whole-ore oxide values from the statement's companion table."""
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
    if header is None or not re.match(
        rf"\s*Class\s*\(kt\)(?:[ \t]+ppm){{{len(_COLUMNS)}}}[ \t]*(?:\n|$)",
        window[header.end():], flags=re.I,
    ):
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
    return tuple(
        ResourceCell(variable, by_species[species], "ppm", page_number, f"Table {number}",
                     "Total", species, rows[0].strip(), chemical_basis=species)
        for species, variable in OXIDE_VARIABLES.items()
    )

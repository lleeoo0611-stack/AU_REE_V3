"""Corroborate a reported oxide aggregate with the reviewed original image table."""

from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from .comparability import TOTAL_REO_15
from .documents import DocumentError
from .normalization import reported_interval
from .observations import NUMBER, exact_value, half_width
from .resource_extraction import ResourceCell, ResourceStatement

SPECIES = ("Nd2O3", "Pr6O11", "Dy2O3", "Tb4O7")


def _verify_images(content: bytes, page: int, expected: dict[int, str]) -> str:
    """Read back the native image that bounds the independently reviewed cells."""
    with TemporaryDirectory() as directory:
        root = Path(directory)
        original = root / "source.pdf"
        original.write_bytes(content)
        try:
            subprocess.run(
                ["pdfimages", "-f", str(page), "-l", str(page), str(original), str(root / "image")],
                capture_output=True, check=True, timeout=30,
            )
            images = {slot: sha256((root / f"image-{slot:03d}.ppm").read_bytes()).hexdigest()
                      for slot in expected}
            runtime = subprocess.run(
                ["pdfimages", "-v"], capture_output=True, text=True, check=True, timeout=5,
            ).stderr.splitlines()[0]
        except (OSError, IndexError, subprocess.SubprocessError) as error:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COMPOSITION_IMAGE_UNAVAILABLE") from error
    if images != expected:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COMPOSITION_IMAGE_FINGERPRINT_MISMATCH")
    return runtime


def _verify_image(content: bytes, page: int, slot: int, expected: str) -> str:
    return _verify_images(content, page, {slot: expected})


def verify_treo_image_context(pages, statement, review, content):
    """Verify reviewed native image columns and their explicit same-MRE text link."""
    page = review["physical_page"]
    normalized = lambda text: " ".join(text.split())
    text = normalized(pages[page - 1])
    declaration = normalized(review["statement_quote"])
    definition = normalized(review["definition_quote"])
    if not declaration or not definition or text.count(declaration) != 1 or text.count(definition) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_IMAGE_STATEMENT_OR_DEFINITION_UNBOUND")
    expected = (statement.scope, statement.effective_date, [statement.cutoff, statement.cutoff_unit, statement.cutoff_analyte])
    if (review["scope"], review["effective_date"], review["cutoff"]) != expected:
        raise DocumentError("SCIENTIFIC_CONFLICT", "REVIEWED_IMAGE_RESOURCE_SCOPE_MISMATCH")
    effective = statement.effective_date
    if not effective or len(effective) not in {7, 10}:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_IMAGE_ESTIMATE_MONTH_UNBOUND")
    period = date.fromisoformat(effective if len(effective) == 10 else effective + "-01")
    cutoff_token = re.escape(statement.cutoff or "")
    unit = re.escape(statement.cutoff_unit)
    cutoff = rf"(?<![0-9A-Za-z.,]){cutoff_token}\s*{unit}(?![A-Za-z0-9.])"
    if (f"{period:%B %Y}" not in declaration or not re.search(cutoff, declaration, flags=re.I)
            or not re.search(r"\b(?:MRE|Mineral Resource Estimate)\b", declaration, flags=re.I)
            or not re.search(r"\bTREO\b", definition, flags=re.I)
            or not re.search(r"\bsum(?:med|ming)?\b", definition, flags=re.I)
            or not re.search(r"\bREO\b|\brare earth oxides?\b", definition, flags=re.I)):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_IMAGE_CUTOFF_OR_CHEMICAL_RELATION_UNBOUND")
    images = review["images"]
    slots = {}
    members = []
    intervals = []
    for item in images:
        slot, digest = item["slot"], item["sha256"]
        if type(slot) is not int or slot < 0 or slot in slots or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_IMAGE_IDENTITY_UNBOUND")
        slots[slot] = digest
        species, tokens = item["members"], item["total_tokens"]
        if item["unit"] != "ppm" or item["row"] != "TOTAL" or len(species) != len(tokens):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_IMAGE_TOTAL_UNIT_OR_MEMBER_TOKEN_UNBOUND")
        members.extend(species)
        try:
            intervals.extend(reported_interval(tok) for tok in tokens)
        except (ValueError, ArithmeticError) as error:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_IMAGE_MEMBER_TOKEN_UNBOUND") from error
    if sorted(members) != sorted(TOTAL_REO_15) or (
        statement.treo_members and sorted(statement.treo_members) != sorted(members)
    ):
        raise DocumentError("SCIENTIFIC_CONFLICT", "REVIEWED_IMAGE_TREO_MEMBER_SET_MISMATCH")
    grades = [item for item in statement.measurements if item.variable == "grade"]
    if len(grades) != 1 or grades[0].raw_unit != "ppm":
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_IMAGE_GRADE_UNIT_UNBOUND")
    grade, = grades
    grade_point, grade_low, grade_high = reported_interval(grade.token)
    runtime = _verify_images(content, page, slots)
    with localcontext() as context:
        context.prec = sum(len(entry[0].as_tuple().digits) for entry in intervals) + 12
        species_total, species_low, species_high = map(sum, zip(*intervals, strict=True))
    if species_low > grade_high or species_high < grade_low:
        raise DocumentError("SCIENTIFIC_CONFLICT", "REVIEWED_IMAGE_TREO_SUM_OUTSIDE_PRINTED_PRECISION")
    return tuple(sorted(members)), {
        **review, "decoder_runtime": runtime,
        "rounding_check": {
            "method": "sum-member-display-intervals-v1", "unit": "ppm",
            "reported": [str(value) for value in (grade_point, grade_low, grade_high)],
            "species_sum": [str(value) for value in (species_total, species_low, species_high)],
            "outcome": "OVERLAP",
        },
    }


def _consistent_display(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= half_width(left) + half_width(right)


def corroborated_aggregate(
    pages: tuple[str, ...], binding: dict, statement: ResourceStatement, content: bytes, source_sha: str,
) -> tuple[ResourceCell, ...]:
    """Keep the source summary token, using a reviewed same-scope image for units."""
    review = binding.get("oxide_aggregate_review")
    if review is None or not statement.primary:
        return ()
    if review["source_sha256"] != source_sha:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COMPOSITION_EDITION_MISMATCH")
    if review["group_unit"] != "ppm" or review["grade_unit"] != "ppm":
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COMPOSITION_UNIT_UNBOUND")
    if (review["scope"] != statement.scope or review["effective_date"] != statement.effective_date
            or review["cutoff"] != [statement.cutoff, statement.cutoff_unit, statement.cutoff_analyte]):
        raise DocumentError("SCIENTIFIC_CONFLICT", "REVIEWED_COMPOSITION_SCOPE_MISMATCH")
    page = binding["page"]
    native = pages[page - 1]
    definition = list(re.finditer(r"MREO\s*=\s*Magnet Rare Earth Oxides?\s*\(([^)]+)\)", native, re.I))
    if len(definition) != 1 or {part.strip() for part in definition[0][1].split(",")} != set(SPECIES):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "FOUR_OXIDE_DEFINITION_UNBOUND")
    heading = binding["table"]["caption"]
    if native.count(heading) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "COMPOSITION_RESOURCE_TABLE_UNBOUND")
    window = native.split(heading, 1)[1].split("Notes on Mineral Resource Estimation", 1)[0]
    if not re.search(r"\bTREO\s+NdPr\s+DyTb\s+MREO\b", " ".join(window.split())):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "COMPOSITION_SUMMARY_COLUMNS_UNBOUND")
    totals = list(re.finditer(rf"(?m)^[ \t]*Total[ \t]+({NUMBER})(?:[ \t]+({NUMBER})){{4}}[ \t]*$", window))
    if len(totals) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "COMPOSITION_SUMMARY_TOTAL_UNBOUND")
    total = totals[0][0].strip()
    ore_token, grade_token, _, _, aggregate_token = re.findall(NUMBER, total)
    grade = next(cell for cell in statement.measurements if cell.variable == "grade")
    ore = next(cell for cell in statement.measurements if cell.variable == "ore_mt")
    if ((ore_token, grade_token) != (ore.token, grade.token) or grade.normalized_unit != "ppm"):
        raise DocumentError("SCIENTIFIC_CONFLICT", "COMPOSITION_SUMMARY_MEASUREMENTS_MISMATCH")

    reviewed_ore = exact_value(review["ore_token_mt"])
    reviewed_grade = exact_value(review["grade_token_ppm"])
    left = exact_value(review["nd_pr_token_ppm"])
    right = exact_value(review["dy_tb_token_ppm"])
    aggregate = exact_value(aggregate_token)
    with localcontext() as arithmetic:
        arithmetic.prec = sum(len(value.as_tuple().digits) for value in (left, right, aggregate, reviewed_ore, reviewed_grade)) + 12
        combined = left + right
        overlap = abs(combined - aggregate) <= half_width(left) + half_width(right) + half_width(aggregate)
    if (not _consistent_display(exact_value(ore_token), reviewed_ore)
            or not _consistent_display(exact_value(grade_token), reviewed_grade)
            or not overlap or not 0 < aggregate <= exact_value(grade_token)):
        raise DocumentError("SCIENTIFIC_CONFLICT", "REVIEWED_COMPOSITION_VALUES_DO_NOT_CORROBORATE")
    runtime = _verify_image(content, review["physical_page"], review["image_slot"], review["image_sha256"])
    return (ResourceCell(
        "mreo_ppm", aggregate_token, "ppm", page, heading, "Total", "MREO", total,
        chemical_basis="PrNdDyTb_REO",
        evidence_context={
            "oxide_species": list(SPECIES),
            "oxide_definition": {"page": page, "quote": definition[0][0]},
            "reviewed_companion_image": {**review, "decoder_runtime": runtime},
        },
    ),)

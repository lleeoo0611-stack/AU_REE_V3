"""Corroborate a reported oxide aggregate with the reviewed original image table."""

from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from .documents import DocumentError
from .observations import NUMBER, exact_value, half_width
from .resource_extraction import ResourceCell, ResourceStatement

SPECIES = ("Nd2O3", "Pr6O11", "Dy2O3", "Tb4O7")


def _verify_image(content: bytes, page: int, slot: int, expected: str) -> str:
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
            image = (root / f"image-{slot:03d}.ppm").read_bytes()
            runtime = subprocess.run(
                ["pdfimages", "-v"], capture_output=True, text=True, check=True, timeout=5,
            ).stderr.splitlines()[0]
        except (OSError, IndexError, subprocess.SubprocessError) as error:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COMPOSITION_IMAGE_UNAVAILABLE") from error
    if sha256(image).hexdigest() != expected:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_COMPOSITION_IMAGE_FINGERPRINT_MISMATCH")
    return runtime


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

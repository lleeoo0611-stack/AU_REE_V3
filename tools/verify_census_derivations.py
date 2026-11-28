"""Check Census derivation arithmetic by independent transcription of two original PDFs."""

from argparse import ArgumentParser
from collections import defaultdict
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from au_ree_v3.comparability import shadow_contained_mass
from au_ree_v3.contracts import ContractViolation, ResearchRelease, canonical_json
from au_ree_v3.derivations import (
    FORMULA_REGISTRY_SHA256, assess_census_treo, checked_census_derivations, persist_census_treo,
    verify_derivation_assessment,
)
from au_ree_v3.research_inputs import CENSUS_RESOURCE_RULE, checked_research_inputs
from au_ree_v3.store import Store


# Reviewed constants from originally printed values and publisher precision.
# Every tuple lists point, minimum and maximum. The units here are Mt and ppm.
ORACLE = {
    "AU-dac3c8e40326b8244cdb": {
        "sha": "bc93c9d96bccf1d5badff0508915633117c723f7858ccddbfedb8a3be938ef7e",
        "row_page": 4, "row": "Total 1,000 ppm 1.8 Bt 1,700* 1.7 Bt 1,700",
        "precision_page": 19,
        "precision": "Grades and tonnages are rounded to 2 significant figures in accordance with the JORC Code (2012 Edition)",
        "input_tokens": ("1.7", "1,700"), "ore": ("1700", "1650", "1750"), "grade": ("1700", "1650", "1750"),
        "raw_units": ("Bt", "ppm"), "cutoff": ("1000", "ppm", "TREO"), "effective_date": "2026-09",
    },
    "AU-216d0983f6892f9b58f9": {
        "sha": "452dabd702e88c0841c2d029af2127e215e52c2ae9d1144ebe6731f880cde491",
        "row_page": 97, "row": "Total 278.80 482.64 171.40 311.24 7.04",
        "precision_page": 97,
        "precision": "Numbers have been rounded to reflect the appropriate precision of the estimate and may not sum due to rounding.",
        "input_tokens": ("278.80", "482.64"), "ore": ("278.8", "278.795", "278.805"),
        "grade": ("482.64", "482.635", "482.645"), "raw_units": ("Mt", "ppm"),
        "cutoff": ("300", "ppm", "TREO"), "effective_date": "2026-08",
    },
}
NOLANS = "AU-1345edd8165c10930e94"
NOLANS_ORACLE = {
    "sha": "5f72a08840640987e8d619c6d55ca047a2fe26c0194ffd1a46521f76eda7b06f",
    "row_page": 2, "row": "TOTAL 56 2.6 11 26.4",
    "precision_page": 27, "precision": "All tonnages are estimated on a dry basis.",
    "other_quotes": ((2, "Figure 1: Mineral Resources for the Nolans Bore deposit as at 07 June 2017 using a 1% TREO Cut-Off Grade."),
                     (4, "JORC TABLE 1 ASSESSMENT CRITERIA FOR NOLANS BORE MINERAL RESOURCE STATEMENT AS AT 7 JUNE 2017")),
    "input_tokens": ("56", "2.6"), "ore": ("56", "55.5", "56.5"), "grade": ("26000", "25500", "26500"),
    "raw_units": ("Mt", "%"), "cutoff": ("1", "%", "TREO"), "effective_date": "2017-06-07",
}
_HOLD_REASONS = {
    "mreo_kt": "NO_APPROVED_CENSUS_MREO_MASS_FORMULA",
    "Nd2O3_kt": "NO_APPROVED_CENSUS_INDIVIDUAL_OXIDE_MASS_FORMULA",
    "Pr6O11_kt": "NO_APPROVED_CENSUS_INDIVIDUAL_OXIDE_MASS_FORMULA",
    "Tb4O7_kt": "NO_APPROVED_CENSUS_INDIVIDUAL_OXIDE_MASS_FORMULA",
    "ndpr_kt": "NO_APPROVED_CENSUS_COMBINED_NDPR_MASS_FORMULA",
}


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _page(path: Path, page: int) -> str:
    text = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(path), "-"],
                          capture_output=True, text=True, check=True, timeout=30).stdout
    return " ".join(text.split())


def _source_oracle(assets: Path, specifications: dict) -> dict:
    observations = {}
    for project, spec in sorted(specifications.items()):
        sha = spec["sha"]
        asset = next(path for path in (assets / "objects" / sha, assets / sha) if path.is_file())
        if _hash(asset) != sha:
            raise ValueError(f"original SHA mismatch for {project}")
        quotes = ((spec["row_page"], spec["row"]), (spec["precision_page"], spec["precision"]), *spec.get("other_quotes", ()))
        for page, quote in quotes:
            if _page(asset, page).count(quote) != 1:
                raise ValueError(f"source quote is unavailable for {project}, PDF page {page}")
        fractions = (Fraction(ore) * Fraction(grade) / 1000 for ore, grade in zip(spec["ore"], spec["grade"]))
        point, lower, upper = (format(Decimal(item.numerator) / Decimal(item.denominator), "f") for item in fractions)
        observations[project] = {
            "value": point, "interval": [lower, upper], "source_sha256": sha,
            "evidence": [{"physical_page": page, "quote": quote} for page, quote in quotes],
            "ore_mt": list(spec["ore"]), "grade_ppm": list(spec["grade"]), "method": "independent-rational-arithmetic-v1",
        }
    return observations


def _store_path(build: Path, directory: Path, label: str) -> Path:
    path = directory / f"{label}.sqlite"
    shutil.copy2(build / "observations/research.sqlite", path)
    return path


def _derived(build: Path, directory: Path, expected: dict, specifications: dict, *, baseline: bool) -> dict:
    store_path = _store_path(build, directory, "baseline" if baseline else "candidate")
    extraction = json.loads((build / "observations/extraction.json").read_bytes())
    with Store(store_path, trusted_temporal_evidence_ids=extraction["trusted_temporal_evidence_ids"]) as ledger:
        release = ledger.get(ResearchRelease, extraction["release_id"])
        if release is None:
            raise ValueError("build does not contain the exact observation release")
        rows = {row.project_id: row for row in assess_census_treo(ledger, release) if row.status == "APPROVED"}
        if set(rows) != set(expected):
            raise ValueError("derived review projects differ from the independent original evidence")
        for name, row in rows.items():
            reference = expected[name]
            spec = specifications[name]
            if (not verify_derivation_assessment(row.to_dict()) or row.source_sha256 != reference["source_sha256"]
                    or row.value != reference["value"] or list(row.rounding_interval) != reference["interval"]
                    or tuple(record["raw_token"] for record in row.inputs) != spec["input_tokens"]
                    or tuple(record["raw_unit"] for record in row.inputs) != spec["raw_units"]
                    or row.effective_date != spec["effective_date"] or row.cutoff != spec["cutoff"]):
                raise ValueError(f"derivation differs from the source transcription for {name}")
        official = checked_census_derivations(ledger, release)
        if baseline:
            return _check_shadow_phase(ledger, release, official, expected)
        return _check_candidate_phase(official, rows, build, expanded=NOLANS in specifications)


def _check_shadow_phase(ledger, release, official, expected) -> dict:
    if official:
        raise ValueError("baseline contains formal derivations")
    grouped = defaultdict(dict)
    for item in checked_research_inputs(ledger, release, purpose="CENSUS"):
        if item.accepted.reason == CENSUS_RESOURCE_RULE:
            grouped[item.raw.project_id][item.raw.variable] = item
    checks = []
    for project, pair in sorted(grouped.items()):
        shadow = shadow_contained_mass(pair["ore_mt"], pair["grade"])
        if (shadow.status != "COMPARABLE_DERIVED" or Decimal(shadow.value) != Decimal(expected[project]["value"])
                or list(map(Decimal, shadow.derivation["rounding_interval_kt"])) != list(map(Decimal, expected[project]["interval"]))):
            raise ValueError(f"legacy shadow and independent Oracle disagree for {project}")
        checks.append({"project_id": project, "shadow_id": shadow.shadow_id, "outcome": shadow.status})
    try:
        with ledger.transaction():
            persist_census_treo(ledger, release)
    except ContractViolation as error:
        if "Formula Registry pin" not in str(error):
            raise
    else:
        raise ValueError("an earlier unpinned release accepted a new formal calculation")
    return {"status": "PASS", "release_id": release.release_id, "shadow_checks": checks,
            "formal_derivation_count": 0, "write_rejection": "UNPINNED_BASELINE_RELEASE"}


def _check_candidate_phase(official, rows, build, *, expanded) -> dict:
    if {item.derived_id for item in official} != {row.assessment_id for row in rows.values()}:
        raise ValueError("candidate does not persist the exact expected derived values")
    record = json.loads((build / "census/census_receipt.json").read_bytes())
    if expanded:
        from au_ree_v3.research_delta import ResearchBuild

        report = ResearchBuild.load(build)
        r1 = report.decision_inputs["criteria_by_project"][NOLANS]["R1"]
        nolans = report.census[NOLANS, "總TREO蘊藏量_kt"]
        if (r1["reason"] != "INCOMPLETE_SELECTED_RESOURCE_BUNDLE" or r1["accepted_value"] is not None
                or nolans["value_origin"] != "FORMAL_DERIVED" or Decimal(nolans["value"]) != Decimal(rows[NOLANS].value)):
            raise ValueError("Nolans' governed Census estimate must not supply a formal R1 value")
        return {"status": "PASS", "formal_treo_derivation_count": len(official), "nolans_r1": r1["reason"],
                "derived_values": [value.to_dict() for value in official]}
    shadows = record["science_views"]["derived_candidates"]
    remaining = [{"project_id": row["project_id"], "formula": row["key"], "layer": "SHADOW",
                  "reason": _HOLD_REASONS[row["key"]]} for row in shadows]
    if set(row["formula"] for row in remaining) != set(_HOLD_REASONS):
        raise ValueError("the existing five oxide shadows must stay in their verified layer")
    return {"status": "PASS", "release_id": release.release_id, "formal_derivation_count": len(official),
            "derived_values": [value.to_dict() for value in official], "existing_composition_shadows": remaining}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--compare-stage6", action="store_true",
                        help="Require both Stage6 and its successor to retain three approved TREO derivations")
    args = parser.parse_args()
    if args.compare_stage6 and not args.candidate:
        parser.error("--compare-stage6 requires --candidate")
    specifications = {**ORACLE, NOLANS: NOLANS_ORACLE} if args.compare_stage6 else ORACLE
    expected = _source_oracle(args.assets, specifications)
    with TemporaryDirectory() as temporary:
        folder = Path(temporary)
        baseline = _derived(args.baseline, folder, expected, specifications, baseline=not args.compare_stage6)
        candidate = (_derived(args.candidate, folder, expected, specifications, baseline=False) if args.candidate
                     else {"status": "NOT_RUN"})
    receipt = {"schema_version": "census-contained-treo-independent-oracle-v1", "formula_registry_sha256": FORMULA_REGISTRY_SHA256,
               "source_oracle": expected, "baseline_shadow_comparison": baseline, "candidate_comparison": candidate}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(canonical_json(receipt) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

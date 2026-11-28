"""Independently read the original reported MREO estimate and assess native conflicts."""

from argparse import ArgumentParser
import csv
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from au_ree_v3.contracts import ResearchRelease, canonical_json
from au_ree_v3.oxide_derivations import (
    OXIDE_FORMULA_REGISTRY_SHA256, checked_census_oxide_derivations, verify_oxide_derivation_assessment,
)
from au_ree_v3.research_inputs import checked_research_inputs
from au_ree_v3.sources import ContentStore
from au_ree_v3.store import Store

WUDINNA = "89467aedb62238c6a98902633fba1bcaf4ccd0f081beaa736facf71533ac3864"
WOLVERINE = "e29ab1305c8edb5bd1074806d5cf31c323c82cfd68c5c5b2e9b545137f09ccb0"
WUDINNA_PROJECT = "AU-0098e17a63e7c05c213f"
WOLVERINE_PROJECT = "AU-9bd8e5ed8408cd84d876"
IMAGE_SHA = "f776a023a5a7f96c8ef90aeb52ce3a05427b609603a01064d4e100bd440447f0"
TABLE_5_ORACLE = Path(__file__).resolve().parents[1] / "tests/fixtures/wolverine_constituent_oracle_v1.json"


def _text(pdf, page):
    result = subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                            capture_output=True, check=True, text=True, timeout=30)
    return " ".join(result.stdout.split())


def _match(pdf, quotes):
    cache = {}
    evidence = []
    for page, quote in quotes:
        if page not in cache:
            cache[page] = _text(pdf, page)
        if cache[page].count(quote) != 1:
            raise ValueError(f"Unique original evidence not recovered from physical PDF page {page}")
        evidence.append({"physical_page": page, "quote": quote})
    return evidence


def _reported(token):
    value = Fraction(token)
    digits = len(token.partition(".")[2])
    half = Fraction(1, 2 * 10**digits)
    return (value, value - half, value + half)


def _decimal(value):
    with localcontext() as context:
        context.prec = 50
        return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


def _mass(ore, *components):
    product = [value * sum(qualities, Fraction()) / 1000
               for value, qualities in zip(_reported(ore), zip(*map(_reported, components)), strict=True)]
    return {"value": _decimal(product[0]), "rounding_interval": list(map(_decimal, product[1:]))}


def _wudinna(pdf, temp):
    evidence = _match(pdf, [
        (3, "Total 150 470 101 10 112"),
        (3, "MREO = Magnet Rare Earth Oxide(Nd2O3, Pr6O11, Dy2O3, Tb4O7)"),
        (3, "Mineral Resources are reported with an Effective Date of 22 October, 2026."),
        (12, "density methodology was selected to ensure that the values applied represent dry in-situ tonnes for Mineral Resource reporting."),
        (15, "Combined Estimate"),
    ])
    prefix = temp / "image"
    subprocess.run(["pdfimages", "-f", "15", "-l", "15", str(pdf), str(prefix)],
                   capture_output=True, check=True, timeout=30)
    digest = sha256((temp / "image-002.ppm").read_bytes()).hexdigest()
    if digest != IMAGE_SHA:
        raise ValueError("Wudinna companion image differs from independently reviewed pixels")
    # Fixed manual readback of the final Indicated + Inferred ISR & OP row from
    # the exact image, independent of the production review or its arithmetic.
    nd_pr, dy_tb = _reported("101"), _reported("10.5")
    mreo = _reported("112")
    if not (nd_pr[1] + dy_tb[1] <= mreo[2] and mreo[1] <= nd_pr[2] + dy_tb[2]):
        raise ValueError("Reported MREO and separately read companion interval are inconsistent")
    return {"source_sha256": WUDINNA, "role": "REPORTED_NAMED_GROUP", "original_evidence": evidence,
            "source_tokens": {"ore_mt":"150", "reported_mreo_ppm":"112", "treo_ppm":"470"},
            "companion": {"physical_page":15, "image_slot":2, "image_sha256":digest, "ore_mt":"149.92",
                          "treo_ppm":"470", "nd_pr_ppm":"101", "dy_tb_ppm":"10.5"},
            "scope_filters": ["Open Pit 200 ppm TREO−CeO2", "Pidinga ISR economic cutover USD 5.27/t"],
            **_mass("150", "112")}


def _wolverine(pdf, *, expanded=False):
    original = _match(pdf, [
        (5, "Table 2: Mineral Resource estimate for the Wolverine deposit as at 15 January 2025 reported above a 0.15 % TREO cut-off grade."),
        (5, "Total 7.3 0.96 0.85 5.68 0.12 89 70,500"),
        (6, "Table 5: Wolverine Total Mineral Resource estimate at 15 January 2025 reported above a 0.15 % TREO cut-off grade – all TREO variables."),
        (6, "Total 7,300 170 420 60 280 200 40 560 120 840 170 490 70 380 50 5,700"),
        (39, "Tonnage was estimated on a dry basis."),
    ])
    outcomes = []
    for species, left, right in (("Dy2O3", "0.85", "840"), ("Y2O3", "5.68", "5700"), ("Tb4O7", "0.12", "120")):
        a = tuple(value * 1000 for value in _reported(left))
        b = _reported(right)
        overlap = a[1] <= b[2] and b[1] <= a[2]
        outcomes.append({
            "species": species, "status": "ROUNDING_INTERVALS_OVERLAP" if overlap else "SCIENTIFIC_CONFLICT",
            "table_2": {"token":left, "unit":"kg/t", "physical_page":5, "interval_ppm":list(map(_decimal,a[1:]))},
            "table_5": {"token":right, "unit":"ppm", "physical_page":6, "interval_ppm":list(map(_decimal,b[1:]))},
            "unresolved_issuer_rounding_quantum": not overlap,
        })
    components = {"Nd2O3_kt": ("280",), "Pr6O11_kt": ("60",), "Tb4O7_kt": ("120",), "ndpr_kt": ("280", "60")}
    if expanded:
        transcription = json.loads(TABLE_5_ORACLE.read_bytes())
        reference = transcription["reference"]
        original += _match(pdf, [(reference["page"], reference["chemistry_quote"])])
        components.update({f"{species}_kt": (token,) for species, token in transcription["new_selected"].items()})
    return {"source_sha256":WOLVERINE, "original_evidence":original, "source_precision_assessments":outcomes,
            "retained_shadow_only": {key:{**_mass("7.3",*tokens), "reason":"NO_FORMAL_REGISTRY_FOR_THIS_OXIDE_USE"}
                                     for key,tokens in components.items()},
            "other_groups": "HREE8/full TREO sum require all scoped members and no relevant precision conflict; four-member calculated magnet group requires independently resolved Dy2O3."}


def _checked_build(build, oracle, sh, temp, *, expected_count, verify_disclosure=False):
    extraction = json.loads((build / "observations/extraction.json").read_bytes())
    census = json.loads((build / "census/census_receipt.json").read_bytes())
    db = temp / f"formal-{expected_count}.sqlite"
    shutil.copy2(build / "observations/research.sqlite", db)
    with Store(db, trusted_temporal_evidence_ids=extraction["trusted_temporal_evidence_ids"]) as store:
        release = store.get(ResearchRelease, extraction["release_id"])
        if release is None:
            raise ValueError("Build lacks its governed observation release")
        selected = checked_census_oxide_derivations(store, release)
        if len(selected) != expected_count or store.integrity() != {"integrity_check":["ok"],"foreign_key_check":[]}:
            raise ValueError("Unexpected formal oxide count or SQLite integrity")
        if verify_disclosure:
            inputs = checked_research_inputs(store, release, purpose="CENSUS")
            _checked_disclosure(build, census, inputs)
    shadows = {row["key"]:row for row in census["science_views"]["derived_candidates"]
               if row["project_id"] == WOLVERINE_PROJECT}
    expected_shadow = sh["retained_shadow_only"]
    if set(shadows) != set(expected_shadow) or any(
        (Decimal(shadows[key]["value"]), list(map(Decimal,shadows[key]["rounding_interval"])))
        != (Decimal(row["value"]),list(map(Decimal,row["rounding_interval"]))) for key,row in expected_shadow.items()
    ):
        raise ValueError("Wolverine shadow results differ from the independent original-data arithmetic")
    with (build / "census/cell_status.csv").open(newline="", encoding="utf-8") as handle:
        projected = [row for row in csv.DictReader(handle) if row["column"] == "磁鐵稀土氧化物蘊藏量_kt"
                     and row["value_origin"] == "FORMAL_DERIVED"]
    if len(projected) != expected_count:
        raise ValueError("Census derived MREO projection count differs from SQLite")
    for value,cell in zip(selected, projected, strict=True):
        if (value.project_id != WUDINNA_PROJECT or cell["project_id"] != value.project_id
                or not verify_oxide_derivation_assessment(value.assessment)
                or value.value != cell["value"] or Decimal(value.value) != Decimal(oracle["value"])
                or list(map(Decimal,value.assessment["rounding_interval"])) != list(map(Decimal,oracle["rounding_interval"]))
                or json.loads(cell["precision"])["rounding_interval"] != value.assessment["rounding_interval"]
                or cell["comparable"] != "NO"):
            raise ValueError("Formal approved Wudinna calculation differs from the original evidence Oracle")
    return {"status":"PASS", "release_id":release.release_id, "approved_oxide_cells":expected_count,
            "new_independent_measurements":0, "wolverine_shadows_checked":len(shadows),
            "derived_value_ids":[value.derived_id for value in selected]} | (
                {"individual_disclosure_verified": 1} if verify_disclosure else {}
            )


def _checked_disclosure(build, census, inputs):
    original = json.loads(TABLE_5_ORACLE.read_bytes())
    proof, = census["science_views"]["verified_magnet_oxide_disclosure"].items()
    project, description = proof
    if project != WOLVERINE_PROJECT or description["display_only"] is not True:
        raise ValueError("Only Wolverine has independently verified individual scientific disclosure")
    by_observation = {item.raw.observation_id: item for item in inputs}
    readings = {}
    for entry in description["inputs"]:
        row = by_observation[entry["observation_id"]]
        if (row.raw.project_id, row.source.sha256, row.anchor.page, row.anchor.table, row.anchor.row,
                row.raw.raw_unit, row.temporal_trace["status"]) != (
            WOLVERINE_PROJECT, WOLVERINE, 6, "Table 5", "Total", "ppm", "ELIGIBLE",
        ):
            raise ValueError("Disclosed oxide is outside Wolverine's original dated table")
        readings[row.anchor.column] = row.raw.raw_value
    if (readings != original["legacy_selected"] or set(description["numerical_precision_holds"]) != {"Dy2O3"}
            or description["numerical_precision_holds"]["Dy2O3"]["nominal_rounding_intervals_ppm"] != {
                "selected": ["839.5", "840.5"], "companion": ["845.000", "855.000"],
            }):
        raise ValueError("Wolverine disclosure tokens or unresolved Dy2O3 precision differ from the independent original")
    with (build / "census/cell_status.csv").open(newline="", encoding="utf-8") as handle:
        flags = [row for row in csv.DictReader(handle) if row["column"] == "四項磁性元素皆公布" and row["value_state"] == "PRESENT"]
    flag, = flags
    if (flag["project_id"], flag["value"], flag["value_origin"], flag["evidence_state"], flag["comparable"]) != (
        WOLVERINE_PROJECT, "是", "EVIDENCE_REFERENCE", "DESCRIPTIVE_ONLY", "NO",
    ):
        raise ValueError("Disclosure flag must be a descriptive Census value")


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--compare-stage6", action="store_true",
                        help="Require both the Stage6 baseline and its successor to retain the same approved oxide value")
    parser.add_argument("--require-disclosure-gain", action="store_true",
                        help="Check Wolverine's one descriptive disclosure against original Table 5")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.compare_stage6 and not args.build:
        parser.error("--compare-stage6 requires --build")
    if args.require_disclosure_gain and not args.compare_stage6:
        parser.error("--require-disclosure-gain requires --compare-stage6")
    store = ContentStore(args.assets)
    with TemporaryDirectory() as scratch:
        temp = Path(scratch)
        for sha in (WUDINNA,WOLVERINE):
            store.read(sha)
        oracle = _wudinna(store.path(WUDINNA), temp)
        wolverine = _wolverine(store.path(WOLVERINE), expanded=args.compare_stage6)
        baseline = _checked_build(args.baseline, oracle, wolverine, temp, expected_count=int(args.compare_stage6))
        comparison = (_checked_build(args.build, oracle, wolverine, temp, expected_count=1,
                                     verify_disclosure=args.require_disclosure_gain) if args.build
                      else {"status":"NOT_RUN"})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(canonical_json({"schema_version":"reported-mreo-independent-oracle-v1",
                                        "formula_registry_sha256":OXIDE_FORMULA_REGISTRY_SHA256,
                                        "wudinna":oracle, "wolverine":wolverine, "baseline":baseline, "candidate":comparison})+"\n")


if __name__ == "__main__":
    main()

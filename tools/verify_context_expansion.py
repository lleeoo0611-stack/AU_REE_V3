"""Verify state and Commonwealth point candidates and corroborate prior production shadows."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from au_ree_v3.contracts import canonical_json
from au_ree_v3.contracts.temporal import evaluate_temporal
from au_ree_v3.documents import DocumentCache
from au_ree_v3.forecast import review_production_forecasts
from au_ree_v3.official_geography import review_official_point_candidates
from au_ree_v3.sources import ContentStore, validate_edition_proofs


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _literal(view, span):
    page, (first, last) = span["page"], span["lines"]
    actual = " ".join(" ".join(view.locate(page, line=line).text for line in range(first, last + 1)).split())
    if actual.count(span["contains"]) != 1:
        raise ValueError(f"Independent forecast excerpt differs at page {page}, lines {first}–{last}")
    return {"physical_page": page, "first_line": first, "last_line": last, "excerpt": span["contains"],
            "layout_sha256": sha256(actual.encode()).hexdigest()}


def verify_forecast_context(production, frozen_sources, assets, oracle_path):
    """Corroborate shadow values with independently pinned source rows and notes."""
    manifest = json.loads(frozen_sources.read_bytes())
    oracle = json.loads(oracle_path.read_bytes())
    if oracle["schema"] != "native-production-forecast-context-oracle-v1":
        raise ValueError("Unknown forecast context oracle")
    if production["source_manifest_sha256"] != _hash(frozen_sources):
        raise ValueError("Forecast source lineage differs from the independent oracle input")
    cases = {row["case_id"]: row for row in production["cases"]}
    content, cache, views, confirmed = ContentStore(assets), DocumentCache(), {}, []
    for expected in oracle["cases"]:
        digest = expected["source_sha256"]
        if digest not in views:
            required = max(span["page"] for item in oracle["cases"] if item["source_sha256"] == digest
                           for span in item["spans"])
            views[digest] = cache.pdf(content.read(digest), digest, required)
        row = cases[expected["case_id"]]
        raw = row["raw_observation"]
        pins = ("role", "amount", "native_unit", "material", "chemistry", "date", "source_sha256")
        columns = ("observation_role", "raw_value", "raw_unit", "material_basis", "chemical_basis",
                   "reported_publication_date", "source_sha")
        source, = (item for item in manifest["editions"] if item["receipt_sha256"] == row["receipt_sha256"]
                   and item["sha256"] == digest and row["project_id"] in item["project_ids"])
        proofs = validate_edition_proofs(content, source, ZoneInfo(manifest["timezone"]))
        temporal = evaluate_temporal(
            source_sha256=digest, observation_role=expected["role"], cutoff=manifest["cutoff"],
            publication_date=expected["date"], acquired_at=source["acquired_at"], reacquired_at=source["reacquired_at"],
            evidence=proofs,
        )
        if not (tuple(raw[key] for key in columns) == tuple(expected[key] for key in pins)
                and proofs and temporal.status == "ELIGIBLE"
                and canonical_json(temporal.to_dict()) == canonical_json(row["temporal_decision"])
                and row["status"] == "VERIFIED_SHADOW" and not row["formal_selection"] and not row["formal_mcda"]
                and raw["status"] == "SHADOW" and raw["denominator_basis"] == "YEAR"
                and raw["effective_date"] is None and raw["event_date"] is None):
            raise ValueError(f"Original forecast quantity, role, basis or date disagrees: {expected['case_id']}")
        confirmed.append({key: expected[key] for key in ("case_id", *pins, "period")} | {
            "project_id": row["project_id"], "source_url": source["url"], "receipt_sha256": source["receipt_sha256"],
            "state": "SHADOW", "observation_id": raw["observation_id"],
            "source_spans": [_literal(views[digest], span) for span in expected["spans"]],
        })
    negative = oracle["negative"]
    row = cases[negative["case_id"]]
    if row["status"] != "REJECTED" or row["reason"] != "LIFETIME_PRODUCT_MASS_IS_NOT_ANNUAL_CONTAINED_TREO":
        raise ValueError("Life-of-mine product mass is not an annual contained TREO observation")
    return {
        "schema": oracle["schema"], "scope": "FIXED_144", "status": "PASS",
        "source_manifest_sha256": _hash(frozen_sources), "oracle_sha256": _hash(oracle_path),
        "cases": confirmed, "negative": {"reason": row["reason"], "source_sha256": negative["source_sha256"],
                                       "source_span": _literal(views[negative["source_sha256"]], negative)},
        "native_pdf_opens": len(views), "native_pages_decoded": sum(len(view.pages) for view in views.values()),
        "new_observations": 0, "formal_census": 0, "formal_mcda": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "fixed-sources", "fixed-assets", "expanded-sources", "expanded-assets",
                 "point-bindings", "forecast-bindings", "forecast-oracle", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (args.out.exists() and any(args.out.iterdir())) or any(
        args.out.resolve().is_relative_to(path.resolve()) for path in (args.fixed_assets, args.expanded_assets)
    ):
        raise ValueError("Context audit requires a new output directory separate from both source stores")
    args.out.mkdir(parents=True, exist_ok=True)
    production = review_production_forecasts(args.profile, args.fixed_sources, args.fixed_assets, args.forecast_bindings,
                                            args.out / "production_shadows.json")
    forecast = verify_forecast_context(production, args.fixed_sources, args.fixed_assets, args.forecast_oracle)
    forecast_path = args.out / "production_forecast_context_oracle.json"
    forecast_path.write_text(canonical_json(forecast) + "\n")
    points = args.out / "government_native_points_shadow.json"
    geography = review_official_point_candidates(args.point_bindings, args.fixed_sources, args.fixed_assets,
                                                 args.expanded_sources, args.expanded_assets, points)
    print(canonical_json({"forecast": {"status": forecast["status"], "receipt_sha256": _hash(forecast_path)},
                          "geography": {"status": geography["status"], "receipt_sha256": _hash(points)}}))


if __name__ == "__main__":
    main()

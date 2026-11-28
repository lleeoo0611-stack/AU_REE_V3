"""Replay official geography role checks and issuer production scenarios."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from au_ree_v3.contracts import EntityKind, canonical_json
from au_ree_v3.forecast import review_production_forecasts
from au_ree_v3.geography import ROLE, evaluate_infrastructure_candidates, infrastructure_site_compatibility, inventory_tenements


GEO_SCHEMA = "government-location-use-shadow-v1"
ROLE_REQUIREMENTS = {
    ROLE: {"entity_kind": EntityKind.TENEMENT.value, "geometry": ["Polygon", "MultiPolygon"],
           "census_point_use": "NOT_COMPARABLE", "requires": "official exact native legal-interest polygon"},
    "OFFICIAL_DEPOSIT_POINT": {"entity_kind": EntityKind.DEPOSIT.value, "geometry": ["Point"],
                               "census_point_use": "REQUIRES_APPROVAL", "requires": "official point, native ID, precision, verified Project relation"},
    "OFFICIAL_MINE_POINT": {"entity_kind": EntityKind.MINE.value, "geometry": ["Point"],
                            "census_point_use": "REQUIRES_APPROVAL", "requires": "official point, native ID, precision, verified Project relation"},
    "PROSPECT": {"entity_kind": None, "geometry": [], "census_point_use": "NOT_COMPARABLE",
                 "requires": "retain original prospect feature kind without inferring a resource deposit or mine"},
}


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def _actual_centroid(feature):
    """Produce a synthetic use test using the actual official geometry as input."""
    dataset = {
        "type": "FeatureCollection", "name": "boundary",
        "crs": {"type": "name", "properties": {"name": feature["crs"]}},
        "features": [{"type": "Feature", "properties": {}, "geometry": feature["geometry"]}],
    }
    with TemporaryDirectory() as working:
        local = Path(working) / "boundary.geojson"
        local.write_text(canonical_json(dataset))
        process = subprocess.run(
            ["ogrinfo", "-ro", "-json", "-features", "-dialect", "SQLite",
             "-sql", "SELECT ST_Centroid(geometry) AS geometry FROM boundary", str(local)],
            check=True, capture_output=True, timeout=15,
        )
    layer, = json.loads(process.stdout)["layers"]
    point, = layer["features"]
    return point["geometry"]


def _geography(shapes, sources, assets, out):
    tenements = out / "tenements.json"
    infrastructure = out / "infrastructure_compatibility.json"
    bundle = inventory_tenements(shapes, sources, assets, tenements)
    compatibility = evaluate_infrastructure_candidates(tenements, sources, infrastructure)
    proposals, checks = [], []
    for row in bundle["locations"]:
        proposals.append({
            "project_id": row["project_id"], "native_entity_kind": EntityKind.TENEMENT.value,
            "native_id": (row.get("locator") or {}).get("native_id"),
            "locator": row.get("locator"), "source_url": row["source_url"], "source_sha256": row["source_sha256"],
            "crs": row["crs"], "precision": row.get("precision"), "location_role": row["location_role"],
            "geometry_type": (row.get("geometry") or {}).get("type"), "geometry_sha256": row.get("geometry_sha256"),
            "project_link": row.get("project_link"), "temporal_decision_id": (row.get("temporal_decision") or {}).get("decision_id"),
            "state": row["status"], "reason": row["reason"],
        })
    used_crs, unavailable = set(), {}
    for row in bundle["locations"]:
        if row["status"] != "VERIFIED_BOUNDARY" or row["crs"] in used_crs:
            continue
        used_crs.add(row["crs"])
        try:
            point = _actual_centroid(row)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            unavailable[row["crs"]] = type(error).__name__
            continue
        control = {**row, "location_role": "TENEMENT_CENTROID", "geometry": point}
        assessment = infrastructure_site_compatibility(control)
        if point.get("type") != "Point" or assessment["status"] != "NOT_COMPARABLE":
            raise ValueError("A centroid derived from an official tenure polygon must remain outside site use")
        checks.append({
            "control": "CENTROID_OF_ACTUAL_OFFICIAL_TENEMENT",
            "crs": row["crs"], "source_sha256": row["source_sha256"], "project_id": row["project_id"],
            "native_id": row["locator"]["native_id"], "geometry_sha256": row["geometry_sha256"],
            "test_point_sha256": sha256(canonical_json(point).encode()).hexdigest(),
            "method": "GDAL SQLite ST_Centroid", "assessment": assessment,
        })
    report = {
        "schema": GEO_SCHEMA, "scope": "CENSUS_INFRASTRUCTURE_SHADOW", "source_manifest_sha256": _sha(sources),
        "original_replay_hashes": {"tenements": _sha(tenements), "I1_I4": _sha(infrastructure)},
        "status": "PASS" if compatibility["status"] == "PASS" and checks and not unavailable else "BLOCKED",
        "cutoff": bundle["cutoff"], "timezone": bundle["timezone"], "runtime": bundle["runtime"],
        "candidate_roles": ROLE_REQUIREMENTS, "counts_by_state": dict(Counter(row["state"] for row in proposals)),
        "native_official_layer_sha256": sorted({row["source_sha256"] for row in proposals}),
        "cases": proposals, "actual_source_centroid_controls": checks,
        "unavailable_centroid_controls": unavailable,
        "verified_mine_or_deposit_points": compatibility["verified_mine_or_deposit_points"],
        "criteria_reviewed": sum(len(row["criteria"]) for row in compatibility["project_criteria"]),
        "formal_census_cells": 0, "formal_mcda_criteria": compatibility["formal_indicators"],
    }
    (out / "government_geography_role_shadow.json").write_text(canonical_json(report) + "\n")
    return report


def build_context_shadows(*, profile, sources, assets, geography_bindings, forecast_bindings, out):
    """Run both optional contexts with independent receipts."""
    if out.resolve().is_relative_to(assets.resolve()) or (out.exists() and any(out.iterdir())):
        raise ValueError("Use an empty shadow output directory separate from the source store")
    out.mkdir(parents=True, exist_ok=True)
    forecast_path = out / "production_forecast_shadow.json"
    forecast = review_production_forecasts(profile, sources, assets, forecast_bindings, forecast_path)
    geography = _geography(geography_bindings, sources, assets, out)
    report = {
        "schema": "census-context-shadow-replay-v1", "scope": "SHADOW", "source_manifest_sha256": _sha(sources),
        "geography": {"status": geography["status"], "receipt_sha256": _sha(out / "government_geography_role_shadow.json")},
        "forecast": {"status": forecast["status"], "receipt_sha256": _sha(forecast_path)},
        "formal_census_cells": 0, "formal_selections": 0, "formal_mcda_criteria": 0,
    }
    (out / "context_shadow_replay.json").write_text(canonical_json(report) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "sources", "assets", "geography-bindings", "forecast-bindings", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    print(canonical_json(build_context_shadows(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()

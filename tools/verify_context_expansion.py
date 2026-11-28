"""Verify state and Commonwealth point candidates and corroborate prior production shadows."""

import argparse
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from au_ree_v3.contracts import canonical_json
from au_ree_v3.forecast import review_production_forecasts, verify_forecast_context
from au_ree_v3.geography import INFRASTRUCTURE_LAYERS
from au_ree_v3.official_geography import review_official_point_candidates
from au_ree_v3.research_delta import ResearchBuild


_GOVERNMENT_ORACLE = {
    "mt-weld-cld-mine": ("S0001709", "Mine", "J00772", None, "OFFICIAL_MINE_POINT"),
    "mt-weld-resource-group-wa": ("S0237005", "Mine", "J00772", None, "OFFICIAL_MINE_POINT"),
    "north-stanmore-deposit-wa": ("S0240894", "Deposit", "J06272", None, "OFFICIAL_DEPOSIT_POINT"),
    "jupiter-deposit-wa": ("S0242502", "Deposit", "J06523", None, "OFFICIAL_DEPOSIT_POINT"),
    "mt-weld-resource-group-ga": (763335, "mineral deposit", None, 5, "OFFICIAL_DEPOSIT_POINT"),
    "aileron-deposit-ga": (2419, "Mineral deposit", None, None, "OFFICIAL_DEPOSIT_POINT"),
    "wim150-deposit-ga": (2282, "Mineral deposit", None, None, "OFFICIAL_DEPOSIT_POINT"),
    "copi-deposit-ga": (2303, "Mineral deposit", None, None, "OFFICIAL_DEPOSIT_POINT"),
    "mt-weld-gold-homonym-wa": ("S0012302", "Mine", "J03533", None, "OFFICIAL_MINE_POINT"),
    "mt-weld-road-wa": ("S0021959", "Infrastructure", "J00772", None, "OTHER_OFFICIAL_LOCATION"),
    "jupiter-prospect-wa": ("S0244130", "Prospect", "J06523", None, "PROSPECT"),
}
_CONTROLS = {
    "mt-weld-gold-homonym-wa": {"NATIVE_GOVERNMENT_PROJECT_ID_MISMATCH", "NATIVE_COMMODITY_SCOPE_MISMATCH"},
    "mt-weld-road-wa": {"FEATURE_ROLE_OUTSIDE_MINE_OR_DEPOSIT_POINT"},
    "jupiter-prospect-wa": {"FEATURE_ROLE_OUTSIDE_MINE_OR_DEPOSIT_POINT"},
}
_LAYOUTS = {
    "WA_MINEDEX": ("site_code", "site_title", "site_type_", "proj_code", 7844, "GDA2020"),
    "GA_CRITICAL_DEPOSITS": ("objectid", "projectname", "status", None, 4283, "GDA94"),
    "GA_OZMIN": ("ENO", "OCCURRENCE_NAME", "CLASS", "PROJECT", 4283, "GDA94"),
}
_FORECAST_GAPS = {
    "jupiter-base-case-annual-contained-treo": ("MODELED_FORECAST", "LIFE_OF_MINE_ANNUAL_AVERAGE"),
    "jupiter-nominal-design-annual-contained-treo": ("ENGINEERING_PLAN", "NOMINAL_PROCESSING_RATE"),
    "north-stanmore-pfs-base-case-annual-wet-concentrate": (
        "MODELED_FORECAST", "WET_CONCENTRATE_PRODUCT_MASS", "CONTAINED_TREO_BASIS_UNBOUND",
        "AVERAGING_WINDOW_UNSPECIFIED",
    ),
}
_FORECAST_PERIODS = {
    "jupiter-base-case-annual-contained-treo": "LIFE_OF_MINE_ANNUAL_AVERAGE_INCLUDING_RAMP_UP_AND_SCHEDULING",
    "jupiter-nominal-design-annual-contained-treo": "NOMINAL_RATE_AT_8_0_MTPA_FEED_NOT_LIFE_OF_MINE_AVERAGE",
    "north-stanmore-pfs-base-case-annual-wet-concentrate": "PFS_ANNUAL_BASE_CASE_AVERAGING_WINDOW_UNSPECIFIED",
}
_RELEASE_IDENTITIES = {"selection_id", "lineage_id", "derived_value_id", "assessment_id", "derivation_id", "derived_id"}
_RELEASE_HASH = re.compile(r"[a-f0-9]{64}")
_DECISION_DIGEST_LINKS = {
    "decision/decision_receipt.json": ("mcda_inputs_sha256", "mcda_results_sha256"),
    "decision/mcda_results.json": ("mcda_inputs_sha256",),
    "decision/mcda_inputs.json": (),
}


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _object(root, digest):
    path = root / "objects" / digest
    if _hash(path) != digest:
        raise ValueError("Independent context source fingerprint differs")
    return json.loads(path.read_bytes())


def _captured_layers(manifest, assets):
    layers = {}
    for edition in manifest["editions"]:
        capture = _object(assets, edition["receipt_sha256"])
        local = datetime.fromisoformat(capture["acquired_at"]).astimezone(ZoneInfo(manifest["timezone"]))
        if (capture["sha256"] != edition["sha256"] or capture["http_status"] != 200
                or capture["status"] != "PUBLIC_CAPTURED" or capture["method"] != "ANONYMOUS_HTTPS_GET"
                or capture["final_url"] != edition["final_url"]
                or local.isoformat() != capture["acquired_at_research_timezone"] or local.date().isoformat() > manifest["cutoff"]):
            raise ValueError("Official acquisition is not tied to its original response and cutoff")
        layers[edition["receipt_sha256"]] = (edition, _object(assets, edition["sha256"]), local.date().isoformat())
    return layers


def _native_feature(payload, dataset, native_id):
    id_field, name_field, role_field, project_field, epsg, datum = _LAYOUTS[dataset]
    if dataset == "GA_OZMIN":
        reported_crs = payload["crs"]["properties"]["name"]
        if (reported_crs != f"urn:ogc:def:crs:EPSG::{epsg}" or payload["type"] != "FeatureCollection"
                or payload["numberMatched"] != len(payload["features"])):
            raise ValueError("Independent WFS CRS, format, or coverage differs")
        feature, = (item for item in payload["features"] if item["properties"][id_field] == native_id)
        properties, geometry = feature["properties"], feature["geometry"]
    else:
        if (set(payload["spatialReference"].values()) != {epsg} or payload["geometryType"] != "esriGeometryPoint"
                or payload.get("exceededTransferLimit")):
            raise ValueError("Independent ArcGIS CRS, format, or coverage differs")
        feature, = (item for item in payload["features"] if item["attributes"][id_field] == native_id)
        properties = feature["attributes"]
        geometry = {"type": "Point", "coordinates": [feature["geometry"]["x"], feature["geometry"]["y"]]}
    if geometry["type"] != "Point" or len(geometry["coordinates"]) != 2:
        raise ValueError("Independent original geometry is not a native two-axis point")
    raw_date = properties.get("extract_da")
    extract_date = datetime.fromtimestamp(raw_date / 1_000, UTC).date().isoformat() if raw_date is not None else None
    return {
        "native_id_field": id_field, "native_id": native_id, "native_name": properties[name_field],
        "raw_role": properties[role_field], "native_project_id": properties.get(project_field) or None,
        "native_cross_reference": properties.get("OTHER_ID"), "positional_accuracy_m": properties.get("ACCURACY_M"),
        "geometry": geometry, "crs": f"EPSG:{epsg}", "datum": datum, "source_extract_date": extract_date,
        "known_temporal_metadata_fields": sorted(field for field in properties if "date" in field.lower() or field == "extract_da"),
    }


def _verify_cld_scope(site, fixed_sources, fixed_assets):
    proof = site["issuer_scope_proof"]
    manifest = json.loads(fixed_sources.read_bytes())
    source = "fe26ac29c4773a5af95e909a1cd95ca1e9ad37c34560dfa72c1e9674e4c8a503"
    pdf = fixed_assets / "objects" / source
    if not (proof and proof["source_sha256"] == _hash(pdf) == source and site["native_name"] == "Central Lanthanide (CLD)"
            and any(edition["sha256"] == source and site["project_id"] in edition["project_ids"]
                    and edition["url"] == proof["source_url"] for edition in manifest["editions"])):
        raise ValueError("Independent issuer edition does not establish the distinctive CLD subsite")
    page = subprocess.run(["pdftotext", "-f", "3", "-l", "3", "-layout", "-enc", "UTF-8", str(pdf), "-"],
                          capture_output=True, check=True, timeout=20).stdout.decode()
    view = " ".join(page.split())
    anchors = proof["anchors"]
    if len(anchors) != 2 or {a["quote"] for a in anchors} != {"Mt Weld open pit", "Central Lanthanide Deposit (CLD)"}:
        raise ValueError("Independent issuer subsite quote roles differ")
    for anchor in anchors:
        span = anchor["text_span"]
        if anchor["page"] != 3 or anchor["source_sha"] != source or view[span["start"]:span["end"]] != anchor["quote"]:
            raise ValueError("Independent issuer CLD text span differs")


def verify_government_origins(geography, expanded_sources, assets, fixed_sources, fixed_assets):
    """Re-decode original responses independently of the production feature-index parser."""
    manifest = json.loads(expanded_sources.read_bytes())
    if (geography["expanded_manifest_sha256"] != _hash(expanded_sources)
            or geography["fixed_manifest_sha256"] != manifest["base_manifest_sha256"]
            or geography["fixed_manifest_sha256"] != _hash(fixed_sources)
            or geography["status"] != "PASS" or geography["formal_census"] or geography["formal_infrastructure"]):
        raise ValueError("Government origin audit requires the exact matched shadow review")
    layers = _captured_layers(manifest, assets)
    indexed = {row["case_id"]: row for row in geography["cases"]}
    if len(indexed) != len(geography["cases"]) or indexed.keys() != _GOVERNMENT_ORACLE.keys():
        raise ValueError("Official point oracle case denominator changed")
    observed = {}
    for name, (native_id, raw_role, native_project, accuracy, role) in _GOVERNMENT_ORACLE.items():
        row = indexed[name]
        edition, payload, capture_date = layers[row["receipt_sha256"]]
        point = _native_feature(payload, row["dataset"], native_id)
        locator = row["native_locator"]
        raw = (point["raw_role"], point["native_project_id"], point["positional_accuracy_m"])
        claimed = (row["raw_role"], locator["native_project_id"], row["precision"]["positional_accuracy_m"])
        if not (raw == claimed == (raw_role, native_project, accuracy)
                and (locator["field"], locator["native_id"]) == (point["native_id_field"], native_id)
                and row["location_role"] == role and row["source_sha256"] == edition["sha256"]
                and row["project_id"] in edition["project_ids"] and row["source_url"] == edition["url"]
                and row["state"] == "SHADOW" and row["feature_effective_date"] is None
                and row["document_temporality"]["observation_role"] == "DOCUMENT"
                and row["document_temporality"]["earliest_proven_public_date"] == capture_date
                and all(row[k] == point[k] for k in ("native_name", "crs", "datum", "geometry", "source_extract_date",
                                                     "native_cross_reference"))
                and row["geometry_sha256"] == sha256(canonical_json(point["geometry"]).encode()).hexdigest()
                and not row["formal_census"] and not row["formal_infrastructure"]):
            raise ValueError(f"Independent original government role, date, axes, identity, or precision differs: {name}")
        issues = set(row["open_issues"])
        required = {"FEATURE_SURVEY_OR_APPLICABILITY_DATE_UNBOUND"} | _CONTROLS.get(name, set())
        if accuracy is None:
            required.add("METRIC_POSITIONAL_ACCURACY_UNDISCLOSED")
        if name != "mt-weld-cld-mine":
            required.add("EXACT_FIXED_PROJECT_TO_GOVERNMENT_SITE_LINK_UNBOUND")
        expected = "REJECTED" if name in _CONTROLS else "EVIDENCE_INSUFFICIENT"
        if (issues != required or row["proposed_site_use"] != expected
                or row["infrastructure_compatibility"]["status"] == "SITE_CANDIDATE"
                or "feature_effective_date" not in row["infrastructure_compatibility"]["missing_dimensions"]
                or point["known_temporal_metadata_fields"] not in ([], ["extract_da"])):
            raise ValueError(f"Point scope, precision, and time prerequisites differ: {name}")
        observed[name] = {**{k: v for k, v in point.items() if k != "geometry"},
                          "dataset": row["dataset"], "project_id": row["project_id"],
                          "source_sha256": row["source_sha256"], "capture_sha256": row["receipt_sha256"],
                          "capture_date": capture_date, "feature_effective_date": None,
                          "geometry_sha256": row["geometry_sha256"], "proposed_site_use": expected,
                          "issuer_scope_proof": row["project_link"], "blockers": sorted(issues)}
    _verify_cld_scope(observed["mt-weld-cld-mine"], fixed_sources, fixed_assets)
    return _origin_summary(geography, layers, observed)


def _origin_summary(geography, layers, observed):
    point_sources = [payload for _, payload, _ in layers.values() if "features" in payload]
    relation, = geography["cross_authority_relations"]
    if not (observed["mt-weld-resource-group-ga"]["native_cross_reference"] == "S0237005"
            and relation["case_ids"] == ["mt-weld-resource-group-ga", "mt-weld-resource-group-wa"]
            and relation["exact_native_cross_reference"] and not relation["source_roles_agree"]
            and not relation["scope_equivalence"] and relation["verdict"] == "CROSS_AUTHORITY_ROLE_MISMATCH"):
        raise ValueError("A native database cross-reference cannot resolve the representative location role")
    metadata = {}
    for row in geography["layer_metadata"]:
        _, native, capture_date = layers[row["receipt_sha256"]]
        period = "2025-12" if "as at December 2025" in native["description"] else None
        if (row["declared_fields"] != [field["name"] for field in native["fields"]]
                or row["snapshot_period"] != period or row["feature_measurement_date"] is not None):
            raise ValueError("Official layer snapshot date is not a native feature date")
        metadata[row["dataset"]] = {"source_sha256": row["source_sha256"], "capture_date": capture_date,
                                    "snapshot_period": period, "snapshot_date_role": row["snapshot_kind"]}
    return {
        "schema": "government-origin-independent-readback-v1", "status": "PASS", "scope": "EXPANDED_MANIFEST",
        "expanded_manifest_sha256": geography["expanded_manifest_sha256"], "cases": observed,
        "point_response_count": len(point_sources), "native_rows": sum(len(p["features"]) for p in point_sources),
        "review_candidates": len(observed) - len(_CONTROLS), "rejected_controls": len(_CONTROLS),
        "representative_origins": 0, "distinct_origin_candidates_with_precision_m": 1,
        "cross_authority_relation": relation, "layer_metadata": metadata,
        "distance_computation": {criterion: {"status": "NOT_RUN", "reason": "NO_VERIFIED_REPRESENTATIVE_ORIGIN",
                                             "required_endpoint_role": role, "distance_km": None}
                                 for criterion, role in INFRASTRUCTURE_LAYERS.items()},
    }


def _rebound_record(value, release_id):
    if isinstance(value, list):
        return [_rebound_record(item, release_id) for item in value]
    if isinstance(value, dict):
        return {
            field: "BUILD_RELEASE" if field == "release_id" and item == release_id else
            "RELEASE_RECORD" if (field in _RELEASE_IDENTITIES and isinstance(item, str) and _RELEASE_HASH.fullmatch(item)) else
            _rebound_record(item, release_id)
            for field, item in value.items()
        }
    return value


def _checked_scientific_content(build, root):
    """Hash comparable content after the build verifier checked each actual artifact's linkage."""
    receipt = build.receipt
    release = receipt["observation_release_id"]
    payloads = {}
    cells = []
    for cell in build.census.values():
        evidence = cell["metadata_evidence"]
        cells.append({**cell, "metadata_evidence": json.loads(evidence) if evidence else evidence})
    payloads["census/cell_status.csv"] = cells
    for relative in (*_DECISION_DIGEST_LINKS, "observations/extraction.json", "census/census_receipt.json"):
        payload = json.loads((root / relative).read_bytes())
        for key in _DECISION_DIGEST_LINKS.get(relative, ()):
            del payload[key]
        payloads[relative] = payload
    del payloads["census/census_receipt.json"]["outputs"]["cell_status.csv"]
    comparable = {relative: _rebound_record(payload, release) for relative, payload in payloads.items()}
    science = comparable["census/census_receipt.json"]["science_views"]
    for key, records in science.items():
        if isinstance(records, list):
            science[key] = sorted(records, key=canonical_json)
    identities = {relative: sha256(canonical_json(payload).encode()).hexdigest()
                  for relative, payload in comparable.items()}
    identities.update({name: receipt["scientific_artifacts"][name]
                       for name in ("census/census.csv", "observations/observations.jsonl")})
    return identities


def compare_context_isolation(baseline, reviewed, fixed_sha, government, forecast):
    """Use exact governed artifacts under one profile and distinguish contextual facts from formal inputs."""
    original, candidate = (ResearchBuild.load(path) for path in (baseline, reviewed))
    before, after = original.receipt, candidate.receipt
    if not (before["input_artifacts"] == after["input_artifacts"] and before["input_artifacts"]["sources"] == fixed_sha
            and before["selection_rules_hash"] == after["selection_rules_hash"]
            and original.census_projects == candidate.census_projects and original.census_columns == candidate.census_columns):
        raise ValueError("Context isolation requires identical research inputs, rules, and grid")
    physical = {"before": before["scientific_artifacts"], "after": after["scientific_artifacts"]}
    if before["observation_release_id"] == after["observation_release_id"] and physical["before"] != physical["after"]:
        raise ValueError("One research release has mixed scientific artifact bytes")
    normalized = _checked_scientific_content(original, baseline)
    if normalized != _checked_scientific_content(candidate, reviewed):
        raise ValueError("Same-input scientific content differs beyond verified release lineage")
    criteria = candidate.decision_inputs["criteria_by_project"]
    for project in criteria.values():
        for criterion in INFRASTRUCTURE_LAYERS:
            if (project[criterion]["state"] != "UNKNOWN" or project[criterion]["accepted_value"] is not None
                    or project[criterion]["lineage"]
                    or project[criterion]["reason"] != "CRITERION_ADMISSION_ADAPTER_NOT_MIGRATED"):
                raise ValueError("An unqualified official location produced an infrastructure criterion")
    cases = {row["case_id"]: {**row, "formal_operating_rate_status": "EVIDENCE_INSUFFICIENT",
                              "formal_operating_rate_blockers": list(_FORECAST_GAPS[row["case_id"]])}
             for row in forecast["cases"]}
    if (cases.keys() != _FORECAST_GAPS.keys() or forecast["formal_census"] or forecast["formal_mcda"]
            or {key: row["period"] for key, row in cases.items()} != _FORECAST_PERIODS
            or forecast["source_manifest_sha256"] != fixed_sha or government["representative_origins"]):
        raise ValueError("Study forecasts and official origins must stay outside scientific inputs")
    comparable = Counter(code for axes in criteria.values() for code, row in axes.items() if row["state"] == "COMPARABLE")
    return {
        "schema": "context-comparability-isolation-v1", "status": "PASS", "fixed_manifest_sha256": fixed_sha,
        "baseline_build_sha256": original.receipt_sha, "reviewed_build_sha256": candidate.receipt_sha,
        "profile_sha256": after["input_artifacts"]["profile"], "scientific_artifacts_sha256": physical,
        "scientific_content_sha256": normalized,
        "exact_artifact_matches": [name for name, digest in physical["before"].items() if digest == physical["after"][name]],
        "verified_release_identity_fields": sorted(_RELEASE_IDENTITIES | {"release_id"}),
        "decision_policy_sha256": candidate.decision_inputs["policy_sha256"],
        "point_method_version": candidate.decision_results["point_method_version"],
        "sensitivity_version": candidate.decision_results["deterministic_sensitivity_version"],
        "comparison_scope": "SAME_PROFILE_SAME_INPUT_CONTEXT_REVIEW_ONLY", "criteria_projects": len(criteria),
        "before_comparable_by_criterion": comparable, "after_comparable_by_criterion": comparable,
        "new_independent_verified_observations": 0, "new_selected_scientific_values": 0,
        "new_comparable_project_criterion_keys": 0, "new_complete_panel_projects": 0,
        "panels": after["mcda_panels"], "government": government, "forecast": {
            "scope": forecast["scope"], "cases": cases, "negative": forecast["negative"],
            "formal_annual_production_status": "EVIDENCE_INSUFFICIENT",
        },
    }


def verify_topographic_leads(fixed_sha, manifest_path, assets):
    """Reopen separately acquired topographic searches without treating envelopes as identity."""
    manifest = json.loads(manifest_path.read_bytes())
    if (manifest.get("base_manifest_sha256") != fixed_sha
            or manifest.get("purpose") != "EXPANDED_MANIFEST_GOVERNMENT_ORIGIN_DISCOVERY"):
        raise ValueError("Topographic discovery belongs to another research cohort or purpose")
    reviewed = []
    for edition, payload, capture_date in _captured_layers(manifest, assets).values():
        locator = urlsplit(edition["final_url"])
        if (locator.scheme != "https" or locator.netloc != "services.ga.gov.au"
                or locator.path != "/gis/rest/services/Topographic_Base_Map/MapServer/238/query"
                or payload["geometryType"] != "esriGeometryPoint"
                or payload["spatialReference"]["latestWkid"] != 3857 or payload.get("exceededTransferLimit")):
            raise ValueError("Topographic source identity, CRS or response extent differs")
        features = []
        for feature in payload["features"]:
            raw = feature["attributes"]
            date = raw["featurereliability"]
            features.append({
                "native_id": raw["ufi"], "objectid": raw["objectid"], "name": raw["name"].strip() or None,
                "native_role": raw["featuretype"], "accuracy_m": raw["planimetricaccuracy"],
                "native_note": raw["textnote"], "reliability_date": (
                    datetime.fromtimestamp(date / 1_000, UTC).date().isoformat() if date is not None else None
                ),
                "review_status": "EVIDENCE_INSUFFICIENT", "review_reason": "EXACT_FEATURE_PROJECT_SCOPE_UNBOUND",
            })
        reviewed.append({"source_key": edition["source_key"], "source_sha256": edition["sha256"],
                         "capture_sha256": edition["receipt_sha256"], "source_url": edition["url"],
                         "capture_date": capture_date, "crs": "EPSG:3857", "features": features,
                         "status": "EVIDENCE_INSUFFICIENT" if features else "NO_MATCHING_ROWS"})
    return {"scope": "DISCOVERY_ONLY", "manifest_sha256": _hash(manifest_path),
            "captures": len(reviewed), "unique_sha256": len({row["source_sha256"] for row in reviewed}),
            "reviewed_features": sum(len(row["features"]) for row in reviewed), "admitted_origins": 0,
            "responses": reviewed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "fixed-sources", "fixed-assets", "expanded-sources", "expanded-assets",
                 "point-bindings", "forecast-bindings", "forecast-oracle", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--baseline-build", type=Path)
    parser.add_argument("--reviewed-build", type=Path)
    parser.add_argument("--topography-sources", type=Path)
    parser.add_argument("--topography-assets", type=Path)
    args = parser.parse_args()
    if bool(args.baseline_build) != bool(args.reviewed_build):
        parser.error("The context-isolation comparison takes both build paths")
    if bool(args.topography_sources) != bool(args.topography_assets) or args.topography_sources and not args.baseline_build:
        parser.error("Topographic discovery requires both of its paths and the context-isolation comparison")
    if args.baseline_build and any(args.out.resolve().is_relative_to(path.resolve())
                                   for path in (args.baseline_build, args.reviewed_build)):
        raise ValueError("Context audit requires an output outside the locked research builds")
    if args.topography_assets and args.out.resolve().is_relative_to(args.topography_assets.resolve()):
        raise ValueError("Context audit requires an output outside the topographic source store")
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
    output = {"forecast": {"status": forecast["status"], "receipt_sha256": _hash(forecast_path)},
              "geography": {"status": geography["status"], "receipt_sha256": _hash(points)}}
    if args.baseline_build:
        fixed_sha = _hash(args.fixed_sources)
        government = verify_government_origins(geography, args.expanded_sources, args.expanded_assets,
                                              args.fixed_sources, args.fixed_assets)
        isolated = compare_context_isolation(args.baseline_build, args.reviewed_build, fixed_sha, government, forecast)
        if args.topography_sources:
            isolated["topographic_discovery"] = verify_topographic_leads(fixed_sha, args.topography_sources,
                                                                        args.topography_assets)
        path = args.out / "context_comparability.json"
        path.write_text(canonical_json(isolated) + "\n", encoding="utf-8")
        output["comparability"] = {"status": isolated["status"], "receipt_sha256": _hash(path)}
    print(canonical_json(output))


if __name__ == "__main__":
    main()

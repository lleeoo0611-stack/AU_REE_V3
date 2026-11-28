"""Verify official point responses in a separate research source expansion."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .contracts import EntityKind, canonical_json
from .documents import DocumentCache, DocumentError
from .geography import SITE_ROLES, _decision, _edition, infrastructure_site_compatibility
from .sources import ContentStore, IntegrityError, validate_edition_proofs


OFFICIAL_POINTS_SCHEMA = "government-native-point-candidates-v1"


@dataclass(frozen=True, slots=True)
class OfficialPointLayer:
    host: str
    path: str
    id_field: str
    role_field: str
    project_field: str | None
    commodity_field: str
    crs: str
    datum: str


_POINT_DATASETS = {
    "WA_MINEDEX": OfficialPointLayer(
        "services.slip.wa.gov.au", "/public/rest/services/SLIP_Public_Services/Industry_and_Mining/MapServer/0",
        "site_code", "site_type_", "proj_code", "site_commo", "EPSG:7844", "GDA2020",
    ),
    "GA_CRITICAL_DEPOSITS": OfficialPointLayer(
        "services.ga.gov.au", "/gis/rest/services/AustralianCriticalMineralsOperatingMinesAndDeposits/MapServer/7",
        "objectid", "status", None, "commodities", "EPSG:4283", "GDA94",
    ),
    "GA_OZMIN": OfficialPointLayer(
        "services.ga.gov.au", "/gis/earthresource/ows",
        "ENO", "CLASS", None, "COMMODITY_CODES", "EPSG:4283", "GDA94",
    ),
}
_NATIVE_SITE_ROLES = {"mine": "OFFICIAL_MINE_POINT", "deposit": "OFFICIAL_DEPOSIT_POINT",
                      "mineral deposit": "OFFICIAL_DEPOSIT_POINT", "prospect": "PROSPECT"}


def _point_index(payload, dataset):
    layer = _POINT_DATASETS[dataset]
    if dataset == "GA_OZMIN":
        reported = payload.get("crs", {}).get("properties", {}).get("name", "")
        authorised_codes = {layer.crs, "urn:ogc:def:crs:EPSG::" + layer.crs.split(":")[1]}
        crs_matches = reported in authorised_codes
        valid_type = payload.get("type") == "FeatureCollection"
        def point(feature):
            return feature["properties"], feature["geometry"]
    else:
        reference = payload.get("spatialReference", {})
        codes = {f"EPSG:{reference[key]}" for key in ("wkid", "latestWkid") if key in reference}
        crs_matches = codes == {layer.crs}
        valid_type = payload.get("geometryType") == "esriGeometryPoint"
        def point(feature):
            geometry = feature["geometry"]
            return feature["attributes"], {"type": "Point", "coordinates": [geometry["x"], geometry["y"]]}
    total = payload.get("numberMatched")
    truncated = payload.get("exceededTransferLimit") or (type(total) is int and total != len(payload.get("features", ())))
    if not valid_type or not crs_matches or truncated:
        raise IntegrityError("Official feature response geometry, CRS or completeness differs from its contract")
    found = {}
    for feature in payload["features"]:
        attributes, geometry = point(feature)
        identity = attributes[layer.id_field]
        coordinates = geometry.get("coordinates", ())
        if (not identity or identity in found or geometry.get("type") != "Point" or len(coordinates) != 2
                or not all(type(value) in {int, float} and math.isfinite(value) for value in coordinates)
                or not (110 <= coordinates[0] <= 156 and -45 <= coordinates[1] <= -10)):
            raise IntegrityError("Official native point has ambiguous identity or unsupported coordinates")
        found[identity] = attributes, geometry
    return found


def _is_government_layer(url, dataset, *, metadata=False):
    layer = _POINT_DATASETS[dataset]
    parsed = urlsplit(url)
    expected = layer.path if metadata or dataset == "GA_OZMIN" else layer.path + "/query"
    return parsed.scheme == "https" and parsed.hostname == layer.host and parsed.path == expected


def _known_point(case, edition, expanded, content, timezone, cache):
    dataset = case["dataset"]
    if (case["project_id"] not in edition["project_ids"]
            or not all(_is_government_layer(edition[key], dataset) for key in ("url", "final_url"))):
        raise DocumentError("SCIENTIFIC_CONFLICT", "OFFICIAL_LAYER_OR_PROJECT_DISCOVERY_SCOPE_MISMATCH")
    if edition["sha256"] not in cache:
        proofs = validate_edition_proofs(content, edition, timezone)
        decision = _decision(edition, expanded, proofs)
        if decision.status != "ELIGIBLE" or json.loads(canonical_json(decision.to_dict())) != edition["temporal_decision"]:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "EXACT_OFFICIAL_EDITION_NOT_PROVEN_BY_CUTOFF")
        points = _point_index(json.loads(content.read(edition["sha256"])), dataset)
        cache[edition["sha256"]] = points, decision
    points, decision = cache[edition["sha256"]]
    if case["native_id"] not in points:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "NATIVE_POINT_ID_NOT_LOCATED")
    attributes, geometry = points[case["native_id"]]
    return attributes, geometry, decision


def _native_issuer_scope(case, fixed, content, timezone, views):
    from .development import bind_native_quote

    link = case["issuer_point_link"]
    source = _edition(fixed, link["source_sha256"], case["project_id"], "pdf")
    if source["sha256"] not in views:
        decision = _decision(source, {"cutoff": case["cutoff"]}, validate_edition_proofs(content, source, timezone))
        if decision.status != "ELIGIBLE":
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ISSUER_PROJECT_LINK_NOT_PROVEN_BY_CUTOFF")
        views[source["sha256"]] = DocumentCache().pdf(content.read(source["sha256"]), source["sha256"], link["page"])
    document = views[source["sha256"]]
    anchors = [
        bind_native_quote(document, source["source_key"], link["page"], link[key], key.upper())
        for key in ("project_quote", "distinctive_subsite_quote")
    ]
    marker = link["same_site_marker"]
    if not marker or not all(re.search(rf"\b{re.escape(marker)}\b", value, re.I)
                            for value in (case["native_site_name"], anchors[1].quote)):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "DISTINCTIVE_NATIVE_SUBSITE_IDENTIFIER_UNBOUND")
    return {"source_sha256": source["sha256"], "source_url": source["url"],
            "role": "ISSUER_PROJECT_AND_DISTINCTIVE_SUBSITE_LINK", "anchors": [item.to_dict() for item in anchors]}


def _point_candidate(case, edition, expanded, content, timezone, cache, fixed, fixed_content, views):
    dataset = case["dataset"]
    layer = _POINT_DATASETS[dataset]
    properties, geometry, decision = _known_point(case, edition, expanded, content, timezone, cache)
    native_role = properties.get(layer.role_field)
    role = _NATIVE_SITE_ROLES.get((native_role or "").casefold(), "OTHER_OFFICIAL_LOCATION")
    raw_accuracy = properties.get("ACCURACY_M")
    accuracy = raw_accuracy if type(raw_accuracy) in {float, int} and math.isfinite(raw_accuracy) and raw_accuracy >= 0 else None
    native_project = (properties.get(layer.project_field) or "").strip() if layer.project_field else None
    commodity = properties.get(layer.commodity_field) or ""
    name = next((properties[key] for key in ("site_title", "projectname", "OCCURRENCE_NAME")
                 if properties.get(key)), "")
    linkage = None
    issues = ["FEATURE_SURVEY_OR_APPLICABILITY_DATE_UNBOUND"]
    if accuracy is None:
        issues.append("METRIC_POSITIONAL_ACCURACY_UNDISCLOSED")
    if case.get("native_project_id") and native_project != case["native_project_id"]:
        issues.append("NATIVE_GOVERNMENT_PROJECT_ID_MISMATCH")
    if case.get("issuer_point_link") and "NATIVE_GOVERNMENT_PROJECT_ID_MISMATCH" not in issues:
        linkage = _native_issuer_scope({**case, "cutoff": expanded["cutoff"], "native_site_name": name},
                                       fixed, fixed_content, timezone, views)
    if linkage is None:
        issues.append("EXACT_FIXED_PROJECT_TO_GOVERNMENT_SITE_LINK_UNBOUND")
    if role not in SITE_ROLES:
        issues.append("FEATURE_ROLE_OUTSIDE_MINE_OR_DEPOSIT_POINT")
    if case.get("expected_commodity_token") and case["expected_commodity_token"] not in re.split(r"[,\s]+", commodity):
        issues.append("NATIVE_COMMODITY_SCOPE_MISMATCH")
    rejected = {"NATIVE_GOVERNMENT_PROJECT_ID_MISMATCH", "FEATURE_ROLE_OUTSIDE_MINE_OR_DEPOSIT_POINT",
                "NATIVE_COMMODITY_SCOPE_MISMATCH"}.intersection(issues)
    raw_date = properties.get("extract_da")
    observed_extract = datetime.fromtimestamp(raw_date / 1000, UTC).date().isoformat() if type(raw_date) is int else None
    precision = {"positional_accuracy_m": accuracy, "public_visibility": properties.get("point_conf")}
    compatibility = infrastructure_site_compatibility({
        "source_sha256": edition["sha256"], "crs": layer.crs, "geometry": geometry, "location_role": role,
        "locator": {"native_id": properties[layer.id_field]}, "project_link": linkage, "precision": precision,
        "temporal_decision": None,
    })
    return {
        "source_sha256": edition["sha256"], "source_url": edition["url"], "receipt_sha256": edition["receipt_sha256"],
        "location_role": role, "raw_role": native_role,
        "native_entity_kind": {"OFFICIAL_MINE_POINT": EntityKind.MINE.value,
                               "OFFICIAL_DEPOSIT_POINT": EntityKind.DEPOSIT.value}.get(role),
        "native_feature_status": properties.get("site_stage") or properties.get("status"),
        "native_feature_subtype": properties.get("site_sub_t"),
        "native_locator": {"field": layer.id_field, "native_id": properties[layer.id_field], "native_project_id": native_project},
        "registered_relationship": "DISTINCTIVE_ISSUER_SITE_CHECKED" if linkage else "DISCOVERY_ASSOCIATION_ONLY",
        "native_name": name,
        "commodity": commodity, "crs": layer.crs, "datum": layer.datum, "geometry": geometry,
        "geometry_sha256": sha256(canonical_json(geometry).encode()).hexdigest(),
        "precision": precision, "infrastructure_compatibility": compatibility,
        "document_temporality": decision.to_dict(), "feature_effective_date": None,
        "source_extract_date": observed_extract, "feature_classification": "OFFICIAL_NATIVE_POINT",
        "project_link": linkage, "state": "SHADOW", "open_issues": issues,
        "proposed_site_use": "REJECTED" if rejected else "EVIDENCE_INSUFFICIENT",
        "formal_census": False, "formal_infrastructure": False,
        "native_cross_reference": properties.get("OTHER_ID"),
    }


def review_official_point_candidates(bindings_path, fixed_manifest, fixed_assets, expanded_manifest, expanded_assets, out):
    """Reopen official point editions in a separate expansion journal, with no site admission."""
    fixed_path, expanded_path, out = map(Path, (fixed_manifest, expanded_manifest, out))
    if (out.resolve() in {fixed_path.resolve(), expanded_path.resolve(), Path(bindings_path).resolve()}
            or any(out.resolve().is_relative_to(Path(assets).resolve()) for assets in (fixed_assets, expanded_assets))):
        raise ValueError("Point candidate receipt requires an output outside the locked sources")
    fixed_bytes, expanded_bytes = fixed_path.read_bytes(), expanded_path.read_bytes()
    fixed, expanded, spec = json.loads(fixed_bytes), json.loads(expanded_bytes), json.loads(Path(bindings_path).read_bytes())
    fixed_sha, expanded_sha = (sha256(body).hexdigest() for body in (fixed_bytes, expanded_bytes))
    if (spec["schema"] != OFFICIAL_POINTS_SCHEMA or spec["fixed_manifest_sha256"] != fixed_sha
            or spec["expanded_manifest_sha256"] != expanded_sha or expanded.get("base_manifest_sha256") != fixed_sha
            or len({e["sha256"] for e in fixed["editions"]}) != 144
            or any(fixed[key] != expanded[key] for key in ("schema", "profile_id", "cutoff", "timezone"))
            or expanded.get("purpose") != "EXPANDED_MANIFEST_GOVERNMENT_GEOGRAPHY_SHADOW"):
        raise IntegrityError("Government review requires separate pinned inputs and the fixed research cohort")
    fixed_editions, expanded_editions = defaultdict(list), {}
    for edition in fixed["editions"]:
        fixed_editions[edition["sha256"]].append(edition)
    for edition in expanded["editions"]:
        if edition["sha256"] in fixed_editions:
            raise IntegrityError("Expansion source bodies overlap the fixed research cohort")
        expanded_editions[edition["receipt_sha256"]] = edition
    timezone = ZoneInfo(fixed["timezone"])
    content, fixed_content = ContentStore(Path(expanded_assets)), ContentStore(Path(fixed_assets))
    cases, decoded, views = {}, {}, {}
    for case in spec["cases"]:
        if case["case_id"] in cases or case["dataset"] not in _POINT_DATASETS:
            raise ValueError("Government point bindings require a unique case and a recognized authority")
        row = {"case_id": case["case_id"], "project_id": case["project_id"], "dataset": case["dataset"]}
        cases[case["case_id"]] = row
        try:
            edition = expanded_editions[case["receipt_sha256"]]
            row.update(_point_candidate(case, edition, expanded, content, timezone, decoded,
                                        fixed_editions, fixed_content, views))
        except (OSError, KeyError):
            row.update(state="SOURCE_UNAVAILABLE", open_issues=["EXACT_GOVERNMENT_SOURCE_OR_FIELD_UNAVAILABLE"])
        except DocumentError as error:
            row.update(state=error.outcome, open_issues=[str(error)])
        except IntegrityError as error:
            row.update(state="SCIENTIFIC_CONFLICT", open_issues=[str(error)])

    relations = []
    for edge in spec["cross_authority_comparisons"]:
        first, second = (cases[case_id] for case_id in edge)
        if first["state"] != "SHADOW" or second["state"] != "SHADOW":
            continue
        native_link = (first["native_cross_reference"] == second["native_locator"]["native_id"]
                       and first["native_cross_reference"] is not None)
        agree = first["location_role"] == second["location_role"]
        relations.append({"case_ids": edge, "exact_native_cross_reference": native_link,
                          "source_roles_agree": agree, "scope_equivalence": False,
                          "verdict": "CROSS_AUTHORITY_ROLE_MISMATCH" if native_link and not agree
                          else "SHADOW_NATIVE_LINK" if native_link else "NATIVE_CROSS_REFERENCE_UNBOUND"})
    metadata = []
    for pin in spec["layer_metadata"]:
        edition = expanded_editions[pin["receipt_sha256"]]
        dataset = pin["dataset"]
        if not all(_is_government_layer(edition[key], dataset, metadata=True) for key in ("url", "final_url")):
            raise IntegrityError("Layer metadata comes from another authority")
        proofs = validate_edition_proofs(content, edition, timezone)
        body = json.loads(content.read(edition["sha256"]))
        decision = _decision(edition, expanded, proofs)
        if body.get("geometryType") != "esriGeometryPoint" or decision.status != "ELIGIBLE":
            raise IntegrityError("Official point metadata lacks exact time proof or the source role")
        metadata.append({
            "dataset": dataset, "source_sha256": edition["sha256"], "source_url": edition["url"],
            "receipt_sha256": edition["receipt_sha256"], "geometry_type": body["geometryType"],
            "layer_name": body["name"], "declared_fields": [field["name"] for field in body["fields"]],
            "snapshot_period": "2025-12" if "as at December 2025" in body.get("description", "") else None,
            "snapshot_kind": "LAYER_PUBLICATION_DESCRIPTION", "feature_measurement_date": None,
            "temporal_decision": decision.to_dict(),
        })
    report = {
        "schema": OFFICIAL_POINTS_SCHEMA, "scope": "EXPANDED_MANIFEST", "cutoff": fixed["cutoff"], "timezone": fixed["timezone"],
        "fixed_manifest_sha256": fixed_sha, "expanded_manifest_sha256": expanded_sha,
        "bindings_sha256": sha256(Path(bindings_path).read_bytes()).hexdigest(),
        "expanded_unique_sha256": len({edition["sha256"] for edition in expanded["editions"]}),
        "decoded_point_responses": len(decoded), "decoded_point_rows": sum(len(points) for points, _ in decoded.values()),
        "cases": list(cases.values()), "cross_authority_relations": relations, "layer_metadata": metadata,
        "formal_census": 0, "formal_infrastructure": 0,
        "status": "FAIL" if any(row["state"] == "SCIENTIFIC_CONFLICT" for row in cases.values())
        else "PASS" if cases and all(row["state"] == "SHADOW" for row in cases.values()) else "BLOCKED",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_json(report) + "\n", encoding="utf-8")
    return report

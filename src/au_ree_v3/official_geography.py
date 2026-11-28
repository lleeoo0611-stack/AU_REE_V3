"""Verify official point responses in a separate research source expansion."""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from .contracts import EntityKind, canonical_json
from .documents import DocumentCache, DocumentError
from .geography import SITE_ROLES, _decision, _edition, infrastructure_site_compatibility, official_point_role
from .sources import ContentStore, IntegrityError, fetch_public, validate_edition_proofs


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

for _layer_number in (5, 6):
    _deposits = _POINT_DATASETS["GA_CRITICAL_DEPOSITS"]
    _POINT_DATASETS[f"GA_CRITICAL_LAYER_{_layer_number}"] = replace(
        _deposits, path=_deposits.path[:-1] + str(_layer_number),
    )


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
    return parsed.scheme == "https" and parsed.netloc == layer.host and parsed.path == expected


HISTORICAL_POINT_SCHEMA = "historical-government-point-recovery-v1"
_HISTORICAL_POINTS = "spatial_point_quality.csv"
_HISTORICAL_POINTS_SHA = "11ede840da25bb5ec3e50f69a986a03dc5dbfe26baecb51a489de826eed1e777"
_HISTORICAL_LAYER_LABELS = {
    "WA_MINEDEX": ("WA_MINEDEX",),
    "GA2025": ("GA_CRITICAL_LAYER_5", "GA_CRITICAL_LAYER_6", "GA_CRITICAL_DEPOSITS"),
}


def _historical_routes(rows, assets):
    urls = defaultdict(set)
    for asset in assets:
        if asset["role"] == "snapshot":
            urls[asset["sha256"]].add(asset["url"])
    routes = defaultdict(list)
    for row in rows:
        label = row["dataset"]
        if label not in _HISTORICAL_LAYER_LABELS:
            continue
        digest = row["source_sha256"]
        approved = []
        for url in sorted(urls[digest]):
            parsed = urlsplit(url)
            if (label == "WA_MINEDEX" and parsed.scheme == "https"
                    and parsed.netloc == "public-services.slip.wa.gov.au"
                    and parsed.path == "/public/rest/services/SLIP_Public_Services/Industry_and_Mining_WFS/MapServer/0/query"):
                approved.append(("WA_MINEDEX", url))
            approved.extend((key, url) for key in _HISTORICAL_LAYER_LABELS[label] if _is_government_layer(url, key))
        if len(approved) != 1:
            raise IntegrityError(f"V2 point source lacks one archived government layer: {digest}")
        dataset, url = approved[0]
        routes[dataset, digest, url].append(row)
    return routes


def _point_query(dataset, rows):
    layer = _POINT_DATASETS[dataset]
    ids = sorted({row["native_feature_id"] for row in rows})
    pattern = r"S[0-9]+" if dataset == "WA_MINEDEX" else r"[0-9]+"
    if not ids or any(not re.fullmatch(pattern, native_id) for native_id in ids):
        raise IntegrityError("V2 point locator does not match the layer's native identifier")
    values = ",".join(f"'{native_id}'" if dataset == "WA_MINEDEX" else native_id for native_id in ids)
    params = {"f": "json", "where": f"{layer.id_field} IN ({values})", "outFields": "*",
              "outSR": layer.crs.split(":")[1], "returnGeometry": "true"}
    return f"https://{layer.host}{layer.path}/query?{urlencode(params)}"


def _point_recovery(row, dataset, points, capture):
    layer = _POINT_DATASETS[dataset]
    locator = row["native_feature_id"]
    identity = locator if dataset == "WA_MINEDEX" else int(locator)
    row_crs = {"7844": "EPSG:7844", "GDA94": "EPSG:4283"}.get(row["export_crs"], row["export_crs"])
    result = {
        "project_id": row["project_id"], "native_id": locator, "native_id_field": layer.id_field,
        "historical_locator": row["locator"], "historical_crs": row_crs, "capture_crs": layer.crs,
        "source_sha256": capture["source_sha256"], "state": "CANDIDATE",
        "feature_effective_date": None, "location_role": None, "native_role": None, "geometry": None,
        "comparison": ("SOURCE_UNAVAILABLE" if capture["status"] != "FETCHED" else
                       "INVALID_LAYER_RESPONSE" if "layer_error" in capture else "NATIVE_FEATURE_NOT_LOCATED"),
    }
    if identity in points:
        properties, geometry = points[identity]
        original = [Decimal(row[key]) for key in ("longitude", "latitude")]
        same_precision = all(
            Decimal(str(now)).quantize(expected) == expected
            for now, expected in zip(geometry["coordinates"], original, strict=True)
        )
        native_role = properties.get(layer.role_field)
        result.update(geometry=geometry, native_role=native_role, location_role=official_point_role(native_role),
                      historical_coordinates=[str(value) for value in original],
                      comparison="CRS_DIFFERS" if row_crs != layer.crs else
                      "MATCH_AT_HISTORICAL_PRECISION" if same_precision else "COORDINATES_DIFFER")
    return result


def _read_point_capture(dataset, url, store, recorded, fetcher):
    if recorded is None:
        response = fetcher(url, "GET", 18, 12_000_000)
        digest = store.put(response.payload) if response.status == "FETCHED" and response.payload else None
        capture = {"requested_url": url, "final_url": response.final_url, "acquired_at": response.at,
                   "status": response.status, "http_status": response.http_status, "source_sha256": digest}
        if response.status == "FETCHED" and digest is None:
            capture["status"] = "EMPTY_RESPONSE"
    else:
        capture = {key: recorded[key] for key in
                   ("requested_url", "final_url", "acquired_at", "status", "http_status", "source_sha256")}
        if capture["requested_url"] != url:
            raise IntegrityError("Recorded point capture disagrees with the frozen native query")
    if capture["status"] != "FETCHED":
        return capture, {}
    payload = store.read(capture["source_sha256"])
    try:
        if not _is_government_layer(capture["final_url"], dataset):
            raise IntegrityError("Point capture redirected outside its government layer")
        return capture, _point_index(json.loads(payload), dataset)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        capture["layer_error"] = str(error)
        return capture, {}


def replay_historical_points(v2_root, store_path, *, prior=None, fetcher=fetch_public):
    """Recover published managed-layer features without assigning release eligibility."""
    from .migration.baseline import BASELINES, PinnedRepository, checked_csv
    from .migration.project_registry import ARCHIVE, ASSETS, RESEARCH_CUTOFF, TIMEZONE, V2_ARCHIVE_FILES

    if store_path.resolve().is_relative_to(v2_root.resolve()):
        raise ValueError("Write point captures outside the V2 reference checkout")
    repo = PinnedRepository(v2_root)
    data = repo.read("engineering", f"{ARCHIVE}/{_HISTORICAL_POINTS}")
    assets = repo.read("engineering", f"{ARCHIVE}/{ASSETS}")
    if data is None or assets is None:
        raise ValueError("Frozen V2 point rows or asset manifest unavailable")
    if sha256(data).hexdigest() != _HISTORICAL_POINTS_SHA or sha256(assets).hexdigest() != V2_ARCHIVE_FILES[ASSETS][1]:
        raise IntegrityError("V2 government point reference fingerprints differ")

    store = ContentStore(store_path)
    cutoff = RESEARCH_CUTOFF.isoformat()
    routes = _historical_routes(checked_csv(data), json.loads(assets))
    recorded = {}
    if prior is not None:
        if prior["schema"] != HISTORICAL_POINT_SCHEMA or prior["archive_sha256"] != _HISTORICAL_POINTS_SHA:
            raise IntegrityError("Recorded point receipt does not refer to the fixed V2 archive")
        recorded = {row["requested_url"]: row for row in prior["captures"]}
        expected = {_point_query(dataset, rows) for (dataset, _, _), rows in routes.items()}
        if len(recorded) != len(prior["captures"]) or set(recorded) != expected:
            raise IntegrityError("Recorded point receipt does not cover the exact native queries")
    captures, results = [], []
    for (dataset, digest, archive_url), rows in sorted(routes.items()):
        url = _point_query(dataset, rows)
        capture, points = _read_point_capture(dataset, url, store, recorded.get(url), fetcher)
        if capture["source_sha256"]:
            capture["temporal_decision"] = _decision(
                {"sha256": capture["source_sha256"], "publication_date": None,
                 "acquired_at": capture["acquired_at"], "reacquired_at": None},
                {"cutoff": cutoff}, (),
            ).to_dict()
        capture.update(dataset=dataset, historical_sha256=digest, historical_url=archive_url)
        captures.append(capture)
        results.extend(_point_recovery(row, dataset, points, capture) for row in rows)
    matched = sum(row["comparison"] == "MATCH_AT_HISTORICAL_PRECISION" for row in results)
    return {
        "schema": HISTORICAL_POINT_SCHEMA, "archive_sha256": _HISTORICAL_POINTS_SHA,
        "archive_commit": BASELINES["refs"]["engineering"], "cutoff": cutoff, "timezone": TIMEZONE,
        "status": "PASS" if results and matched == len(results) else "BLOCKED",
        "counts": {"native_routes": len(routes), "historical_points": len(results), "coordinate_matches": matched,
                   "native_features_reread": sum(row["geometry"] is not None for row in results),
                   "formal_coordinates": 0, "formal_infrastructure": 0},
        "captures": captures, "points": sorted(results, key=lambda item: item["project_id"]),
    }


def _known_point(case, edition, expanded, content, timezone, cache):
    dataset = case["dataset"]
    if (case["project_id"] not in edition["project_ids"]
            or not all(_is_government_layer(edition[key], dataset) for key in ("url", "final_url"))):
        raise DocumentError("SCIENTIFIC_CONFLICT", "OFFICIAL_LAYER_OR_PROJECT_DISCOVERY_SCOPE_MISMATCH")
    proofs = validate_edition_proofs(content, edition, timezone)
    decision = _decision(edition, expanded, proofs)
    if decision.status != "ELIGIBLE" or json.loads(canonical_json(decision.to_dict())) != edition["temporal_decision"]:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "EXACT_OFFICIAL_EDITION_NOT_PROVEN_BY_CUTOFF")
    cache_key = edition["sha256"], dataset
    if cache_key not in cache:
        cache[cache_key] = _point_index(json.loads(content.read(edition["sha256"])), dataset)
    points = cache[cache_key]
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
    role = official_point_role(native_role)
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


def review_official_point_candidates(bindings_path, fixed_manifest, fixed_assets, expanded_manifest, expanded_assets,
                                     out=None):
    """Reopen official point editions in a separate expansion journal, with no site admission."""
    fixed_path, expanded_path = map(Path, (fixed_manifest, expanded_manifest))
    if out is not None:
        out = Path(out)
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
        "decoded_point_responses": len(decoded), "decoded_point_rows": sum(len(points) for points in decoded.values()),
        "cases": list(cases.values()), "cross_authority_relations": relations, "layer_metadata": metadata,
        "formal_census": 0, "formal_infrastructure": 0,
        "status": "FAIL" if any(row["state"] == "SCIENTIFIC_CONFLICT" for row in cases.values())
        else "PASS" if cases and all(row["state"] == "SHADOW" for row in cases.values()) else "BLOCKED",
    }
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(canonical_json(report) + "\n", encoding="utf-8")
    return report


def official_geography_evidence(bindings_path, fixed_manifest, fixed_assets, expanded_manifest,
                               expanded_assets) -> tuple[dict, ...]:
    """Expose validated native feature context without publishing coordinates or site cells."""
    report = review_official_point_candidates(bindings_path, fixed_manifest, fixed_assets, expanded_manifest,
                                              expanded_assets)
    disagreements = {case_id for relation in report["cross_authority_relations"]
                     if relation["verdict"] == "CROSS_AUTHORITY_ROLE_MISMATCH" for case_id in relation["case_ids"]}
    records = []
    for row in report["cases"]:
        if (row["state"] != "SHADOW" or row["proposed_site_use"] != "EVIDENCE_INSUFFICIENT"
                or row["document_temporality"]["status"] != "ELIGIBLE" or not row["native_name"]):
            continue
        layer = _POINT_DATASETS[row["dataset"]]
        accuracy = row["precision"]["positional_accuracy_m"]
        native_id = row["native_locator"]
        context = {
            "source_scope": "EXPANDED_MANIFEST", "expanded_manifest_sha256": report["expanded_manifest_sha256"],
            "source_kind": "OFFICIAL_STRUCTURED_POINT", "source_capture_url": row["source_url"],
            "capture_date": row["document_temporality"]["earliest_proven_public_date"],
            "feature_effective_date": "UNKNOWN_CONTEXT", "temporal_status": "ELIGIBLE",
            "temporal_decision_id": row["document_temporality"]["decision_id"],
            "native_role": row["raw_role"], "geometry_type": row["geometry"]["type"],
            "crs": row["crs"], "datum": row["datum"],
            "positional_accuracy_m": str(accuracy) if accuracy is not None else "UNKNOWN_CONTEXT",
            "registered_relationship": row["registered_relationship"],
        }
        if row["source_extract_date"]:
            context["source_extract_date"] = row["source_extract_date"]
        if row["precision"]["public_visibility"]:
            context["public_visibility"] = row["precision"]["public_visibility"]
        if native_id["native_project_id"]:
            context["native_project_id"] = native_id["native_project_id"]
        if row["case_id"] in disagreements:
            context["cross_authority_comparison"] = "CROSS_AUTHORITY_ROLE_MISMATCH"
        records.append({
            "project_id": row["project_id"], "data_role": row["location_role"], "state": "SHADOW",
            "label": row["native_name"], "reason": ", ".join(row["open_issues"]),
            "source_sha256": row["source_sha256"], "source_capture_sha256": row["receipt_sha256"],
            "source_url": f"https://{layer.host}{layer.path}", "publication_date": "",
            "locator": f"{row['dataset']}: {native_id['field']}={native_id['native_id']}",
            "quote": row["native_name"], "scope": row["registered_relationship"], "context": context,
        })
    return tuple(records)

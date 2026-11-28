"""Locate public tenement geometries without substituting them for mine sites."""

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .contracts import canonical_json
from .contracts.temporal import _date_bounds, evaluate_temporal
from .documents import DocumentCache, DocumentError
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_MANIFEST, validate_edition_proofs


SCHEMA = "wa-tenement-evidence-v1"
INFRASTRUCTURE_SCHEMA = "infrastructure-evidence-compatibility-v1"
ROLE = "LEGAL_TENEMENT_BOUNDARY"
SITE_ROLES = {"OFFICIAL_MINE_POINT", "OFFICIAL_DEPOSIT_POINT"}
INFRASTRUCTURE_LAYERS = {"I1": "ROAD", "I2": "RAIL", "I3": "POWER_GRID", "I4": "PORT"}
_LAYER = "CurrentTenements"
_LICENCE = re.compile(r"[A-Z]{1,3} \d{1,3}/\d{1,6}")

_POINT_ROLES = {"mine": "OFFICIAL_MINE_POINT", "deposit": "OFFICIAL_DEPOSIT_POINT",
                "mineral deposit": "OFFICIAL_DEPOSIT_POINT", "prospect": "PROSPECT"}


def official_point_role(label: str | None) -> str:
    """Preserve the distinction between native sites and other published locations."""
    return _POINT_ROLES.get((label or "").casefold(), "OTHER_OFFICIAL_LOCATION")


def _decision(edition, manifest, proofs, effective_date=None):
    return evaluate_temporal(
        source_sha256=edition["sha256"], cutoff=manifest["cutoff"],
        publication_date=edition["publication_date"], acquired_at=edition["acquired_at"],
        reacquired_at=edition["reacquired_at"], effective_date=effective_date,
        observation_role="HISTORICAL_MEASUREMENT" if effective_date else "DOCUMENT", evidence=proofs,
    )


def _edition(editions, sha, project, kind):
    matches = [e for e in editions.get(sha, ()) if project in e["project_ids"] and e["kind"] == kind]
    if not matches:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "EXACT_EDITION_PROJECT_LINK_MISSING")
    return min(matches, key=lambda e: (e["acquired_at"], e["edition_id"]))


def _read_layer(payload, titles, expected_crs):
    if not titles or any(not _LICENCE.fullmatch(title) for title in titles):
        raise ValueError("Expected public WA licence identifiers")
    joined = ",".join(f"'{title}'" for title in sorted(titles))
    query = (f"SELECT TENID,FMT_TENID,TYPE,SURVSTATUS,TENSTATUS,EXTRACT_DA FROM {_LAYER} "
             f"WHERE FMT_TENID IN ({joined})")
    # Give OGR a read-only local view of exactly the bytes the CAS verified.
    with TemporaryDirectory() as directory:
        path = Path(directory) / "tenements.zip"
        path.write_bytes(payload)
        result = subprocess.run(
            ["ogrinfo", "-ro", "-json", "-features", "-dialect", "OGRSQL", "-sql", query,
             f"/vsizip/{path}/{_LAYER}.shp"], check=True, capture_output=True, timeout=60,
        )
    layer, = json.loads(result.stdout)["layers"]
    identity = layer["geometryFields"][0]["coordinateSystem"]["projjson"].get("id", {})
    crs = f'{identity.get("authority")}:{identity.get("code")}'
    if crs != expected_crs:
        raise IntegrityError(f"Government layer CRS {crs} differs from binding {expected_crs}")
    features = defaultdict(list)
    for feature in layer["features"]:
        title = feature["properties"]["FMT_TENID"]
        if feature.get("geometry", {}).get("type") not in {"Polygon", "MultiPolygon"}:
            raise IntegrityError("Tenement geometry is missing or is not a polygon")
        features[title].append(feature)
    return crs, features


def _issuer_link(binding, title, manifest, editions, content, cache, timezone):
    issuer = _edition(editions, binding["issuer_sha256"], binding["project_id"], "pdf")
    decision = _decision(issuer, manifest, validate_edition_proofs(content, issuer, timezone))
    if decision.status != "ELIGIBLE":
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ISSUER_LINK_NOT_TIME_ELIGIBLE")
    page = binding["issuer_page"]
    doc = cache.pdf(content.read(issuer["sha256"]), issuer["sha256"], page)
    for first, last in binding["issuer_lines"]:
        lines = [doc.locate(page, line=line).text for line in range(first, last + 1)]
        quote = " ".join(" ".join(lines).split())
        if (binding["project_marker"].casefold() in quote.casefold()
                and re.search(rf"(?<!\w){re.escape(title.replace(' ', ''))}(?!\d)", quote)):
            return {"source_sha256": issuer["sha256"], "url": issuer["final_url"],
                    "locator": f"PDF page {page}, lines {first}-{last}", "quote": quote,
                    "temporal_decision": asdict(decision)}
    raise DocumentError("EVIDENCE_INSUFFICIENT", "PROJECT_LICENCE_QUOTE_MISSING")


def _locations(binding, manifest, editions, content, cache, timezone, layer):
    government = _edition(editions, binding["layer_sha256"], binding["project_id"], "zip")
    proofs = validate_edition_proofs(content, government, timezone)
    crs, features = layer
    for title in binding["licences"]:
        row = {"project_id": binding["project_id"], "licence": title, "source_sha256": government["sha256"],
               "source_url": government["final_url"], "location_role": ROLE, "crs": crs,
               "formal_indicator": None}
        try:
            row["project_link"] = _issuer_link(binding, title, manifest, editions, content, cache, timezone)
        except (DocumentError, FileNotFoundError, IntegrityError) as error:
            reason = error.reason if isinstance(error, DocumentError) else "ISSUER_EDITION_READBACK_FAILED"
            yield {**row, "status": "CANDIDATE", "reason": reason}
            continue
        matches = features[title]
        if len(matches) != 1:
            yield {**row, "status": "CANDIDATE", "reason": "LICENCE_ABSENT_OR_AMBIGUOUS_IN_LAYER"}
            continue
        feature, = matches
        properties = feature["properties"]
        effective = datetime.strptime(properties["EXTRACT_DA"], "%Y/%m/%d").date().isoformat()
        decision = _decision(government, manifest, proofs, effective)
        geometry = feature["geometry"]
        row.update({
            "effective_date": effective, "temporal_decision": asdict(decision),
            "locator": {"member": f"{_LAYER}.shp", "feature_id": feature["fid"],
                        "native_id_field": "TENID", "native_id": properties["TENID"]},
            "licence_type": properties["TYPE"], "licence_status": properties["TENSTATUS"],
            "precision": {"survey_status": properties["SURVSTATUS"], "positional_accuracy_m": None},
            "geometry": geometry, "geometry_sha256": sha256(canonical_json(geometry).encode()).hexdigest(),
        })
        row["status"] = "VERIFIED_BOUNDARY" if decision.status == "ELIGIBLE" else "CANDIDATE"
        row["reason"] = "SITE_LOCATION_EVIDENCE_MISSING" if decision.status == "ELIGIBLE" else decision.reason
        yield row


def inventory_tenements(bindings_path: Path, sources_path: Path, store: Path, out: Path) -> dict:
    """Produce a replayable, exact-edition receipt for governed geometry candidates."""
    bindings_bytes, manifest_bytes = bindings_path.read_bytes(), sources_path.read_bytes()
    spec, manifest = json.loads(bindings_bytes), json.loads(manifest_bytes)
    if spec["schema"] != SCHEMA or manifest["schema"] != SOURCE_MANIFEST:
        raise ValueError("Unsupported tenement or source manifest contract")
    if (spec["cutoff"], spec["timezone"]) != (manifest["cutoff"], manifest["timezone"]):
        raise ValueError("Tenement bindings and source manifest require the same cutoff and timezone")
    timezone, content, cache = ZoneInfo(spec["timezone"]), ContentStore(store), DocumentCache()
    editions, titles, rows = defaultdict(list), defaultdict(set), []
    for edition in manifest["editions"]:
        editions[edition["sha256"]].append(edition)
    for binding in spec["bindings"]:
        titles[binding["layer_sha256"]].update(binding["licences"])
    runtime, unavailable = None, {}
    executable = shutil.which("ogrinfo")
    if executable:
        try:
            version = subprocess.run([executable, "--version"], check=True, capture_output=True,
                                     text=True, timeout=10).stdout.strip()
            runtime = {"version": version, "sha256": sha256(Path(executable).read_bytes()).hexdigest()}
        except (OSError, subprocess.SubprocessError):
            pass
    if runtime:
        layers = {}
        for sha, requested in sorted(titles.items()):
            try:
                layers[sha] = _read_layer(content.read(sha), requested, spec["layers"][sha])
            except (OSError, subprocess.SubprocessError, IntegrityError) as error:
                unavailable[sha] = type(error).__name__
        for binding in spec["bindings"]:
            sha = binding["layer_sha256"]
            if sha in layers:
                try:
                    rows.extend(_locations(binding, manifest, editions, content, cache, timezone, layers[sha]))
                except (DocumentError, IntegrityError, OSError) as error:
                    unavailable[f'{sha}:{binding["project_id"]}'] = type(error).__name__
    if unavailable:
        status, reason = "BLOCKED", "ONE_OR_MORE_GOVERNMENT_SOURCE_EDITION_CHECKS_BLOCKED"
    elif runtime:
        status, reason = "PASS", "VERIFIED_OFFICIAL_BOUNDARY_INVENTORY"
    else:
        status, reason = "BLOCKED", "GDAL_SHAPEFILE_RUNTIME_OR_LAYER_UNAVAILABLE"
    counts = {"verified_boundaries": sum(row["status"] == "VERIFIED_BOUNDARY" for row in rows),
              "candidates": sum(row["status"] == "CANDIDATE" for row in rows),
              "formal_indicators": 0}
    result = {"schema": SCHEMA, "status": status, "reason": reason, "cutoff": spec["cutoff"],
              "timezone": spec["timezone"], "runtime": runtime, "counts": counts, "locations": rows,
              "unbound_projects": spec["unbound_projects"], "unavailable": unavailable,
              "bindings_sha256": sha256(bindings_bytes).hexdigest(),
              "sources_sha256": sha256(manifest_bytes).hexdigest()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


def _date_upper_bound(value):
    """Preserve a feature's original date precision at the cutoff."""
    try:
        return _date_bounds(value)[1]
    except ValueError:
        return None


def infrastructure_site_compatibility(location: dict) -> dict:
    """Assess a geographic feature's suitability as a mine or deposit origin."""
    role = location.get("location_role")
    geometry = location.get("geometry") or {}
    locator = location.get("locator") or {}
    temporal = location.get("temporal_decision") or {}
    effective_token = location.get("feature_effective_date")
    effective = _date_upper_bound(effective_token)
    cutoff_token = temporal.get("cutoff")
    cutoff = _date_upper_bound(cutoff_token) if isinstance(cutoff_token, str) and len(cutoff_token) == 10 else None
    coordinates = geometry.get("coordinates")
    point_coordinates = (
        isinstance(coordinates, (tuple, list)) and len(coordinates) in {2, 3}
        and all(type(value) in (int, float) and math.isfinite(value) for value in coordinates)
    )
    precision = (location.get("precision") or {}).get("positional_accuracy_m")
    missing = [
        name for name, present in (
            ("location_role", role),
            ("source_sha256", location.get("source_sha256")),
            ("native_feature_id", locator.get("native_id")),
            ("crs", location.get("crs")),
            ("geometry", geometry.get("type")),
            ("point_coordinates", point_coordinates),
            ("feature_effective_date", effective),
            ("eligible_temporal_decision", temporal.get("status") == "ELIGIBLE" and temporal.get("decision_id")
             and temporal.get("source_sha256") == location.get("source_sha256")
             and temporal.get("observation_role") == "HISTORICAL_MEASUREMENT"
             and effective is not None and cutoff is not None and effective <= cutoff
             and temporal.get("effective_date") == effective_token),
            ("positional_precision", type(precision) in (int, float) and math.isfinite(precision) and precision >= 0),
            ("verified_project_location_link", location.get("project_link")),
        ) if not present
    ]
    incompatible = [
        name for name, present in (
            ("location_role", role and role not in SITE_ROLES),
            ("geometry_type", geometry.get("type") not in {None, "Point"}),
        ) if present
    ]
    if incompatible:
        return {
            "status": "NOT_COMPARABLE", "reason": "GEOGRAPHIC_ROLE_OR_TYPE_IS_NOT_A_MINE_OR_DEPOSIT_POINT",
            "incompatible_dimensions": incompatible, "missing_dimensions": missing,
        }
    if missing:
        return {
            "status": "INSUFFICIENT_CONTEXT", "reason": "LOCATION_EVIDENCE_INCOMPLETE",
            "incompatible_dimensions": [], "missing_dimensions": missing,
        }
    return {
        "status": "SITE_CANDIDATE", "reason": "SITE_READY_FOR_OFFICIAL_SOURCE_AND_INFRASTRUCTURE_REVIEW",
        "incompatible_dimensions": [], "missing_dimensions": [],
    }


def _check_tenement_feature(feature: dict, editions: dict, cutoff: str) -> dict:
    digest = feature["source_sha256"]
    project = feature["project_id"]
    geometry = feature.get("geometry")
    temporal = feature.get("temporal_decision") or {}
    if feature["location_role"] != ROLE or not any(
        entry["kind"] == "zip" and project in entry["project_ids"]
        and entry["final_url"] == feature["source_url"]
        for entry in editions.get(digest, ())
    ):
        raise IntegrityError("Tenement feature conflicts with its source role or Project linkage")
    if feature["status"] == "VERIFIED_BOUNDARY":
        if not (
            feature["crs"] in {"EPSG:4283", "EPSG:7844"}
            and geometry and geometry["type"] in {"Polygon", "MultiPolygon"}
            and (feature.get("locator") or {}).get("native_id")
            and feature["geometry_sha256"] == sha256(canonical_json(geometry).encode()).hexdigest()
            and temporal.get("source_sha256") == digest and temporal.get("cutoff") == cutoff
            and temporal.get("status") == "ELIGIBLE" and temporal.get("decision_id")
        ):
            raise IntegrityError("Verified tenement feature conflicts with its source or geometry evidence")
    elif feature["status"] != "CANDIDATE":
        raise IntegrityError("Unsupported tenement feature status")
    return {
        "source_sha256": digest, "licence": feature["licence"], "location_role": feature["location_role"],
        "source_url": feature["source_url"], "feature_status": feature["status"],
        "native_locator": feature.get("locator"), "crs": feature["crs"],
        "geometry_type": geometry["type"] if geometry else None,
        "geometry_sha256": feature.get("geometry_sha256"),
        "temporal_decision_id": temporal.get("decision_id"), "precision": feature.get("precision"),
        "site_compatibility": infrastructure_site_compatibility(feature),
    }


def evaluate_infrastructure_candidates(tenements_path: Path, sources_path: Path, out: Path) -> dict:
    """Report I1–I4 evidence gaps on the fixed cohort and preserve legal geometry roles."""
    tenements_path, sources_path, out = Path(tenements_path), Path(sources_path), Path(out)
    tenements_bytes, sources_bytes = tenements_path.read_bytes(), sources_path.read_bytes()
    inventory, manifest = json.loads(tenements_bytes), json.loads(sources_bytes)
    sources_sha = sha256(sources_bytes).hexdigest()
    if not (
        inventory["schema"] == SCHEMA and manifest["schema"] == SOURCE_MANIFEST
        and inventory["sources_sha256"] == sources_sha
        and (inventory["cutoff"], inventory["timezone"]) == (manifest["cutoff"], manifest["timezone"])
    ):
        raise ValueError("Tenement inventory belongs to a different source manifest or research cutoff")
    editions, features = defaultdict(list), defaultdict(list)
    projects = {row["project_id"] for row in manifest["projects"]}
    for edition in manifest["editions"]:
        editions[edition["sha256"]].append(edition)
    for feature in inventory["locations"]:
        project = feature["project_id"]
        if project not in projects:
            raise IntegrityError("Tenement feature belongs to a Project outside the research cohort")
        features[project].append(_check_tenement_feature(feature, editions, manifest["cutoff"]))
    rows = []
    for project in sorted(projects):
        reviewed = features.get(project, [])
        rows.append({
            "project_id": project, "site_status": "INSUFFICIENT_CONTEXT",
            "site_reason": "LEGAL_BOUNDARIES_CANNOT_LOCATE_MINE_OR_DEPOSIT" if reviewed
            else inventory["unbound_projects"].get(project, "REPRESENTATIVE_MINE_OR_DEPOSIT_POINT_UNAVAILABLE"),
            "geometry_evidence": reviewed,
            "criteria": {
                criterion: {
                    "status": "INSUFFICIENT_CONTEXT", "value": None,
                    "required_geometry": layer, "measure": "STRAIGHT_LINE_SPATIAL_PROXIMITY",
                    "missing_dimensions": ["verified_native_site_point", f"verified_official_{layer.lower()}_geometry"],
                } for criterion, layer in INFRASTRUCTURE_LAYERS.items()
            },
        })
    result = {
        "schema": INFRASTRUCTURE_SCHEMA, "status": "PASS" if inventory["status"] == "PASS" else "BLOCKED",
        "cutoff": manifest["cutoff"], "timezone": manifest["timezone"], "sources_sha256": sources_sha,
        "tenement_inventory_sha256": sha256(tenements_bytes).hexdigest(),
        "project_count": len(rows), "legal_boundaries": sum(
            feature["feature_status"] == "VERIFIED_BOUNDARY" for project in features.values() for feature in project
        ),
        "verified_mine_or_deposit_points": 0, "formal_indicators": 0, "project_criteria": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result

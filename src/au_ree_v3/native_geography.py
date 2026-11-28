"""Read source-native state government locations before any representative-site selection."""

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

from .contracts import canonical_json
from .development import bind_native_quote
from .documents import DocumentCache, DocumentError
from .geography import _decision, _edition, infrastructure_site_compatibility, official_point_role
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_MANIFEST, validate_edition_proofs


SCHEMA = "native-government-location-review-v1"
_SARIG_PATH = "/MineralDepositDetails.aspx"
_SARIG_SERVICE_PATH = "/industry/geological-survey/products-and-services/map-viewers-databases-and-services/data-services"
_NT_STRIKE_PATH = "/contents/prod/Downloads/Mineral_Deposits/MINERALOCCURRENCES_csv.zip"
_NSW_RESOURCES_PATH = "/invest-nsw/investment-pathways/nsw-priority-metals/rare-earth-elements"
_ENDPOINTS = {
    "SA_SARIG_MINDEP": ({"minerals.sarig.sa.gov.au"}, _SARIG_PATH),
    "SA_SARIG_SERVICES": ({"energymining.sa.gov.au", "www.energymining.sa.gov.au"}, _SARIG_SERVICE_PATH),
    "NT_STRIKE_MODAT": ({"geoscience.nt.gov.au", "www.geoscience.nt.gov.au"}, _NT_STRIKE_PATH),
    "NSW_RESOURCES": ({"www.resources.nsw.gov.au"}, _NSW_RESOURCES_PATH),
}


def _field(text):
    return {"text": text.text.strip(), "locator": text.locator}


def _exact(fields, description):
    matches = list(fields)
    if len(matches) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", f"{description}_ABSENT_OR_AMBIGUOUS")
    return matches[0]


def _html_labels(document, labels):
    fields = document.fragments
    return {key: _field(_exact((value for label, value in zip(fields, fields[1:]) if label.text.strip() == title), key))
            for key, title in labels.items()}


def _sarig_mindep(content, cache, edition, identity):
    document = cache.html(content, edition["sha256"])
    fields = _html_labels(document, {
        "native_id": "Deposit Number:", "native_name": "Deposit Name:", "role": "Deposit Type:",
        "status": "Status:", "datum": "Datum", "method": "Coordinate Source", "zone": "MGA Zone",
        "easting": "MGA Easting", "northing": "MGA Northing", "longitude": "Long Degrees Decimal",
        "latitude": "Lat Degrees Decimal", "accuracy": "Horizontal Accuracy",
    })
    value = {key: field["text"] for key, field in fields.items()}
    if value["native_id"] != identity:
        raise IntegrityError("SARIG response content belongs to a different native deposit")
    longitude, latitude = map(Decimal, (value["longitude"], value["latitude"]))
    easting, northing = map(Decimal, (value["easting"], value["northing"]))
    if (value["datum"] != "GDA2020" or value["zone"] not in {"52", "53", "54"}
            or not (129 <= longitude <= 142 and -39 <= latitude <= -25)
            or not (100_000 <= easting <= 900_000 and 5_000_000 <= northing <= 8_000_000)):
        raise IntegrityError("SARIG axes, datum or MGA zone conflict with the native location profile")
    return {"native_name": value["native_name"], "raw_role": value["role"],
            "location_role": official_point_role(value["role"]), "crs": "EPSG:7844", "datum": value["datum"],
            "geometry": {"type": "Point", "coordinates": [float(longitude), float(latitude)]},
            "axes": [{"axis_order": ["longitude", "latitude"], "unit": "degree", "crs": "EPSG:7844"},
                     {"axis_order": ["easting", "northing"], "unit": "m", "crs": f"EPSG:78{value['zone']}"}],
            "native_fields": fields, "native_locator": {"native_id": identity, "field": "Deposit Number"},
            "positional_accuracy": {"raw": value["accuracy"], "unit": None, "method": value["method"]}}


def _nt_modat(content, cache, edition, identity):
    member = "MINERALOCCURRENCES.csv"
    with ZipFile(BytesIO(content)) as archive:
        member_info = _exact((item for item in archive.infolist() if item.filename == member), "MODAT_MEMBER")
        if member_info.file_size > 8 * 1024 * 1024:
            raise IntegrityError("MODAT member exceeds the bounded source profile")
        payload = archive.read(member_info)
    digest = sha256(payload).hexdigest()
    table = cache.csv(payload, digest)
    row = _exact((index for index in range(2, len(table.rows) + 1)
                  if table.locate(index, "MODAT_ID").text == identity), "MODAT_ID")
    fields = {key: _field(table.locate(row, name)) for key, name in {
        "native_id": "MODAT_ID", "native_name": "NAME", "role": "STATUS", "commodity": "COM_MAJOR",
        "easting": "EASTING", "northing": "NORTHING", "zone": "ZONE_MGA",
        "method": "LOC_METHOD", "accuracy": "LOC_ACCURY",
    }.items()}
    value = {key: field["text"] for key, field in fields.items()}
    return {"native_name": value["native_name"], "raw_role": value["role"],
            "location_role": official_point_role(value["role"]),
            "crs": None, "datum": None, "geometry": None,
            "axes": [{"axis_order": ["easting", "northing"], "unit": "m", "crs": None}],
            "native_locator": {"member": member, "member_sha256": digest, "native_id": identity, "field": "MODAT_ID"},
            "native_fields": fields,
            "positional_accuracy": {"raw": value["accuracy"], "unit": None, "method": value["method"]}}


def _nsw_locality(content, cache, edition, identity):
    text = _exact((fragment for fragment in cache.html(content, edition["sha256"]).fragments
                   if identity in fragment.text), "NSW_PROJECT_LOCALITY")
    return {"native_name": "Toongi", "raw_role": "Project locality statement", "location_role": "PROJECT_LOCALITY_TEXT",
            "crs": None, "datum": None, "geometry": None, "axes": [],
            "native_locator": {"native_id": None, "locator": text.locator},
            "native_fields": {"location_context": {"text": identity, "locator": text.locator}},
            "positional_accuracy": None}


_READ_NATIVE = {"SA_SARIG_MINDEP": _sarig_mindep, "NT_STRIKE_MODAT": _nt_modat, "NSW_RESOURCES": _nsw_locality}


def _read_edition(store, edition, dataset, manifest, identity=None):
    hosts, path = _ENDPOINTS[dataset]
    for url in (edition["url"], edition["final_url"]):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.path != path
                or dataset == "SA_SARIG_MINDEP" and parse_qs(parsed.query) != {"DEPOSIT_NO": [identity]}):
            raise IntegrityError("Exact source URL differs from the approved government feature endpoint")
    proofs = validate_edition_proofs(store, edition, ZoneInfo(manifest["timezone"]))
    decision = _decision(edition, manifest, proofs)
    if canonical_json(decision.to_dict()) != canonical_json(edition["temporal_decision"]):
        raise IntegrityError("Government edition temporal decision differs from exact capture receipts")
    return store.read(edition["sha256"]), decision.to_dict()


def _read_issuer(case, editions, store, manifest, cache, name):
    binding = case["issuer_context"]
    edition = _edition(editions, binding["source_sha256"], case["project_id"], "pdf")
    proofs = validate_edition_proofs(store, edition, ZoneInfo(manifest["timezone"]))
    decision = _decision(edition, manifest, proofs)
    if decision.status != "ELIGIBLE":
        raise DocumentError("EVIDENCE_INSUFFICIENT", "ISSUER_SCOPE_EDITION_NOT_TIME_ELIGIBLE")
    document = cache.pdf(store.read(edition["sha256"]), edition["sha256"], binding["page"])
    anchor = bind_native_quote(document, edition["source_key"], binding["page"], binding["quote"], "PROJECT_LOCALITY_CONTEXT")
    if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", anchor.quote, re.IGNORECASE) is None:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "NATIVE_SITE_NAME_NOT_READ_IN_ELIGIBLE_ISSUER_CONTEXT")
    return {"role": "ISSUER_NAMED_PROJECT_OR_SUBSITE_CONTEXT", "source_url": edition["url"],
            "anchor": anchor.to_dict(), "temporal_decision": decision.to_dict()}


def _review_case(case, edition, expanded, store, issuer_inputs, cache):
    if case["project_id"] not in edition["project_ids"]:
        raise IntegrityError("Government edition belongs to a different acquisition Project")
    native, decision = _read_edition(store, edition, case["dataset"], expanded, case["native_token"])
    parsed = _READ_NATIVE[case["dataset"]](native, cache, edition, case["native_token"])
    issues = ["EXACT_FEATURE_NOT_PUBLIC_BY_CUTOFF"] if decision["status"] != "ELIGIBLE" else []
    context = None
    try:
        context = _read_issuer(case, *issuer_inputs, cache, parsed["native_name"])
    except (DocumentError, OSError):
        issues.append("ISSUER_NAMED_SITE_CONTEXT_UNBOUND")
    location = {**parsed, "source_sha256": edition["sha256"], "locator": parsed["native_locator"],
                "feature_effective_date": None, "temporal_decision": None,
                "precision": {"positional_accuracy_m": None}, "project_link": None}
    compatibility = infrastructure_site_compatibility(location)
    issues.extend(["REPRESENTATIVE_SITE_SCOPE_UNBOUND", "FEATURE_APPLICABILITY_DATE_UNBOUND"])
    if parsed["crs"] is None:
        issues.append("EXACT_FEATURE_CRS_UNBOUND")
    if parsed["positional_accuracy"] is None or parsed["positional_accuracy"]["unit"] is None:
        issues.append("METRIC_POSITIONAL_ACCURACY_UNBOUND")
    if compatibility["status"] == "NOT_COMPARABLE":
        issues.append("NATIVE_ROLE_IS_NOT_A_MINE_OR_DEPOSIT_POINT")
    return {**parsed, "source_sha256": edition["sha256"], "source_url": edition["url"],
            "capture_receipt_sha256": edition["receipt_sha256"], "document_temporality": decision,
            "feature_effective_date": None, "coordinate_transform": None, "issuer_context": context,
            "compatibility": compatibility, "issues": issues, "status": "REVIEWED_CANDIDATE",
            "reason": "EVIDENCE_INSUFFICIENT"}


def _pinned_inputs(paths):
    spec_body, stable_body, probe_body = (path.read_bytes() for path in paths)
    spec, stable, probe = map(json.loads, (spec_body, stable_body, probe_body))
    stable_sha, probe_sha = (sha256(body).hexdigest() for body in (stable_body, probe_body))
    baseline_projects = [item["project_id"] for item in stable["projects"]]
    probe_projects = [item["project_id"] for item in probe["projects"]]
    if (spec["schema"] != SCHEMA or stable_sha != spec["baseline_manifest_sha256"]
            or probe_sha != spec["probe_manifest_sha256"] or stable["schema"] != SOURCE_MANIFEST
            or baseline_projects != probe_projects or len(set(baseline_projects)) != len(baseline_projects)
            or any(stable[key] != probe[key] for key in ("schema", "profile_id", "cutoff", "timezone"))):
        raise IntegrityError("Native government bindings require their exact separate Project-scoped source editions")
    cases = spec["cases"]
    if (not cases or len({case["case_id"] for case in cases}) != len(cases)
            or any(case["project_id"] not in baseline_projects or case["dataset"] not in _READ_NATIVE for case in cases)):
        raise IntegrityError("Native government bindings require unique reviewed cases in the pinned Project cohort")
    fingerprints = {"baseline_manifest_sha256": stable_sha, "probe_manifest_sha256": probe_sha,
                    "bindings_sha256": sha256(spec_body).hexdigest()}
    return spec, stable, probe, fingerprints


def _review_cases(cases, editions, probe, store, issuer_inputs, cache):
    results = []
    for case in cases:
        result = {key: case[key] for key in ("case_id", "project_id", "dataset")}
        try:
            result.update(_review_case(case, editions[case["receipt_sha256"]], probe, store, issuer_inputs, cache))
        except DocumentError as error:
            result.update(status="BLOCKED", reason=error.outcome, issues=[error.reason])
        except (OSError, KeyError):
            result.update(status="BLOCKED", reason="SOURCE_UNAVAILABLE", issues=["EXACT_EDITION_OR_FIELD_MISSING"])
        except (IntegrityError, BadZipFile, InvalidOperation) as error:
            result.update(status="FAIL", reason="SCIENTIFIC_CONFLICT", issues=[str(error)])
        results.append(result)
    return results


def review_native_locations(bindings, baseline_manifest, baseline_assets, probe_manifest, probe_assets, out):
    """Return a hash-pinned readback of candidate locations, preserving the science cutoff."""
    paths = [Path(path) for path in (bindings, baseline_manifest, probe_manifest)]
    output = Path(out)
    if output.resolve() in {path.resolve() for path in paths} or any(
        output.resolve().is_relative_to(Path(asset).resolve()) for asset in (baseline_assets, probe_assets)
    ):
        raise ValueError("Native location receipt must be written outside its locked inputs")
    spec, stable, probe, fingerprints = _pinned_inputs(paths)
    stable_editions = defaultdict(list)
    for edition in stable["editions"]:
        stable_editions[edition["sha256"]].append(edition)
    editions = {edition["receipt_sha256"]: edition for edition in probe["editions"]}
    issuer_inputs = stable_editions, ContentStore(Path(baseline_assets)), stable
    store, cache = ContentStore(Path(probe_assets)), DocumentCache()
    license_evidence = _sarig_license(spec["license_evidence"], editions, store, probe, cache)
    results = _review_cases(spec["cases"], editions, probe, store, issuer_inputs, cache)
    states = Counter(row["status"] for row in results)
    outcomes = set(states) | {license_evidence["status"]}
    status = "FAIL" if "FAIL" in outcomes else "BLOCKED" if "BLOCKED" in outcomes else "PASS"
    receipt = {"schema": SCHEMA, "status": status,
               "cutoff": stable["cutoff"], "timezone": stable["timezone"], **fingerprints,
               "project_count": len(stable["projects"]), "case_counts": states, "cases": results,
               "acquisition_counts": probe["counts"], "sarig_license_evidence": license_evidence,
               "formal_coordinates_added": 0, "formal_infrastructure_indicators_added": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(canonical_json(receipt) + "\n")
    return receipt


def _sarig_license(pin, editions, store, manifest, cache):
    try:
        edition = editions[pin["receipt_sha256"]]
        payload, _ = _read_edition(store, edition, "SA_SARIG_SERVICES", manifest)
        label = "(CC BY 4.0)."
        text = _exact((fragment for fragment in cache.html(payload, edition["sha256"]).fragments
                       if fragment.text.strip() == label), "SARIG_DATA_SERVICE_LICENSE")
    except (KeyError, OSError, DocumentError):
        return {"status": "BLOCKED", "reason": "LICENSE_OR_EXACT_ATTESTATION_UNAVAILABLE"}
    except IntegrityError as error:
        return {"status": "FAIL", "reason": str(error)}
    return {"status": "PASS", "source_url": edition["url"], "source_sha256": edition["sha256"], **_field(text)}

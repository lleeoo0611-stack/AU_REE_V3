"""Extract the identity cohort and search pointers from fixed V2 release artifacts."""

from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ..registry import (
    CATALOG_SCHEMA, CENSUS_COLUMNS_SCHEMA, PROFILE_SCHEMA, PROJECT_ID_COLUMN, REGISTRY_SCHEMA,
    load_census_columns, load_profile, load_registry,
)
from .baseline import BASELINES, PinnedRepository, checked_csv

ARCHIVE = "results/ree-project-census/coverage-locked-20261125"
CENSUS = "REE project census.csv"
RELEASE = "census_release.json"
SELECTION = "census_selection.csv"
IDENTITY_ROLES = "census_identity_roles.csv"
ASSETS = "asset_manifest.json"
PROVENANCE = "field_provenance.csv"
ARCHIVED_CUTOFF = "2026-11-13"
PROFILE_ID = "au-ree-2026-11-28"
RESEARCH_CUTOFF = date(2026, 11, 28)
TIMEZONE = "Asia/Taipei"
COUNTS = (93, 51)
V2_ARCHIVE_FILES = {
    item["path"].rsplit("/", 1)[1]: (item["git_blob_sha"], item["sha256"])
    for item in BASELINES["pins"]["archived-census-2026-11-13"]
    if item["path"] in {f"{ARCHIVE}/{name}" for name in (CENSUS, RELEASE, ASSETS, PROVENANCE)}
}
V2_ARCHIVE_FILES.update({
    SELECTION: ("b269d45a0fdbb78095adab3b3d2fb79f2ca76e69",
                "d8cb35a3b66e9cfd1862bb479178f6a82280dbd53c923fe46d163c07ef6bebec"),
    IDENTITY_ROLES: ("204b0059cd0fbce2cfca31430a372f115bd6446b",
                     "34b50b1cf3d63dbb319906f9ebb528b1fe7f4b732dd8da8dec30e23f798a82d4"),
})
COMPONENT_FILES = {
    "project_registry": "project_registry_2026-11-28.json",
    "source_seed_catalog": "source_seeds_2026-11-28.json",
    "census_columns": "census_columns_v1.json",
}
RAW_DOCUMENT_KINDS = {
    "pdf": "pdf", "html": "html", "discovery_html": "html", "government_csv": "csv",
    "wa_dasc_tenements_zip": "zip",
}
OFFICIAL_PUBLISHERS = {"www.asx.com.au": "ASX", "announcements.asx.com.au": "ASX",
                       "www.londonstockexchange.com": "London Stock Exchange"}
ISSUER_DISTRIBUTION_HOSTS = {"api.investi.com.au", "hermes-cdn.investorhub.com", "wcsecure.weblink.com.au"}
THIRD_PARTY_HOSTS = {"www.annualreports.com", "www.marketindex.com.au", "data-api.marketindex.com.au",
                     "company-announcements.afr.com", "cdn-api.markitdigital.com"}


class ReferenceUnavailable(ValueError):
    """A pinned reference commit or artifact was unavailable."""


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _archive_inputs(v2_root: Path) -> dict[str, bytes]:
    repository = PinnedRepository(v2_root)
    engineering = BASELINES["refs"]["engineering"]
    if repository.resolve(f"{engineering}^{{commit}}") != engineering:
        raise ReferenceUnavailable("The pinned V2 engineering commit is unavailable")
    result = {}
    for name, (blob_sha, digest) in V2_ARCHIVE_FILES.items():
        path = f"{ARCHIVE}/{name}"
        data = repository.read("engineering", path)
        if data is None:
            raise ReferenceUnavailable(f"The pinned V2 artifact is unavailable: {path}")
        actual = repository.resolve(f"{engineering}:{path}")
        if actual != blob_sha or sha256(data).hexdigest() != digest:
            raise ValueError(f"V2 reference fingerprint differs: {path}")
        result[name] = data
    return result


def _project_rows(reference: dict[str, bytes]) -> tuple[list[dict], list[str], list[dict], list[dict]]:
    census = checked_csv(reference[CENSUS])
    selection = checked_csv(reference[SELECTION])
    release = json.loads(reference[RELEASE])
    ids = [row[PROJECT_ID_COLUMN] for row in census]
    columns = list(census[0])
    expected_science_commit = BASELINES["refs"]["science"]
    if (release["cutoff"] != ARCHIVED_CUTOFF or release["code"]["commit"] != expected_science_commit
            or release["census_csv_sha256"] != sha256(reference[CENSUS]).hexdigest()):
        raise ValueError("The archived Census disagrees with its cutoff, release code or fingerprint")
    if (
        (len(ids), len(columns)) != COUNTS or len(set(ids)) != len(ids)
        or sorted(ids) != sorted(release["selected_census_ids"])
        or sorted(ids) != sorted(row["project_id"] for row in selection)
        or any(row["canonical_project_id"] != row["project_id"] or row["status"] != "SELECTED"
               for row in selection)
    ):
        raise ValueError("Archived Census, publication and canonical identity selections disagree")
    aliases = defaultdict(list)
    archived_aliases = []
    for row in checked_csv(reference[IDENTITY_ROLES]):
        if row["role"] != "DOCUMENTED_CANONICAL_ALIAS":
            continue
        link = {
            key: row[key] for key in ("related_id", "role", "status", "source_sha256", "locator")
        }
        if row["project_id"] in ids:
            aliases[row["project_id"]].append(link)
        else:
            archived_aliases.append({"project_id": row["project_id"], **link})
    projects = [
        {
            "project_id": row[PROJECT_ID_COLUMN], "name": row["專案名稱"], "state": row["州省地區"],
            "developer": row["開發商"] or None, "listing_code": row["ASX代碼"] or None,
            "identity_links": sorted(aliases[row[PROJECT_ID_COLUMN]], key=lambda item: item["related_id"]),
        } for row in census
    ]
    archived_aliases.sort(key=lambda row: (row["project_id"], row["related_id"]))
    return sorted(projects, key=lambda row: row["project_id"]), columns, census, archived_aliases


def _host_policy(url: str) -> tuple[str, str | None]:
    host = urlsplit(url).hostname or ""
    if host in OFFICIAL_PUBLISHERS:
        return "OFFICIAL_EXCHANGE", OFFICIAL_PUBLISHERS[host]
    if host.endswith(".gov.au"):
        return "OFFICIAL_GOVERNMENT", None
    if host in ISSUER_DISTRIBUTION_HOSTS:
        return "ISSUER_DISTRIBUTION", None
    if host in THIRD_PARTY_HOSTS:
        return "THIRD_PARTY_ARCHIVED_POINTER", None
    return "ARCHIVED_PUBLISHER_UNVERIFIED", None


def _document_index(asset_manifest: list[dict]) -> dict[str, list[dict]]:
    result = defaultdict(list)
    for record in asset_manifest:
        if record["role"] != "snapshot" or record["kind"] not in RAW_DOCUMENT_KINDS:
            continue
        url = record["url"]
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname and parts.username is None and not parts.fragment:
            result[url].append(record)
    return result


def _seed_rows(reference: dict[str, bytes], census: list[dict]) -> list[dict]:
    document_index = _document_index(json.loads(reference[ASSETS]))
    associations = defaultdict(lambda: defaultdict(set))
    for row in census:
        url = row["資料來源URL"]
        if url not in document_index:
            raise ValueError(f"Archived primary source has no original-document record: {row[PROJECT_ID_COLUMN]}")
        associations[url][row[PROJECT_ID_COLUMN]].add(CENSUS)
    for row in checked_csv(reference[PROVENANCE]):
        for url in re.split(r";(?=https://)", row["source_url"]):
            if url in document_index:
                associations[url][row["project_id"]].add(PROVENANCE)

    seeds = []
    ranked = defaultdict(list)
    for url, projects in sorted(associations.items()):
        records = document_index[url]
        kinds = {RAW_DOCUMENT_KINDS[row["kind"]] for row in records}
        classification, publisher = _host_policy(url)
        # A URL that once served HTML and PDF can still be expected to serve the document.
        kind = "pdf" if "pdf" in kinds else min(kinds)
        same_kind = [r for r in records if RAW_DOCUMENT_KINDS[r["kind"]] == kind]
        seed = {
            "source_key": "seed-" + sha256(url.encode()).hexdigest()[:24],
            "url": url, "publisher": publisher, "kind": kind, "channel": classification,
            "project_ids": sorted(projects),
            "historical_sha256": sorted({row["sha256"] for row in same_kind}),
            "historical_source_keys": sorted({row["key"] for row in same_kind}),
            "reference": f"{ARCHIVE}/{ASSETS}",
            "association_references": {
                project_id: sorted(f"{ARCHIVE}/{name}" for name in refs)
                for project_id, refs in sorted(projects.items())
            },
            "priority_by_project": {},
        }
        seeds.append(seed)
        for project_id, refs in projects.items():
            rank = (CENSUS not in refs, classification == "THIRD_PARTY_ARCHIVED_POINTER", kind != "pdf",
                    len(projects), url)
            ranked[project_id].append((rank, seed))
    for project_id, candidates in ranked.items():
        for priority, (_, seed) in enumerate(sorted(candidates, key=lambda item: item[0]), start=1):
            seed["priority_by_project"][project_id] = priority
    return seeds


def _references() -> dict:
    return {
        "artifact_commit": BASELINES["refs"]["engineering"],
        "historical_science_commit": BASELINES["refs"]["science"],
        "historical_cutoff": ARCHIVED_CUTOFF,
        "artifacts": {
            f"{ARCHIVE}/{name}": {"git_blob_sha": blob, "sha256": digest}
            for name, (blob, digest) in V2_ARCHIVE_FILES.items()
        },
    }


def _components(reference: dict[str, bytes]) -> dict[str, dict]:
    projects, columns, census, archived_aliases = _project_rows(reference)
    return {
        "project_registry": {
            "schema_version": REGISTRY_SCHEMA,
            "historical_reference": _references(),
            "projects": projects,
            "out_of_cohort_identity_links": archived_aliases,
        },
        "source_seed_catalog": {
            "schema_version": CATALOG_SCHEMA,
            "historical_reference": _references(),
            "seed_role": "HISTORICAL_SEARCH_POINTER_ONLY",
            "seed_urls": _seed_rows(reference, census),
        },
        "census_columns": {
            "schema_version": CENSUS_COLUMNS_SCHEMA,
            "historical_reference": _references(),
            "columns": columns,
        },
    }


def _fresh_profile_payload(components: dict[str, dict]) -> dict:
    return {
        "schema_version": PROFILE_SCHEMA,
        "profile_id": PROFILE_ID,
        "research_cutoff": RESEARCH_CUTOFF.isoformat(),
        "timezone": TIMEZONE,
        "project_count": COUNTS[0],
        "census_column_count": COUNTS[1],
        "inputs": {
            key: {"path": COMPONENT_FILES[key], "sha256": sha256(_json_bytes(payload)).hexdigest()}
            for key, payload in components.items()
        },
        "legacy_replay": "LEGACY_DIAGNOSTIC_ONLY",
    }


def _outside_reference(v2_root: Path, output: Path) -> None:
    if output.resolve().is_relative_to(v2_root.resolve()):
        raise ValueError("Write registry artifacts outside the read-only V2 reference checkout")
    output.mkdir(parents=True, exist_ok=True)


def generate_profile(v2_root: Path | str, output: Path | str) -> Path:
    """Write a candidate profile and components into a review directory."""
    v2_root, output = Path(v2_root), Path(output)
    _outside_reference(v2_root, output)
    components = _components(_archive_inputs(v2_root))
    profile = output / "au_ree_2026-11-28.json"
    files = {output / COMPONENT_FILES[key]: _json_bytes(payload) for key, payload in components.items()}
    files[profile] = _json_bytes(_fresh_profile_payload(components))
    for path, data in files.items():
        if path.exists() and path.read_bytes() != data:
            raise ValueError(f"Generated profile would replace a different artifact: {path}")
    for path, data in files.items():
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(data)
    return profile


def verify_reference(v2_root: Path | str, profile_path: Path | str, output: Path | str) -> dict:
    """Verify immutable identity inputs and write the F0 acceptance receipt."""
    v2_root, output = Path(v2_root), Path(output)
    _outside_reference(v2_root, output)
    receipt = {"gate": "F0", "reference": _references()}
    try:
        profile = load_profile(profile_path)
        registry = load_registry(profile.registry_path)
        columns = load_census_columns(profile.census_columns_path)
        fresh = _components(_archive_inputs(v2_root))
        if Path(profile_path).read_bytes() != _json_bytes(_fresh_profile_payload(fresh)):
            raise ValueError("Fresh research profile differs from the checked 93-project reference")
        paths = {
            "project_registry": profile.registry_path, "source_seed_catalog": profile.source_catalog_path,
            "census_columns": profile.census_columns_path,
        }
        for name, path in paths.items():
            if path.read_bytes() != _json_bytes(fresh[name]):
                raise ValueError(f"Versioned component differs from the V2 reference extraction: {name}")
        seeds = fresh["source_seed_catalog"]["seed_urls"]
        cohort_ids = {project.project_id for project in registry.projects}
        by_project = Counter(project for seed in seeds for project in seed["project_ids"])
        if set(by_project) != cohort_ids:
            raise ValueError("Search pointer associations do not cover the fixed cohort")
        receipt.update(
            status="PASS",
            profile_id=profile.profile_id,
            profile_sha256=profile.sha256,
            research_cutoff=profile.research_cutoff.isoformat(),
            timezone=profile.timezone.key,
            project_count=len(registry.projects),
            project_registry_sha256=registry.sha256,
            historical_alias_count=sum(len(p.identity_links) for p in registry.projects),
            out_of_cohort_historical_alias_count=len(fresh["project_registry"]["out_of_cohort_identity_links"]),
            census_column_count=len(columns),
            seed_urls=len(seeds),
            seed_projects=len(by_project),
            seed_counts_by_project=dict(sorted(by_project.items())),
            seed_url_associations=sum(by_project.values()),
            seed_channels=dict(sorted(Counter(seed["channel"] for seed in seeds).items())),
            seed_status="HISTORICAL_SEARCH_POINTER_ONLY",
        )
    except ReferenceUnavailable as exc:
        receipt.update(status="BLOCKED", reason=str(exc))
    except (KeyError, ValueError, TypeError, OSError) as exc:
        receipt.update(status="FAIL", reason=str(exc))
    (output / "registry_verification.json").write_bytes(_json_bytes(receipt))
    return receipt

"""Load the versioned research cohort and identity-only reference data."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

PROFILE_SCHEMA = "fresh-research-baseline-v1"
REGISTRY_SCHEMA = "project-registry-v1"
CATALOG_SCHEMA = "source-seed-catalog-v1"
CENSUS_COLUMNS_SCHEMA = "census-column-contract-v1"
PROJECT_ID_COLUMN = "專案ID"
PROJECT_ID = re.compile(r"AU-[a-f0-9]{20}")
SHA256 = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True, slots=True)
class IdentityLink:
    related_id: str
    role: str
    status: str
    source_sha256: str
    locator: str


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    state: str
    developer: str | None
    listing_code: str | None
    identity_links: tuple[IdentityLink, ...]


@dataclass(frozen=True, slots=True)
class ProjectRegistry:
    projects: tuple[Project, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class ResearchProfile:
    profile_id: str
    research_cutoff: date
    timezone: ZoneInfo
    registry_path: Path
    source_catalog_path: Path
    census_columns_path: Path
    project_count: int
    census_column_count: int
    sha256: str
    attribute_bindings_path: Path | None = None
    attribute_milestones_path: Path | None = None


def _read(path: Path, version: str) -> tuple[dict, str]:
    data = path.read_bytes()
    payload = json.loads(data)
    if not isinstance(payload, dict) or payload.get("schema_version") != version:
        raise ValueError(f"Expected {version} at {path}")
    return payload, sha256(data).hexdigest()


def _pinned_profile_path(directory: Path, entry: dict) -> Path:
    path = Path(entry["path"])
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError("Profile file path must be relative to the profile directory")
    path = (directory / path).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Profile file path points outside its directory")
    if sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError(f"Profile fingerprint differs from {path}")
    return path


def load_profile(path: Path | str) -> ResearchProfile:
    """Load profile configuration and verify the three immutable component hashes."""
    path = Path(path).resolve()
    payload, digest = _read(path, PROFILE_SCHEMA)
    inputs = payload["inputs"]
    project_count = payload["project_count"]
    census_column_count = payload["census_column_count"]
    if type(project_count) is not int or project_count <= 0:
        raise ValueError("Profile project count must be positive")
    if type(census_column_count) is not int or census_column_count <= 0:
        raise ValueError("Profile Census column count must be positive")
    profile_id = payload["profile_id"]
    if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", profile_id):
        raise ValueError("Profile ID must be a stable lowercase identifier")
    attribute_inputs = {"attribute_bindings", "attribute_milestones"}
    if attribute_inputs.intersection(inputs) not in (set(), attribute_inputs):
        raise ValueError("Profile attribute bindings and milestone inputs must be pinned together")
    return ResearchProfile(
        profile_id=profile_id,
        research_cutoff=date.fromisoformat(payload["research_cutoff"]),
        timezone=ZoneInfo(payload["timezone"]),
        registry_path=_pinned_profile_path(path.parent, inputs["project_registry"]),
        source_catalog_path=_pinned_profile_path(path.parent, inputs["source_seed_catalog"]),
        census_columns_path=_pinned_profile_path(path.parent, inputs["census_columns"]),
        project_count=project_count, census_column_count=census_column_count, sha256=digest,
        attribute_bindings_path=_pinned_profile_path(path.parent, inputs["attribute_bindings"])
        if "attribute_bindings" in inputs else None,
        attribute_milestones_path=_pinned_profile_path(path.parent, inputs["attribute_milestones"])
        if "attribute_milestones" in inputs else None,
    )


def _validate_link(link: IdentityLink, parent: str, cohort_ids: set[str]) -> None:
    if not PROJECT_ID.fullmatch(link.related_id) or link.related_id in cohort_ids:
        raise ValueError(f"Invalid external alias {link.related_id!r} for {parent}")
    if link.role != "DOCUMENTED_CANONICAL_ALIAS" or not link.status:
        raise ValueError(f"Invalid historical identity role for {parent}")
    if not SHA256.fullmatch(link.source_sha256) or not link.locator:
        raise ValueError(f"Historical alias has insufficient reference metadata for {parent}")


def load_registry(path: Path | str) -> ProjectRegistry:
    """Read frozen Project IDs and attributed display metadata without inferring identity."""
    payload, digest = _read(Path(path), REGISTRY_SCHEMA)
    rows = payload["projects"]
    if not rows:
        raise ValueError("Project registry is empty")
    ids = [row["project_id"] for row in rows]
    id_set = set(ids)
    if len(id_set) != len(ids) or any(not PROJECT_ID.fullmatch(value) for value in ids):
        raise ValueError("Project registry contains duplicate or malformed IDs")
    projects = []
    aliases = set()
    for row in rows:
        links = tuple(IdentityLink(**link) for link in row["identity_links"])
        project = Project(**{**row, "identity_links": links})
        if not project.name or not project.state:
            raise ValueError(f"Missing registry identity label for {project.project_id}")
        for link in links:
            _validate_link(link, project.project_id, id_set)
            if link.related_id in aliases:
                raise ValueError(f"Historical alias appears more than once: {link.related_id}")
            aliases.add(link.related_id)
        projects.append(project)
    return ProjectRegistry(tuple(projects), digest)


def load_census_columns(path: Path | str) -> tuple[str, ...]:
    """Return the exact ordered headings of the frozen starting column contract."""
    payload, _ = _read(Path(path), CENSUS_COLUMNS_SCHEMA)
    columns = tuple(payload["columns"])
    if (not columns or columns[0] != PROJECT_ID_COLUMN or len(columns) != len(set(columns))
            or any(not isinstance(name, str) or not name.strip() for name in columns)):
        raise ValueError("Census heading order is invalid or contains duplicate or empty names")
    return columns

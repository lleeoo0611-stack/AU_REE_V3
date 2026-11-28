"""Pin resource bindings to captured sources and read their exact PDF editions."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import Entity, Source
from .contracts.temporal import TemporalEvidence, evaluate_temporal
from .documents import DocumentCache, DocumentError, PDFDocument
from .observations import digest
from .research_inputs import (
    CENSUS_RESOURCE_RULE, CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE, NATIVE_RESOURCE_CANDIDATE, RESOURCE_RULE,
)
from .sources import ContentStore, IntegrityError, SCHEMA as ACQUISITION_SCHEMA, validate_edition_proofs

SCHEMA = "fresh-resource-extraction-v1"
NATIVE_STRATEGY = "native-resource-table-v1"


@dataclass(frozen=True, slots=True)
class SourcePlan:
    spec: dict
    manifest_sha: str
    rules_sha: str
    native_bindings: list[dict]
    matches: list[tuple[dict, dict, Entity]]

    @property
    def cutoff(self) -> str:
        return self.spec["cutoff"]

    def unmatched_cases(self) -> list[dict]:
        found = {binding["case_id"] for binding, _, _ in self.matches}
        return [{
            "case_id": binding["case_id"], "project_id": binding["project"]["entity_id"],
            "source_key": binding["source_key"], "method": binding["strategy"],
            "source_integrity": "MISSING", "outcome": "SOURCE_UNAVAILABLE", "reason": "NO_MATCHING_CAPTURED_EDITION",
        } for binding in self.spec["bindings"] if binding["case_id"] not in found]


def _project(binding: dict) -> Entity:
    record = Entity(kind="PROJECT", **binding["project"])
    if "AU-" + digest([record.native_namespace, record.native_id])[:20] != record.entity_id:
        raise ValueError("Reviewed binding Project ID disagrees with the native identity")
    return record


def matched_editions(spec: dict, manifest: dict) -> list[tuple[dict, dict, Entity]]:
    result = []
    for binding in spec["bindings"]:
        project = _project(binding)
        editions = [
            edition for edition in manifest["editions"]
            if edition["url"] in binding["approved_urls"]
            and binding["source_key"] in edition["source_keys"]
            and project.entity_id in edition["project_ids"]
            and (binding["strategy"] != NATIVE_STRATEGY or binding.get("source_sha256") == edition["sha256"])
        ]
        for edition in sorted(editions, key=lambda item: item["sha256"]):
            result.append((binding, edition, project))
    return result


def _validate_native_binding(binding: dict) -> None:
    source_sha = binding.get("source_sha256")
    if not isinstance(source_sha, str) or len(source_sha) != 64 or any(
        character not in "0123456789abcdef" for character in source_sha
    ):
        raise ValueError("Native tables require an exact source SHA-256")
    uses = {CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE, NATIVE_RESOURCE_CANDIDATE}
    if binding.get("research_use", NATIVE_RESOURCE_CANDIDATE) not in uses:
        raise ValueError("Unknown native resource research use")


def plan_sources(bindings: Path, receipt: Path, database: Path, out: Path) -> SourcePlan:
    spec = json.loads(bindings.read_bytes())
    manifest_bytes = receipt.read_bytes()
    manifest = json.loads(manifest_bytes)
    if database.resolve() in {(out / name).resolve() for name in ("extraction.json", "observations.jsonl")}:
        raise ValueError("Database path overlaps extraction outputs")
    return plan_source_editions(spec, manifest, sha256(manifest_bytes).hexdigest())


def plan_source_editions(spec: dict, manifest: dict, manifest_sha: str) -> SourcePlan:
    """Bind an already read resource contract to its exact captured editions."""
    if spec["schema"] != SCHEMA or manifest["schema"] != ACQUISITION_SCHEMA:
        raise ValueError("Unsupported extraction bindings or acquisition manifest")
    if any(spec[key] != manifest[key] for key in ("profile_id", "cutoff", "timezone")):
        raise ValueError("The extraction and acquisition research profiles disagree")
    if not spec["bindings"]:
        raise ValueError("No reviewed structure bindings")
    if len({row["case_id"] for row in spec["bindings"]}) != len(spec["bindings"]):
        raise ValueError("Extraction binding case IDs must be unique")
    native = [binding for binding in spec["bindings"] if binding["strategy"] == NATIVE_STRATEGY]
    for binding in native:
        _validate_native_binding(binding)
    rules_sha = digest([RESOURCE_RULE, CENSUS_RESOURCE_RULE, spec] if native else [RESOURCE_RULE, spec])
    return SourcePlan(spec, manifest_sha, rules_sha, native, matched_editions(spec, manifest))


def _last_pages(matches: list[tuple[dict, dict, Entity]]) -> dict[str, int]:
    pages = {}
    for binding, edition, _ in matches:
        required = max(
            binding["page"], binding.get("details_page", 1), binding.get("summary_page", 1),
            binding.get("composition_table", {}).get("page", 1),
            binding.get("treo_image_context", {}).get("physical_page", 1),
            binding.get("table", {}).get("header_page", 1),
            *(item["page"] for item in binding.get("context", ())),
            *(link[name]["page"] for link in binding.get("context_links", ())
              for name in ("start", "end", "reference") if name in link),
        )
        pages[edition["sha256"]] = max(required, pages.get(edition["sha256"], 0))
    return pages


@dataclass(frozen=True, slots=True)
class CapturedSource:
    source: Source
    content: bytes
    pdf: PDFDocument
    proofs: tuple[TemporalEvidence, ...]
    reacquired_at: str | None


def describe_document_error(case: dict, error: DocumentError) -> None:
    compatibility = {
        "SCIENTIFIC_CONFLICT": "INCOMPATIBLE", "EVIDENCE_INSUFFICIENT": "INSUFFICIENT_EVIDENCE",
    }.get(error.outcome, "NOT_EVALUATED")
    case.update(outcome=error.outcome, reason=str(error), scientific_comparability=compatibility)


class ResourceSourceReader:
    """Reuse exact bytes and each decoded PDF while checking proofs per edition."""

    def __init__(self, assets: Path, plan: SourcePlan, cache: DocumentCache, *, required_pages: dict[str, int] | None = None) -> None:
        self._cutoff = plan.cutoff
        self._timezone = ZoneInfo(plan.spec["timezone"])
        self._content = ContentStore(assets)
        self._last_pages = _last_pages(plan.matches)
        for source_sha, required in (required_pages or {}).items():
            if source_sha in self._last_pages:
                self._last_pages[source_sha] = max(self._last_pages[source_sha], required)
        self._cache = cache
        self.originals: dict[str, bytes] = {}
        self.documents: dict[str, PDFDocument] = {}

    def _read_original(self, source: Source, case: dict) -> bytes | None:
        try:
            if source.sha256 not in self.originals:
                self.originals[source.sha256] = self._content.read(source.sha256)
        except FileNotFoundError:
            case.update(source_integrity="MISSING", outcome="SOURCE_UNAVAILABLE", reason="SOURCE_OBJECT_MISSING")
            return None
        except IntegrityError:
            case.update(source_integrity="WRONG_SHA", outcome="SCIENTIFIC_CONFLICT", reason="SOURCE_OBJECT_HASH_MISMATCH")
            return None
        case["source_integrity"] = "EXACT_SHA"
        return self.originals[source.sha256]

    def _proofs_for(self, source: Source, edition: dict, case: dict) -> tuple[TemporalEvidence, ...] | None:
        try:
            proofs = validate_edition_proofs(self._content, edition, self._timezone)
        except (OSError, ValueError, KeyError, TypeError):
            proofs = ()
        case["proof_status"] = "EXACT_CAPTURE_PROOF" if proofs else "UNVERIFIED_CAPTURE_PROOF"
        temporal = evaluate_temporal(
            source_sha256=source.sha256, cutoff=self._cutoff, publication_date=source.publication_date,
            acquired_at=source.acquired_at, reacquired_at=edition["reacquired_at"],
            observation_role="DOCUMENT", evidence=proofs,
        )
        case["temporal_decision"] = temporal.to_dict()
        if temporal.status in {"INELIGIBLE", "CONFLICT"}:
            case.update(outcome=temporal.status, reason=temporal.reason)
            return None
        return proofs

    def _pdf_for(self, source: Source, original: bytes) -> PDFDocument:
        if source.kind != "pdf":
            raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEWED_BINDING_REQUIRES_PDF")
        if source.sha256 not in self.documents:
            self.documents[source.sha256] = self._cache.pdf(original, source.sha256, self._last_pages[source.sha256])
        return self.documents[source.sha256]

    def read(self, binding: dict, edition: dict, project: Entity) -> tuple[dict, CapturedSource | None]:
        source = Source(
            source_key=edition["source_key"], url=edition["url"], sha256=edition["sha256"],
            kind=edition["kind"], source_version=edition["source_version"],
            publication_date=binding.get("publication_date"), acquired_at=edition["acquired_at"],
            original_path=f"objects/{edition['sha256']}",
        )
        case = {
            "case_id": binding["case_id"], "project_id": project.entity_id, "source_key": source.source_key,
            "source_sha256": source.sha256, "method": binding["strategy"], "source_integrity": "NOT_RUN",
            "scientific_comparability": "NOT_EVALUATED",
        }
        original = self._read_original(source, case)
        if original is None:
            return case, None
        proofs = self._proofs_for(source, edition, case)
        if proofs is None:
            return case, None
        try:
            document = self._pdf_for(source, original)
        except DocumentError as error:
            describe_document_error(case, error)
            return case, None
        return case, CapturedSource(source, original, document, proofs, edition["reacquired_at"])

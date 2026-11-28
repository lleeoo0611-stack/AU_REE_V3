"""Coordinate resource extraction, evidence binding, and governed persistence."""

from pathlib import Path

from .contracts import AcceptedResearchValue, ResearchRelease
from .contracts.temporal import TEMPORAL_POLICY_VERSION
from .documents import DocumentCache
from .derivations import RELEASE_REGISTRY_PIN
from .observations import digest
from .research_inputs import CENSUS_RESOURCE_USE, RESOURCE_RULE as RULE, checked_research_inputs
from .resource_audit import complete_report, prepare_report, write_report
from .resource_context import BoundStatement, Measurement, bind_context
from .resource_selection import SelectionProposal, native_resource_verdict as _native_resource_verdict
from .resource_selection import propose_selections, select_reported_resources as _select
from .resource_sources import SCHEMA, ResourceSourceReader, SourcePlan, matched_editions as _matched_editions, plan_sources
from .store import Store


def _persist_release(
    database: Path, plan: SourcePlan, statements: list[BoundStatement], proposal: SelectionProposal,
    originals: dict[str, bytes], fingerprint: str, code_commit: str,
    *, census_derivation_registry_sha256: str | None = None,
) -> tuple[ResearchRelease, dict]:
    selected = proposal.selected
    decisions = tuple(sorted({item.validation.temporal_decision_id for item in selected.values()}))
    proofs = tuple(sorted({proof.evidence_id for item in selected.values() for proof in item.validation.temporal_evidence}))
    identity = [SCHEMA, code_commit, plan.rules_sha, plan.manifest_sha, fingerprint]
    fingerprints = {"admission_plan": fingerprint}
    if census_derivation_registry_sha256 is not None:
        fingerprints[RELEASE_REGISTRY_PIN] = census_derivation_registry_sha256
        identity.append(census_derivation_registry_sha256)
    release = ResearchRelease(
        release_id=digest(identity),
        input_sha=plan.manifest_sha, code_commit=code_commit, rules_sha=plan.rules_sha,
        cutoff=plan.cutoff, temporal_policy_version=TEMPORAL_POLICY_VERSION,
        temporal_decision_ids=decisions, temporal_evidence_ids=proofs,
        cohort=tuple(sorted({item.project.entity_id for item in statements})),
        validation_status="PASS" if selected else "NOT_RUN",
        release_mode="FORMAL" if selected else "EXPERIMENTAL",
        result_fingerprints=fingerprints,
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    with Store(database, trusted_temporal_evidence_ids=proofs) as store:
        with store.transaction():
            for item in statements:
                store.add(item.project)
                store.add(item.scope)
                store.add_source(item.source, originals[item.source.sha256])
                for measurement in item.measurements:
                    for record in (measurement.anchor, measurement.raw, measurement.candidate, measurement.validation):
                        store.add(record)
            store.add(release)
            for measurement in selected.values():
                store.add(AcceptedResearchValue(
                    acceptance_id=digest(["fresh-resource-selection-v1", release.release_id,
                                          measurement.validation.validation_id]),
                    validation_id=measurement.validation.validation_id, release_id=release.release_id,
                    selection_key=f"{measurement.raw.project_id}:{measurement.raw.variable}",
                    reason=proposal.selection_rule(measurement.raw.observation_id),
                ))
            emitted = checked_research_inputs(store, release, purpose="CENSUS")
            if len(emitted) != len(selected):
                raise RuntimeError("Census does not see the complete governed selection")
        return release, store.integrity()


def extract_sources(
    bindings: Path, receipt: Path, assets: Path, database: Path, out: Path, *, code_commit: str,
    census_derivation_registry_sha256: str | None = None,
) -> dict:
    """Rebuild typed observation and selection records from the V3 acquisition store."""
    plan = plan_sources(bindings, receipt, database, out)
    reader = ResourceSourceReader(assets, plan, DocumentCache())
    cases = plan.unmatched_cases()
    statements = []
    for binding, edition, project in plan.matches:
        case, capture = reader.read(binding, edition, project)
        cases.append(case)
        if capture is not None:
            statements.extend(bind_context(binding, project, capture, case, cutoff=plan.cutoff, rules_sha=plan.rules_sha))

    proposal = propose_selections(statements)
    result, rows = prepare_report(plan, reader, cases, statements, proposal)
    if rows:
        pin = census_derivation_registry_sha256 if any(
            binding.get("research_use") == CENSUS_RESOURCE_USE for binding in plan.native_bindings
        ) else None
        release, integrity = _persist_release(
            database, plan, statements, proposal, reader.originals, result["scientific_fingerprint"], code_commit,
            census_derivation_registry_sha256=pin,
        )
        if pin is not None:
            result["census_derivation_registry_sha256"] = pin
        complete_report(result, plan, proposal, release, integrity)
    return write_report(out, result, rows)

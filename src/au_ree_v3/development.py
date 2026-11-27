"""Review source-bound development milestone assertions as shadow observations."""

from collections import Counter
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from .contracts import EvidenceAnchor, RawObservation, canonical_json
from .contracts.temporal import evaluate_temporal
from .documents import DocumentCache, DocumentError, PDFDocument
from .fresh_decision import POLICY_SHA256, load_policy
from .registry import load_profile, load_registry
from .sources import ContentStore, IntegrityError, SCHEMA as SOURCE_SCHEMA, validate_edition_proofs

SCHEMA = "source-bound-development-events-v1"
_STAGE_TERMS = {
    "MRE": r"\b(?:MRE|Mineral Resource Estimate)\b",
    "Scoping": r"\bScoping Study\b",
    "PFS": r"\b(?:PFS|Pre[- ]?Feasibility Study)\b",
    "DFS": r"\b(?:DFS|Definitive Feasibility Study)\b",
    "Construction": r"\bConstruction\b",
    "Production": r"\bProduction\b",
}
_NEGATED = re.compile(r"\b(?:not|never)\s+(?:(?:yet|been)\s+)*(?:completed|finished|started|commenced|released|published)\b", re.I)
_FUTURE = re.compile(r"\b(?:will|would)\s+(?:(?:have|been|be)\s+)*(?:commence|begin|start|undertake|complete|release|publish|finish)\w*\b"
                     r"|\b(?:plan(?:s|ned)?|expect(?:s|ed)?|intend(?:s|ed)?|target(?:s|ed)?|schedul(?:e[ds]?|ing))\b"
                     r"\s+(?:to|for|that)\b.{0,40}\b(?:complete|commence|begin|start|publish|launch|undertake|"
                     r"completion|commencement|production)\w*\b", re.I)
_COMPLETED = re.compile(r"\b(?:completed|finalised|finalized|finished|released|published)\b", re.I)
_COMMENCED = re.compile(r"\b(?:has\s+)?(?:commenced|began|started|achieved)\b", re.I)
_DATE_LITERAL = re.compile(r"(?:(?:[1-3]?[0-9])\s+)?(?:January|February|March|April|May|June|July|August|September|"
                           r"October|November|December)\s+[12][0-9]{3}", re.I)


def _canonical_sha(value) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _flatten(text: str) -> str:
    return " ".join(text.split())


def _classification(stage: str, quote: str) -> tuple[str, str]:
    mentioned = {name for name, expression in _STAGE_TERMS.items() if re.search(expression, quote, re.I)}
    if stage not in mentioned:
        return "INSUFFICIENT_CONTEXT", "DECLARED_STAGE_NOT_IN_EVIDENCE"
    if len(mentioned) > 1:
        return "INSUFFICIENT_CONTEXT", "MULTIPLE_MILESTONE_STAGES_IN_REVIEW_SPAN"
    if _NEGATED.search(quote):
        return "NOT_COMPLETED", "EXPLICITLY_NOT_COMPLETED"
    if _FUTURE.search(quote):
        return "PLANNED", "FUTURE_OR_CONDITIONAL_LANGUAGE"
    if stage in {"Construction", "Production"}:
        if re.search(r"\b(?:pilot|bench|trial|test|demonstration)\b", quote, re.I):
            return "INSUFFICIENT_CONTEXT", "TRIAL_ACTIVITY_DOES_NOT_ESTABLISH_PROJECT_STAGE"
        started = r"(?:commenced|began|started|achieved)"
        if _COMMENCED.search(quote) and re.search(rf"\b{stage}\s+(?:(?:has|had)\s+)?{started}\b"
                                                 rf"|\b{started}\s+(?:commercial\s+)?{stage}\b", quote, re.I):
            return "ATTAINED", "EXPLICIT_STAGE_COMMENCEMENT"
    elif _COMPLETED.search(quote):
        return "COMPLETED", "EXPLICIT_COMPLETION_LANGUAGE"
    return "MENTIONED", "NO_COMPLETION_OR_COMMENCEMENT_CLAIM"


def _date_token(token: str | None) -> str | None:
    """Keep a reported date at the precision actually disclosed."""
    if token is None:
        return None
    if _DATE_LITERAL.fullmatch(token):
        pattern = "%d %B %Y" if token[0].isdigit() else "%B %Y"
        parsed = datetime.strptime(token.title(), pattern)
        return parsed.date().isoformat() if token[0].isdigit() else parsed.strftime("%Y-%m")
    if re.fullmatch(r"(?:early |mid[ -]|late )?[12][0-9]{3}", token, re.I):
        return token[-4:]
    raise ValueError("D1 binding date must be a reported calendar day, month, or year")


def _anchor(document: PDFDocument, source_key: str, page: int, quote: str, purpose: str) -> EvidenceAnchor:
    view = _flatten(document.locate(page).text)
    quote = _flatten(quote)
    if not quote or view.count(quote) != 1:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "REVIEW_QUOTE_NOT_UNIQUE_ON_PAGE")
    start = view.index(quote)
    span = {"view": "NATIVE_PDF_WHITESPACE_FOLDED", "scope": "PAGE", "start": start, "end": start + len(quote)}
    return EvidenceAnchor(
        anchor_id=_canonical_sha([SCHEMA, source_key, document.source_sha256, page, span, quote, purpose]),
        source_key=source_key, source_sha=document.source_sha256, page=page, quote=quote,
        locator=f"PDF page {page}, native whitespace-folded characters {start}-{start + len(quote)}",
        text_span=span, role=purpose,
    )


def _review(binding, edition, document, proofs, cutoff, ordinal):
    page, key, digest = binding["page"], edition["source_key"], edition["sha256"]
    statement = _anchor(document, key, page, binding["quote"], "DEVELOPMENT_STAGE_CLAIM")
    scope = _anchor(document, key, page, binding["scope_quote"], "DEVELOPMENT_PROJECT_SCOPE")
    gap = max(statement.text_span["start"], scope.text_span["start"]) - min(
        statement.text_span["end"], scope.text_span["end"],
    )
    if gap > 160:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "PROJECT_SCOPE_NOT_LOCAL_TO_MILESTONE")
    publication = binding.get("publication")
    publication_anchor = None
    if publication:
        publication_anchor = _anchor(
            document, key, publication["page"], publication["quote"], "DEVELOPMENT_PUBLICATION_DATE",
        )
        if publication["date_token"] not in publication_anchor.quote:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "PUBLICATION_DATE_NOT_BOUND_TO_QUOTE")
    published_date = _date_token(publication["date_token"]) if publication else None
    claim_state, reason = _classification(binding["stage"], statement.quote)
    date_token = binding.get("event_date_token")
    if date_token is not None and not re.search(
        rf"\b(?:in|on)\s+{re.escape(date_token)}\b", statement.quote, re.I,
    ):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "EVENT_DATE_NOT_BOUND_TO_QUOTE")
    event_date = _date_token(date_token)
    reached = claim_state in {"COMPLETED", "ATTAINED"}
    role = "HISTORICAL_EVENT" if reached else "ENGINEERING_PLAN" if claim_state == "PLANNED" else "DOCUMENT"
    decision = evaluate_temporal(
        source_sha256=digest, cutoff=cutoff, observation_role=role, publication_date=published_date,
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"],
        event_date=event_date if reached else None, evidence=proofs,
    )
    anchors = [statement, scope] + ([publication_anchor] if publication_anchor else [])
    observed = None
    if reached or claim_state == "PLANNED":
        scope_id = _canonical_sha([SCHEMA, binding["project_id"], binding["scope"]])
        observed = RawObservation(
            observation_id=_canonical_sha([SCHEMA, binding["project_id"], scope_id, statement.anchor_id]),
            project_id=binding["project_id"], scope_id=scope_id, source_key=key, source_sha=digest,
            variable="study_maturity_ord", quantity_kind="DEVELOPMENT_MILESTONE",
            raw_value=statement.quote, raw_unit=None, status="SHADOW", transform_version=SCHEMA,
            anchor_id=statement.anchor_id, event_date=event_date if reached else None,
            reported_publication_date=published_date, acquisition_date=edition["acquired_at"],
            observation_role=role, id_version=SCHEMA,
            original_payload={
                "stage": binding["stage"], "claim_state": claim_state, "event_date_token": date_token,
                "policy_ordinal": ordinal, "scope_label": binding["scope"],
                "scope_anchor_id": scope.anchor_id, "publication_anchor_id": (
                    publication_anchor.anchor_id if publication_anchor else None
                ),
            },
        )
    # A temporally eligible plan can be studied as a plan, not as a completed milestone.
    dated = reached and decision.status == "ELIGIBLE"
    return {
        "claim_state": claim_state, "reason": reason, "observation_role": role,
        "temporal_decision": decision.to_dict(), "temporal_evidence": [asdict(proof) for proof in proofs],
        "event_date": event_date if reached else None, "event_date_token": date_token,
        "shadow_ordinal": ordinal if dated else None, "ordinal_policy_sha256": POLICY_SHA256,
        "scope": binding["scope"], "evidence": [anchor.to_dict() for anchor in anchors],
        "raw_observation": observed.to_dict() if observed else None,
    }


def review_milestones(profile: Path, manifest: Path, assets: Path, bindings: Path, out: Path) -> dict:
    """Review exact native source spans and leave the formal D1 adapter unchanged."""
    settings = load_profile(profile)
    cutoff = settings.research_cutoff.isoformat()
    registry = {project.project_id for project in load_registry(settings.registry_path).projects}
    frozen_data = manifest.read_bytes()
    frozen_sha = sha256(frozen_data).hexdigest()
    sources = json.loads(frozen_data)
    binding_bytes = bindings.read_bytes()
    spec = json.loads(binding_bytes)
    if (spec["schema"] != SCHEMA or spec["source_manifest_sha256"] != frozen_sha or sources["schema"] != SOURCE_SCHEMA
            or (sources["profile_id"], sources["cutoff"], sources["timezone"])
            != (settings.profile_id, cutoff, settings.timezone.key)):
        raise ValueError("D1 binding, frozen manifest and research profile differ")
    ordinals = {label: int(ordinal) for ordinal, label in load_policy()["criteria"]["D1"]["meaning"].items()}
    if set(ordinals) != set(_STAGE_TERMS):
        raise ValueError("D1 policy stage vocabulary differs from the reviewed extractor")
    cases = spec["bindings"]
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("D1 binding case IDs must be nonempty and unique")
    content = ContentStore(assets)
    editions = {item["receipt_sha256"]: item for item in sources["editions"]}
    cache = DocumentCache()
    views = {}
    results = []
    for binding in cases:
        project, digest, stage = (binding[name] for name in ("project_id", "source_sha256", "stage"))
        if project not in registry:
            raise ValueError("D1 binding Project ID lies outside the pinned cohort")
        edition = editions.get(binding["receipt_sha256"])
        row = {"case_id": binding["case_id"], "project_id": project, "stage": stage,
               "source_sha256": digest, "page": binding["page"]}
        try:
            if edition is None:
                raise DocumentError("SOURCE_UNAVAILABLE", "IMMUTABLE_EDITION_MISSING")
            if edition["sha256"] != digest or project not in edition["project_ids"] or edition["kind"] != "pdf":
                raise DocumentError("SCIENTIFIC_CONFLICT", "EDITION_OR_PROJECT_ID_MISMATCH")
            proofs = validate_edition_proofs(content, edition, settings.timezone)
            if not proofs:
                raise DocumentError("EVIDENCE_INSUFFICIENT", "EXACT_EDITION_PUBLIC_PROOF_MISSING")
            if digest not in views:
                views[digest] = cache.pdf(content.read(digest), digest)
            row.update(_review(binding, edition, views[digest], proofs, cutoff, ordinals[stage]))
        except DocumentError as error:
            row.update(claim_state=error.outcome, reason=str(error), shadow_ordinal=None)
        except OSError:
            row.update(claim_state="SOURCE_UNAVAILABLE", reason="SOURCE_OR_PROOF_BYTES_UNAVAILABLE", shadow_ordinal=None)
        except IntegrityError:
            row.update(claim_state="SCIENTIFIC_CONFLICT", reason="SOURCE_OR_PROOF_DIGEST_MISMATCH", shadow_ordinal=None)
        results.append(row)
    counts = Counter(row["claim_state"] for row in results)
    receipt = {
        "schema": SCHEMA, "research_profile_sha256": settings.sha256, "research_cutoff": cutoff,
        "source_manifest_sha256": frozen_sha, "bindings_sha256": sha256(binding_bytes).hexdigest(),
        "source_manifest_unique_sha256": len({item["sha256"] for item in sources["editions"]}),
        "case_states": dict(counts), "cases": results,
        "dated_eligible_milestone_assertions": sum(row["shadow_ordinal"] is not None for row in results),
        "formal_project_criterion_delta": 0, "formal_D1": "NOT_RUN",
        "status": "FAIL" if counts["SCIENTIFIC_CONFLICT"] or counts["CODE_ERROR"] else
                  "BLOCKED" if counts["SOURCE_UNAVAILABLE"] or counts["INSUFFICIENT_CONTEXT"] else "PASS",
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "development_milestones.json").write_text(canonical_json(receipt) + "\n", encoding="utf-8")
    return receipt

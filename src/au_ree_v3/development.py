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
_EARLY_WORKS = re.compile(r"\b(?:early (?:site access|construction|works)|site mobili[sz]ation|enabling works)\b", re.I)
_DATE_LITERAL = re.compile(r"(?:(?:[1-3]?[0-9])\s+)?(?:January|February|March|April|May|June|July|August|September|"
                           r"October|November|December)\s+[12][0-9]{3}", re.I)
_STUDY_RESULTS_RELEASE = (
    r"(?:\b(?:released|published)\s+(?:the|a)\s+"
    r"|\b(?:is\s+pleased\s+to\s+(?:announce|report)|announces|reports)\s+the\s+results\s+of\s+(?:the|a)\s+)"
    r"(?:(?:final|updated|revised|enhanced)\s+)?"
)
_ISSUER_NOTICE = re.compile(r"\b(?:ASX|RNS)\s+(?:announcement|release)\b", re.I)
_PLANNED_STUDY_RELEASE = re.compile(r"\b(?:planned|expected|scheduled|targeted)\s+for\s+(?:release|publication)\b", re.I)
_UNCERTAIN_STUDY_RELEASE = re.compile(r"\b(?:if|whether|unless|may have|could have|possibly|reportedly)\b", re.I)
STUDY_RESULTS_RELEASED = "STUDY_RESULTS_RELEASED"
STUDY_STAGES = frozenset({"Scoping", "PFS", "DFS"})
DATED_STUDY_POLICY_VERSION = "census-dated-study-event-v1"
DATED_STUDY_POLICY_V2 = "census-dated-study-event-v2"
_STUDY_RELEASE_PATTERNS = {
    DATED_STUDY_POLICY_VERSION: _STUDY_RESULTS_RELEASE,
    DATED_STUDY_POLICY_V2: _STUDY_RESULTS_RELEASE.replace(r"\s+results\s+", r"\s+(?:results|outcomes)\s+"),
}
MILESTONE_SCOPE_SECTION_LIMIT = 950
_COMPLETION_DATE = r"(?:\s+\((?:DFS|PFS|BFS)\))?(?:\s+(?:was|has been|had been))?\s+(?:completed|finalised|finalized|finished)\s+(?:in|on)\s+"
_LEADING_EVENT_DATE = r"^(?:in|on)\s+{date},\s+[\w &’'.-]{{1,90}}?\s+{event}"


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
        if stage == "Construction" and _EARLY_WORKS.search(quote):
            return "EARLY_WORKS", "EARLY_OR_ENABLING_WORKS_DO_NOT_ESTABLISH_FORMAL_CONSTRUCTION"
        started = r"(?:commenced|began|started|achieved)"
        if _COMMENCED.search(quote) and re.search(rf"\b{stage}\s+(?:(?:has|had)\s+)?{started}\b"
                                                 rf"|\b{started}\s+(?:commercial\s+)?{stage}\b", quote, re.I):
            return "ATTAINED", "EXPLICIT_STAGE_COMMENCEMENT"
    elif _COMPLETED.search(quote):
        return "COMPLETED", "EXPLICIT_COMPLETION_LANGUAGE"
    elif stage in STUDY_STAGES:
        terms = _STAGE_TERMS[stage]
        began = r"(?:commenced|began|started)"
        if re.search(rf"{terms}\s+(?:has\s+)?{began}\b|\b{began}\s+(?:(?:the|a)\s+)?{terms}", quote, re.I):
            return "ONGOING", "STUDY_COMMENCEMENT_WITHOUT_COMPLETION"
    return "MENTIONED", "NO_COMPLETION_OR_COMMENCEMENT_CLAIM"


def reported_calendar_date(token: str | None) -> str | None:
    """Keep a reported date at the precision actually disclosed."""
    if token is None:
        return None
    token = re.sub(r"(?<=[A-Za-z]), (?=[12][0-9]{3}$)", " ", token)
    if _DATE_LITERAL.fullmatch(token):
        pattern = "%d %B %Y" if token[0].isdigit() else "%B %Y"
        parsed = datetime.strptime(token.title(), pattern)
        return parsed.date().isoformat() if token[0].isdigit() else parsed.strftime("%Y-%m")
    if re.fullmatch(r"(?:early |mid[ -]|late )?[12][0-9]{3}", token, re.I):
        return token[-4:]
    raise ValueError("D1 binding date must be a reported calendar day, month, or year")


def bind_native_quote(document: PDFDocument, source_key: str, page: int, quote: str, purpose: str) -> EvidenceAnchor:
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


def study_results_released(stage: str, quote: str, *, policy: str = DATED_STUDY_POLICY_VERSION) -> bool:
    """Recognize the issuer's act of disclosing results for one named study type."""
    return (
        stage in STUDY_STAGES
        and not (_FUTURE.search(quote) or _NEGATED.search(quote) or _UNCERTAIN_STUDY_RELEASE.search(quote))
        and bool(re.search(rf"{_STUDY_RELEASE_PATTERNS[policy]}{_STAGE_TERMS[stage]}", quote, re.I))
    )


def _date_in_event(date_token: str, event_quote: str) -> bool:
    return bool(re.search(rf"\b(?:in|on)\s+{re.escape(date_token)}\b", event_quote, re.I))


def calendar_precedes(earlier: str, later: str) -> bool:
    """Order canonical calendar dates only when their reported precision cannot overlap."""
    return earlier < later and not later.startswith(earlier + "-")


def dated_study_policy_definition(policy: str) -> dict:
    return {
        "stage_patterns": {stage: _STAGE_TERMS[stage] for stage in sorted(STUDY_STAGES)},
        "study_release": _STUDY_RELEASE_PATTERNS[policy], "study_completion": _COMPLETED.pattern,
        "completion_date": _COMPLETION_DATE, "leading_event_date": _LEADING_EVENT_DATE,
        "planned": _FUTURE.pattern, "negated": _NEGATED.pattern,
        "planned_study_release": _PLANNED_STUDY_RELEASE.pattern,
        "uncertain_study_release": _UNCERTAIN_STUDY_RELEASE.pattern,
        "issuer_notice": _ISSUER_NOTICE.pattern,
        "scope_section_limit": MILESTONE_SCOPE_SECTION_LIMIT,
        "history_order": "NONOVERLAPPING_REPORTED_CALENDAR_PRECISION",
    }


def dated_study_matches(stage, role, token, statement, publication, *, policy=DATED_STUDY_POLICY_VERSION):
    if not token or stage not in STUDY_STAGES:
        return False
    if role == "COMPLETED":
        return _classification(stage, statement.quote)[0] == role and bool(
            re.search(rf"{_STAGE_TERMS[stage]}{_COMPLETION_DATE}{re.escape(token)}\b", statement.quote, re.I)
        )
    if role != STUDY_RESULTS_RELEASED or not study_results_released(stage, statement.quote, policy=policy):
        return False
    results = f"{_STUDY_RELEASE_PATTERNS[policy]}{_STAGE_TERMS[stage]}"
    return (
        re.search(_LEADING_EVENT_DATE.format(date=re.escape(token), event=results), statement.quote, re.I)
        is not None or (
            publication is not None and publication.page == statement.page
            and token in publication.quote and _ISSUER_NOTICE.search(publication.quote) is not None
        )
    )


def _scope_anchors(binding, document, key, page, statement):
    scope = bind_native_quote(document, key, page, binding["scope_quote"], "DEVELOPMENT_PROJECT_SCOPE")
    if "scope_section_quote" in binding:
        section = bind_native_quote(
            document, key, page, binding["scope_section_quote"], "DEVELOPMENT_SCOPE_SECTION",
        )
        if (len(section.quote) > MILESTONE_SCOPE_SECTION_LIMIT or not all(
            section.text_span["start"] <= ref.text_span["start"] <= ref.text_span["end"] <= section.text_span["end"]
            for ref in (statement, scope)
        )):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "MILESTONE_OUTSIDE_PROJECT_SCOPE_SECTION")
        return scope, section
    gap = max(statement.text_span["start"], scope.text_span["start"]) - min(
        statement.text_span["end"], scope.text_span["end"],
    )
    if gap > 160:
        raise DocumentError("EVIDENCE_INSUFFICIENT", "PROJECT_SCOPE_NOT_LOCAL_TO_MILESTONE")
    return scope, None


def _event_claim(binding, statement, publication_anchor, published_date):
    claim_state, reason = _classification(binding["stage"], statement.quote)
    policy = binding.get("event_policy", DATED_STUDY_POLICY_VERSION)
    if policy not in _STUDY_RELEASE_PATTERNS:
        raise DocumentError("CODE_ERROR", "UNRECOGNIZED_DATED_STUDY_POLICY")
    dated_study_policy = "event_policy" in binding
    if (dated_study_policy and claim_state == "MENTIONED" and binding["stage"] in STUDY_STAGES
            and _PLANNED_STUDY_RELEASE.search(statement.quote)):
        claim_state, reason = "PLANNED", "FUTURE_OR_CONDITIONAL_LANGUAGE"
    date_token = binding.get("event_date_token")
    if binding.get("event_date_role") == STUDY_RESULTS_RELEASED:
        if not study_results_released(binding["stage"], statement.quote, policy=policy):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "ISSUER_STUDY_RESULTS_RELEASE_UNBOUND")
        claim_state, reason = STUDY_RESULTS_RELEASED, "EXPLICIT_ISSUER_STUDY_RESULTS_RELEASE"
    release_date = binding.get("event_date_source") == "SAME_ISSUER_NOTICE"
    if release_date:
        if (claim_state != STUDY_RESULTS_RELEASED or date_token is not None or publication_anchor is None
                or publication_anchor.page != statement.page or not _ISSUER_NOTICE.search(publication_anchor.quote)):
            raise DocumentError("EVIDENCE_INSUFFICIENT", "STUDY_RESULTS_RELEASE_DATE_UNBOUND")
        date_token = binding["publication"]["date_token"]
    if date_token is not None and not release_date and not _date_in_event(date_token, statement.quote):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "EVENT_DATE_NOT_BOUND_TO_QUOTE")
    if (dated_study_policy and claim_state in {"COMPLETED", STUDY_RESULTS_RELEASED}
            and not dated_study_matches(binding["stage"], claim_state, date_token, statement, publication_anchor,
                                        policy=policy)):
        raise DocumentError("EVIDENCE_INSUFFICIENT", "DATE_NOT_BOUND_TO_STUDY_EVENT")
    event_date = reported_calendar_date(date_token)
    if (dated_study_policy and event_date and published_date
            and calendar_precedes(published_date, event_date)):
        raise DocumentError("SCIENTIFIC_CONFLICT", "HISTORICAL_STUDY_EVENT_AFTER_ISSUER_PUBLICATION")
    return claim_state, reason, date_token, event_date


def read_milestone_evidence(binding, edition, document, proofs, cutoff):
    """Read one source-bound claim without projecting a stage or decision score."""
    page, key, digest = binding["page"], edition["source_key"], edition["sha256"]
    statement = bind_native_quote(document, key, page, binding["quote"], "DEVELOPMENT_STAGE_CLAIM")
    scope, section = _scope_anchors(binding, document, key, page, statement)
    publication = binding.get("publication")
    publication_anchor = None
    if publication:
        publication_anchor = bind_native_quote(
            document, key, publication["page"], publication["quote"], "DEVELOPMENT_PUBLICATION_DATE",
        )
        if publication["date_token"] not in publication_anchor.quote:
            raise DocumentError("EVIDENCE_INSUFFICIENT", "PUBLICATION_DATE_NOT_BOUND_TO_QUOTE")
    published_date = reported_calendar_date(publication["date_token"]) if publication else None
    claim_state, reason, date_token, event_date = _event_claim(binding, statement, publication_anchor, published_date)
    reached = claim_state in {"COMPLETED", "ATTAINED", STUDY_RESULTS_RELEASED}
    role = "HISTORICAL_EVENT" if reached else "ENGINEERING_PLAN" if claim_state == "PLANNED" else "DOCUMENT"
    decision = evaluate_temporal(
        source_sha256=digest, cutoff=cutoff, observation_role=role, publication_date=published_date,
        acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"],
        event_date=event_date if reached else None, evidence=proofs,
    )
    anchors = [statement, scope] + ([publication_anchor] if publication_anchor else []) + ([section] if section else [])
    return claim_state, reason, role, event_date, date_token, published_date, anchors, decision


def _review(binding, edition, document, proofs, cutoff, ordinal):
    claim_state, reason, role, event_date, date_token, published_date, anchors, decision = (
        read_milestone_evidence(binding, edition, document, proofs, cutoff)
    )
    statement, scope, *_ = anchors
    publication_anchor = next((ref for ref in anchors if ref.role == "DEVELOPMENT_PUBLICATION_DATE"), None)
    reached = claim_state in {"COMPLETED", "ATTAINED", STUDY_RESULTS_RELEASED}
    key, digest = edition["source_key"], edition["sha256"]
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
    dated = reached and claim_state != STUDY_RESULTS_RELEASED and decision.status == "ELIGIBLE"
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

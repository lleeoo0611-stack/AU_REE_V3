"""SQLite repository for immutable versions and explicit observation transitions."""

from __future__ import annotations

import calendar
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

from .contracts import (
    AcceptedResearchValue,
    ContractViolation,
    DerivedValue,
    Entity,
    EntityKind,
    EntityRelationship,
    EvidenceAnchor,
    NormalizedObservation,
    RawObservation,
    Record,
    ResearchRelease,
    ResourceScope,
    Source,
    ValidatedObservation,
    ValidationOutcome,
    canonical_json,
)

APPLICATION_ID = 0x41555233
SCHEMA_VERSION = 1

_MIGRATIONS = {
    1: (
        """CREATE TABLE sources (
            source_key TEXT NOT NULL, sha256 TEXT NOT NULL, source_version TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(source_key, sha256), UNIQUE(source_key, source_version)
        )""",
        """CREATE TABLE verified_sources (
            source_key TEXT NOT NULL, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
            PRIMARY KEY(source_key, sha256), FOREIGN KEY(source_key, sha256) REFERENCES sources
        )""",
        """CREATE TABLE entities (
            entity_id TEXT PRIMARY KEY NOT NULL, kind TEXT NOT NULL, native_namespace TEXT NOT NULL,
            native_id TEXT NOT NULL, parent_id TEXT REFERENCES entities, payload TEXT NOT NULL,
            UNIQUE(kind, native_namespace, native_id)
        )""",
        """CREATE TABLE scopes (
            scope_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL REFERENCES entities,
            deposit_id TEXT REFERENCES entities, parent_scope_id TEXT REFERENCES scopes, payload TEXT NOT NULL
        )""",
        """CREATE TABLE anchors (
            anchor_id TEXT PRIMARY KEY NOT NULL, source_key TEXT NOT NULL, source_sha TEXT NOT NULL,
            payload TEXT NOT NULL, FOREIGN KEY(source_key, source_sha) REFERENCES sources
        )""",
        """CREATE TABLE relationships (
            relationship_id TEXT PRIMARY KEY NOT NULL, subject_id TEXT NOT NULL REFERENCES entities,
            object_id TEXT NOT NULL REFERENCES entities, anchor_id TEXT NOT NULL REFERENCES anchors,
            payload TEXT NOT NULL
        )""",
        """CREATE TABLE raw_observations (
            raw_record_id TEXT PRIMARY KEY NOT NULL, observation_id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES entities,
            scope_id TEXT NOT NULL REFERENCES scopes, source_key TEXT NOT NULL, source_sha TEXT NOT NULL,
            anchor_id TEXT REFERENCES anchors, payload TEXT NOT NULL, FOREIGN KEY(source_key, source_sha) REFERENCES sources
        )""",
        """CREATE INDEX raw_by_observation_id ON raw_observations(observation_id)""",
        """CREATE TABLE candidates (
            candidate_id TEXT PRIMARY KEY NOT NULL, raw_record_id TEXT NOT NULL REFERENCES raw_observations,
            payload TEXT NOT NULL
        )""",
        """CREATE TABLE validations (
            validation_id TEXT PRIMARY KEY NOT NULL, candidate_id TEXT NOT NULL REFERENCES candidates,
            payload TEXT NOT NULL
        )""",
        """CREATE TABLE validation_anchors (
            validation_id TEXT NOT NULL REFERENCES validations, anchor_id TEXT NOT NULL REFERENCES anchors,
            PRIMARY KEY(validation_id, anchor_id)
        )""",
        """CREATE TABLE releases (
            release_id TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL
        )""",
        """CREATE TABLE release_projects (
            release_id TEXT NOT NULL REFERENCES releases, project_id TEXT NOT NULL REFERENCES entities,
            PRIMARY KEY(release_id, project_id)
        )""",
        """CREATE TABLE accepted_values (
            acceptance_id TEXT PRIMARY KEY NOT NULL, validation_id TEXT NOT NULL REFERENCES validations,
            release_id TEXT NOT NULL REFERENCES releases, selection_key TEXT NOT NULL, payload TEXT NOT NULL,
            UNIQUE(release_id, selection_key)
        )""",
        """CREATE INDEX accepted_values_by_release ON accepted_values(release_id, validation_id)""",
        """CREATE TABLE derived_values (
            derived_id TEXT PRIMARY KEY NOT NULL, release_id TEXT NOT NULL REFERENCES releases,
            project_id TEXT NOT NULL REFERENCES entities, scope_id TEXT NOT NULL REFERENCES scopes,
            payload TEXT NOT NULL
        )""",
        """CREATE TABLE derived_inputs (
            derived_id TEXT NOT NULL REFERENCES derived_values, position INTEGER NOT NULL,
            raw_record_id TEXT NOT NULL REFERENCES raw_observations, PRIMARY KEY(derived_id, position)
        )""",
    ),
}

# Each entry lists the table, stable key and relational fields in its JSON payload.
_TABLES = {
    Source: ("sources", ("source_key", "sha256"), ("source_version",)),
    Entity: ("entities", ("entity_id",), ("kind", "native_namespace", "native_id", "parent_id")),
    ResourceScope: ("scopes", ("scope_id",), ("project_id", "deposit_id", "parent_scope_id")),
    EntityRelationship: ("relationships", ("relationship_id",), ("subject_id", "object_id", "anchor_id")),
    EvidenceAnchor: ("anchors", ("anchor_id",), ("source_key", "source_sha")),
    RawObservation: (
        "raw_observations", ("raw_record_id",),
        ("observation_id", "project_id", "scope_id", "source_key", "source_sha", "anchor_id"),
    ),
    NormalizedObservation: ("candidates", ("candidate_id",), ("raw_record_id",)),
    ValidatedObservation: ("validations", ("validation_id",), ("candidate_id",)),
    ResearchRelease: ("releases", ("release_id",), ()),
    AcceptedResearchValue: ("accepted_values", ("acceptance_id",), ("validation_id", "release_id", "selection_key")),
    DerivedValue: ("derived_values", ("derived_id",), ("release_id", "project_id", "scope_id")),
}
R = TypeVar("R", bound=Record)


class RecordConflict(ContractViolation):
    """An existing immutable identity was submitted with different contents."""


class SourceDigestMismatch(ContractViolation):
    """Source bytes do not match the declared immutable SHA-256."""


class Store:
    """Apply schema migrations and persist batches through a single SQLite writer."""

    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path, isolation_level=None)
        try:
            self._db.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        except BaseException:
            self._db.close()
            raise

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_exceptions) -> None:
        self._db.close()

    @contextmanager
    def transaction(self):
        """Commit one batch or roll back all its statements on failure."""
        if self._db.in_transaction:
            raise RuntimeError("nested store transactions are unsupported")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def _migrate(self) -> None:
        with self.transaction():
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            application_id = self._db.execute("PRAGMA application_id").fetchone()[0]
            if application_id != APPLICATION_ID:
                tables = self._db.execute("SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")
                if application_id or version or tables.fetchone():
                    raise ContractViolation("SQLite database does not belong to AU_REE_V3")
                self._db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            if not 0 <= version <= SCHEMA_VERSION:
                raise ContractViolation(f"unsupported SQLite schema version {version}")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[target]:
                    self._db.execute(statement)
                self._db.execute(f"PRAGMA user_version = {target}")

    def get(self, record_type: type[R], key: str | tuple[str, ...]) -> R | None:
        """Read a record by its versioned key or an unambiguous legacy Observation ID."""
        record = self._get_exact(record_type, key)
        if record is not None or record_type is not RawObservation:
            return record
        observation_id = key[0] if isinstance(key, tuple) else key
        rows = self._db.execute(
            "SELECT payload FROM raw_observations WHERE observation_id = ? LIMIT 2", (observation_id,)
        ).fetchall()
        if len(rows) > 1:
            raise RecordConflict(f"Observation ID {observation_id!r} has multiple editions; supply a raw_record_id")
        return record_type.from_dict(json.loads(rows[0][0])) if rows else None

    def _get_exact(self, record_type: type[R], key: str | tuple[str, ...]) -> R | None:
        table, keys, _ = _TABLES[record_type]
        params = key if isinstance(key, tuple) else (key,)
        if len(params) != len(keys):
            raise ContractViolation(f"{record_type.__name__} requires {len(keys)} key fields")
        where = " AND ".join(f"{field} = ?" for field in keys)
        row = self._db.execute(f"SELECT payload FROM {table} WHERE {where}", params).fetchone()
        return record_type.from_dict(json.loads(row[0])) if row else None

    def records(self, record_type: type[R]) -> list[R]:
        table, keys, _ = _TABLES[record_type]
        rows = self._db.execute(f"SELECT payload FROM {table} ORDER BY {', '.join(keys)}")
        return [record_type.from_dict(json.loads(payload)) for (payload,) in rows]

    def raw_for_candidate(self, candidate_id: str) -> RawObservation:
        """Follow the immutable source-edition binding selected at insertion."""
        row = self._db.execute(
            "SELECT raw_record_id FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise ContractViolation(f"missing NormalizedObservation {candidate_id!r}")
        return self._required(RawObservation, row[0])

    def _required(self, record_type: type[R], key: str | tuple[str, ...]) -> R:
        record = self.get(record_type, key)
        if record is None:
            raise ContractViolation(f"missing {record_type.__name__} {key!r}")
        return record

    def _require_transaction(self) -> None:
        if not self._db.in_transaction:
            raise RuntimeError("store writes require transaction()")

    def add_source(self, source: Source, content: bytes) -> bool:
        """Register exact source bytes as verified after computing SHA-256."""
        self._require_transaction()
        actual = hashlib.sha256(content).hexdigest()
        if actual != source.sha256:
            raise SourceDigestMismatch(f"{source.source_key}: expected {source.sha256}, received {actual}")
        self.add(source)
        cursor = self._db.execute(
            "INSERT INTO verified_sources VALUES (?, ?, ?) ON CONFLICT(source_key, sha256) DO NOTHING",
            (source.source_key, source.sha256, len(content)),
        )
        return cursor.rowcount == 1

    def source_verified(self, source_key: str, sha256: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM verified_sources WHERE source_key = ? AND sha256 = ?", (source_key, sha256)
        ).fetchone() is not None

    def add(self, record: Record) -> bool:
        """Insert an immutable record once. An identical replay returns False."""
        self._require_transaction()
        table, keys, extra_columns = _TABLES[type(record)]
        key = tuple(getattr(record, name) for name in keys)
        existing = self._get_exact(type(record), key)
        payload = record.to_dict()
        if existing is not None:
            if canonical_json(existing.to_dict()) != canonical_json(payload):
                raise RecordConflict(f"{type(record).__name__} {key!r} already has different contents")
            return False

        self._validate_links(record)
        columns = (*keys, *extra_columns, "payload")
        placeholders = ", ".join("?" for _ in columns)
        values = [getattr(record, column) for column in columns[:-1]]
        if isinstance(record, NormalizedObservation):
            raw_key = self._candidate_raw_key(record)
            values[-1] = raw_key
        try:
            self._db.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                (*values, canonical_json(payload)),
            )
        except sqlite3.IntegrityError as error:
            if error.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE":
                raise RecordConflict(f"{type(record).__name__} {key!r} conflicts with an existing identity: {error}") from error
            raise ContractViolation(str(error)) from error

        if isinstance(record, ValidatedObservation):
            self._db.executemany(
                "INSERT INTO validation_anchors VALUES (?, ?)",
                [(record.validation_id, anchor_id) for anchor_id in record.evidence_anchor_ids],
            )
        elif isinstance(record, ResearchRelease):
            self._db.executemany(
                "INSERT INTO release_projects VALUES (?, ?)",
                [(record.release_id, project_id) for project_id in record.cohort],
            )
        elif isinstance(record, DerivedValue):
            self._db.executemany(
                "INSERT INTO derived_inputs VALUES (?, ?, ?)",
                [
                    (record.derived_id, i, self._required(RawObservation, observation_id).raw_record_id)
                    for i, observation_id in enumerate(record.input_observation_ids)
                ],
            )
        return True

    def _require_project(self, project_id: str) -> Entity:
        project = self._required(Entity, project_id)
        if project.kind != EntityKind.PROJECT:
            raise ContractViolation(f"{project_id!r} must be a project")
        return project

    def _validate_links(self, record: Record) -> None:
        if isinstance(record, Entity) and record.kind == EntityKind.DEPOSIT:
            self._require_project(record.parent_id)
        elif isinstance(record, ResourceScope):
            self._require_project(record.project_id)
            if record.deposit_id is not None:
                deposit = self._required(Entity, record.deposit_id)
                if deposit.kind != EntityKind.DEPOSIT or deposit.parent_id != record.project_id:
                    raise ContractViolation("scope deposit must belong to its project")
            if record.parent_scope_id is not None:
                parent = self._required(ResourceScope, record.parent_scope_id)
                same_deposit = parent.deposit_id is None or record.deposit_id == parent.deposit_id
                if parent.project_id != record.project_id or not same_deposit:
                    raise ContractViolation("parent scope belongs to another project or deposit")
        elif isinstance(record, RawObservation):
            self._validate_raw(record)
        elif isinstance(record, ValidatedObservation):
            self._validate_assessment(record)
        elif isinstance(record, ResearchRelease):
            for project_id in record.cohort:
                self._require_project(project_id)
        elif isinstance(record, AcceptedResearchValue):
            self._validate_acceptance(record)
        elif isinstance(record, DerivedValue):
            self._validate_derivation(record)

    def _validate_raw(self, raw: RawObservation) -> None:
        scope = self._required(ResourceScope, raw.scope_id)
        if scope.project_id != raw.project_id or (raw.deposit_id is not None and raw.deposit_id != scope.deposit_id):
            raise ContractViolation("observation scope must identify the same project and deposit")
        if raw.anchor_id is not None:
            anchor = self._required(EvidenceAnchor, raw.anchor_id)
            if (anchor.source_key, anchor.source_sha) != (raw.source_key, raw.source_sha):
                raise ContractViolation("observation evidence is from another source version")

    def _validate_assessment(self, assessment: ValidatedObservation) -> None:
        candidate = self._required(NormalizedObservation, assessment.candidate_id)
        raw = self.raw_for_candidate(candidate.candidate_id)
        anchors = [self._required(EvidenceAnchor, key) for key in assessment.evidence_anchor_ids]
        if assessment.outcome != ValidationOutcome.VALIDATED:
            return

        source_key = (raw.source_key, raw.source_sha)
        source = self._required(Source, source_key)
        if not self.source_verified(*source_key):
            raise ContractViolation("validated observations require verified source bytes")
        if raw.anchor_id not in assessment.evidence_anchor_ids:
            raise ContractViolation("validated observations require their original evidence anchor")
        raw_anchor = self._required(EvidenceAnchor, raw.anchor_id)
        if any(
            (anchor.source_key, anchor.source_sha) != source_key or anchor.page != raw_anchor.page
            or (anchor.page is None and not anchor.locator)
            for anchor in anchors
        ):
            raise ContractViolation("this evidence profile requires anchored evidence from the same source page")
        if raw.raw_value is None or candidate.value is None:
            raise ContractViolation("validated observations require a reported and normalized value")
        dates = (
            source.publication_date, source.available_date, raw.reported_publication_date,
            raw.effective_date, raw.event_date,
        )
        if not (source.publication_date or source.available_date) or any(
            _latest_possible_day(value) > date.fromisoformat(assessment.cutoff) for value in dates if value
        ):
            raise ContractViolation("historical availability and observation dates must be within the cutoff")
        if assessment.comparable:
            bases = (
                (candidate.material_basis, raw.material_basis),
                (candidate.chemical_basis, raw.chemical_basis),
                (candidate.denominator_basis, raw.denominator_basis),
            )
            if any(normalized != original for normalized, original in bases):
                raise ContractViolation("this evidence profile does not authorize unbound basis changes")
            if not raw.statement_id or not raw.category or "exploration target" in raw.category.casefold().replace("_", " "):
                raise ContractViolation("comparable resource values require an identifiable non-target statement and category")

    def _validate_acceptance(self, accepted: AcceptedResearchValue) -> None:
        release = self._required(ResearchRelease, accepted.release_id)
        validation = self._required(ValidatedObservation, accepted.validation_id)
        raw = self.raw_for_candidate(validation.candidate_id)
        if validation.outcome != ValidationOutcome.VALIDATED or not validation.comparable:
            raise ContractViolation("this selection profile requires a validated, comparable observation")
        if validation.cutoff != release.cutoff or validation.rules_sha != release.rules_sha:
            raise ContractViolation("selection cutoff and rules must match the release")
        if raw.project_id not in release.cohort:
            raise ContractViolation("selected observation project is outside the release cohort")

    def _validate_derivation(self, derived: DerivedValue) -> None:
        scope = self._required(ResourceScope, derived.scope_id)
        release = self._required(ResearchRelease, derived.release_id)
        if scope.project_id != derived.project_id or derived.project_id not in release.cohort:
            raise ContractViolation("derived value is outside its Project scope or release cohort")
        cohort_observations = self._db.execute(
            """SELECT candidate.raw_record_id FROM accepted_values AS accepted
               JOIN validations AS validation USING (validation_id)
               JOIN candidates AS candidate USING (candidate_id)
               WHERE accepted.release_id = ?""",
            (derived.release_id,),
        )
        accepted_inputs = {row[0] for row in cohort_observations}
        for observation_id in derived.input_observation_ids:
            observed = self._required(RawObservation, observation_id)
            if observed.scope_id != derived.scope_id or observed.raw_record_id not in accepted_inputs:
                raise ContractViolation("derived inputs must be accepted for the same scope and release")

    def _candidate_raw_key(self, candidate: NormalizedObservation) -> str:
        raw = (
            self._get_exact(RawObservation, candidate.raw_record_id)
            if candidate.raw_record_id is not None
            else self.get(RawObservation, candidate.observation_id)
        )
        if raw is None or raw.observation_id != candidate.observation_id:
            raise ContractViolation("candidate must identify the exact original observation edition")
        return raw.raw_record_id

    def integrity(self) -> dict:
        return {
            "integrity_check": [row[0] for row in self._db.execute("PRAGMA integrity_check")],
            "foreign_key_check": [list(row) for row in self._db.execute("PRAGMA foreign_key_check")],
        }


def _latest_possible_day(value: str) -> date:
    """Preserve month/year roles while conservatively comparing against daily cutoffs."""
    if len(value) > 10:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    year, *rest = value.split("-")
    if not rest:
        return date(int(year), 12, 31)
    month, *days = rest
    if not days:
        return date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])
    return date.fromisoformat(value)

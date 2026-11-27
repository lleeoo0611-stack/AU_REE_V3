import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from au_ree_v3.contracts import AcceptedResearchValue, Entity, EvidenceAnchor, RawObservation, ResourceScope, Source
from au_ree_v3.migration.crosswalk import observation_raw_id
from au_ree_v3.migration.v2_import import import_v2_observations, read_v2_sqlite
from au_ree_v3.store import RecordConflict, Store


class V2ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source_asset = self.root / "original.txt"
        self.source_asset.write_bytes(b"Synthetic fixture: total is 100.0000 ppm")
        self.sha = hashlib.sha256(self.source_asset.read_bytes()).hexdigest()
        self.source = Source(source_key="original", sha256=self.sha, source_version="first",
                             url="https://example.invalid/original.txt", kind="REPORT")
        self.identity = ("original", self.sha)
        self.sources = {self.identity: self.source}
        self.assets = {self.identity: self.source_asset}
        self.store = self.enterContext(Store(self.root / "v3.sqlite"))
        self.path = self.root / "v2-export.jsonl"
        with self.store.transaction():
            self.store.add(Entity(entity_id="AU-one", kind="PROJECT", name="Same Name",
                                  native_namespace="synthetic", native_id="1"))
            self.store.add(Entity(entity_id="AU-two", kind="PROJECT", name="Same Name",
                                  native_namespace="synthetic", native_id="2"))
            self.store.add(ResourceScope(scope_id="AU-one/north", project_id="AU-one", name="North"))
            self.store.add(ResourceScope(scope_id="AU-two/north", project_id="AU-two", name="North"))

    def payload(self, observation_id, **overrides):
        return {
            "schema_version": "observation-v3", "observation_id": observation_id, "project_id": "AU-one",
            "scope_id": "AU-one/north", "variable": "grade", "quantity_kind": "RESOURCE",
            "evidence": {
                "source_id": "original", "sha256": self.sha, "page": 2, "table": 0,
                "row": 0, "column": 3, "quote": "100.0000", "bbox": [1, 2, 3, 4],
                "locator": "physical page 2 row 0", "role": "token", "offset": 0,
                "text_span": {"scope": "QUOTE", "view": "plain", "start": 0, "end": 8, "quote_start": 0},
            },
            "extractor_version": "v2-synthetic", "original_value": "100.0000", "original_unit": "ppm",
            "deposit_id": None, "material_basis": "WHOLE_ORE", "chemical_basis": "TREO",
            "denominator_basis": "ORE_MASS", "context": {"conditions": [["category", "INDICATED"]]},
            "effective_date": "2025-03", "publication_date": "2025-04-03", "acquisition_date": None,
            "statement_id": "estimate-1", "estimate_id": "national-example", "related_observation_ids": [],
            "manual_decisions": [], "source_value_state": None, "extraction_fingerprint": None,
            "evidence_anchors": [], **overrides,
        }

    def write(self, *payloads):
        self.path.write_text("".join(json.dumps(payload) + "\n" for payload in payloads), encoding="utf-8")

    def run_import(self, assets=None, **kwargs):
        return import_v2_observations(self.store, self.path, sources=self.sources,
                                      assets=self.assets if assets is None else assets, **kwargs)

    def test_replay_preserves_exact_source_tokens_raw_identity_and_evidence(self):
        first = self.payload("published-1")
        first["raw_id"] = observation_raw_id(first)
        second = self.payload("published-2", original_value=None, original_unit=None,
                              source_value_state="NOT_DISCLOSED")
        self.write(first, second)
        original = self.path.read_bytes()
        run = self.run_import()
        replay = self.run_import()
        self.assertEqual(run["status"], "PASS")
        self.assertTrue(all(row["inserted"] for row in run["results"]))
        self.assertTrue(all(not row["inserted"] for row in replay["results"]))
        self.assertEqual(self.path.read_bytes(), original)
        raw = self.store.get(RawObservation, first["raw_id"])
        self.assertEqual(raw.observation_id, "published-1")
        self.assertEqual(raw.raw_value, "100.0000")
        self.assertEqual(raw.effective_date, "2025-03")
        self.assertEqual(raw.reported_publication_date, "2025-04-03")
        self.assertEqual(raw.denominator_basis, "ORE_MASS")
        self.assertEqual(raw.original_payload, original.decode().splitlines()[0])
        anchor = self.store.get(EvidenceAnchor, raw.anchor_id)
        self.assertEqual((anchor.page, anchor.table, anchor.row, anchor.column, anchor.offset), (2, 0, 0, 3, 0))
        self.assertEqual(anchor.text_span["end"], 8)
        self.assertIsNone(self.store.get(RawObservation, "published-2").raw_value)
        self.assertEqual(self.store.get(RawObservation, "published-2").null_reason, "NOT_DISCLOSED")
        self.assertFalse(self.store.records(AcceptedResearchValue))

    def test_isolate_missing_scope_wrong_scope_and_invalid_legacy_raw_id(self):
        missing = self.payload("missing", scope_id=None)
        wrong = self.payload("wrong", scope_id="AU-two/north")
        altered = self.payload("altered", raw_id="0" * 64)
        good = self.payload("good")
        self.write(missing, wrong, altered, good)
        report = self.run_import()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual([row["status"] for row in report["results"]],
                         ["EVIDENCE_INSUFFICIENT", "SCIENTIFIC_CONFLICT", "SCIENTIFIC_CONFLICT", "IMPORTED"])
        self.assertEqual([r.observation_id for r in self.store.records(RawObservation)], ["good"])

    def test_missing_and_wrong_assets_never_verify_then_exact_replay_verifies(self):
        self.write(self.payload("observation"))
        self.assertEqual(self.run_import({})["results"][0]["status"], "SOURCE_UNAVAILABLE")
        bad = self.root / "wrong.txt"
        bad.write_bytes(b"Changed after freeze")
        self.assertEqual(self.run_import({self.identity: bad})["results"][0]["status"], "SCIENTIFIC_CONFLICT")
        self.assertFalse(self.store.source_verified(*self.identity))
        self.assertFalse(self.run_import()["results"][0]["inserted"])
        self.assertTrue(self.store.source_verified(*self.identity))

    def test_corrupt_batch_rolls_back_staged_rows_and_byte_verifications(self):
        item = self.payload("same-identity")
        conflict = {**item, "original_value": "200.0000"}
        self.write(item, conflict)
        with self.assertRaises(RecordConflict):
            self.run_import()
        self.assertFalse(self.store.records(RawObservation))
        self.assertFalse(self.store.records(EvidenceAnchor))
        self.assertFalse(self.store.records(Source))
        self.assertFalse(self.store.source_verified(*self.identity))

    def test_same_legacy_scope_text_can_be_explicitly_mapped_under_distinct_projects(self):
        self.write(self.payload("one", scope_id="North"),
                   self.payload("two", project_id="AU-two", scope_id="North"))
        report = self.run_import(scope_links={("AU-one", "North"): "AU-one/north",
                                              ("AU-two", "North"): "AU-two/north"})
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(self.store.get(RawObservation, "two").scope_id, "AU-two/north")
        original = json.loads(self.store.get(RawObservation, "two").original_payload)
        self.assertEqual(original["scope_id"], "North")

    def test_read_v2_sqlite_preserves_uninterpreted_value_and_original_bytes(self):
        path = self.root / "v2.sqlite"
        connection = sqlite3.connect(path)
        connection.executescript("""
            PRAGMA user_version = 1;
            CREATE TABLE sources (sha TEXT PRIMARY KEY, source_key TEXT, metadata TEXT);
            CREATE TABLE observations (obs_id TEXT PRIMARY KEY, source_sha TEXT, value REAL, raw TEXT);
            INSERT INTO sources VALUES ('hash', 'key', '{}');
            INSERT INTO observations VALUES ('original', 'hash', 0, '{"original_token":"0.0000"}');
        """)
        connection.close()
        original = path.read_bytes()
        rows = read_v2_sqlite(path, ["original", "missing"])
        self.assertEqual(rows[0]["observation"]["value"], 0.0)
        self.assertEqual(rows[0]["observation"]["raw"], '{"original_token":"0.0000"}')
        self.assertEqual(rows[0]["source"]["source_key"], "key")
        self.assertEqual(rows[1]["status"], "NOT_FOUND")
        self.assertEqual(path.read_bytes(), original)

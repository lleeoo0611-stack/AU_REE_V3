"""Read-only regression cases for the legacy resource lead locator."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "recover_legacy_value_hints.py"
spec = importlib.util.spec_from_file_location("recover_legacy_value_hints", SCRIPT)
recover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recover)


class LegacyResourceHintsTests(unittest.TestCase):
    def test_pinned_provenance_matches_the_existing_census_contract(self):
        contract = SCRIPT.parents[1] / "config/research_profiles/census_columns_v1.json"
        reference = json.loads(contract.read_bytes())["historical_reference"]
        self.assertEqual(recover.V2_REF, reference["artifact_commit"])
        self.assertRegex(recover.V2_PROVENANCE_SHA256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            recover.V2_PROVENANCE_SHA256,
            reference["artifacts"][recover.V2_PROVENANCE_PATH]["sha256"],
        )

    @unittest.skipUnless(os.environ.get("AU_REE_V2_REFERENCE"), "the pinned V2 Git reference is required")
    def test_actual_pinned_reference_has_complete_grid(self):
        digest, rows = recover._read_v2(Path(os.environ["AU_REE_V2_REFERENCE"]))
        self.assertEqual(digest, recover.V2_PROVENANCE_SHA256)
        self.assertEqual(len(rows), 93 * 51)

    def test_fixed_page_first_and_secondary_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite"
            with sqlite3.connect(path) as db:
                db.executescript("""
                    CREATE TABLE spans(rowid INTEGER PRIMARY KEY, span_id TEXT, source_sha256 TEXT,
                         locator TEXT, page INTEGER, original_text TEXT);
                    CREATE VIRTUAL TABLE spans_fts USING fts5(original_text, content='spans', content_rowid='rowid');
                    """)
                sha = "a"*64
                texts = [(1,"Mineral Resource table TREO tonnage"),(4,"Nd2O3 Pr6O11 rare earth oxide"),
                         (8,"JORC code and cut off 2025"),(9,"More mineral resource tonnage")]
                for i,(page,text) in enumerate(texts,1):
                    db.execute("INSERT INTO spans VALUES (?,?,?,?,?,?)",(i,str(i),sha,str(page),page,text))
                db.execute("INSERT INTO spans_fts(spans_fts) VALUES('rebuild')")
                plan = recover._page_plan(db, sha, (9,))
                self.assertEqual(plan["value_pages"][0], 9)
                self.assertIn(4, plan["value_pages"])
                self.assertIn(8, plan["context_pages"])
            db.close()

    def test_source_exact_project_binding_and_immutable_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); index=root/"index.sqlite"
            sha="b"*64
            with sqlite3.connect(index) as db:
                db.executescript("""
                   CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
                   CREATE TABLE sources(source_sha256 TEXT PRIMARY KEY,kind TEXT,status TEXT,detail TEXT,decoder TEXT,span_count INTEGER);
                   CREATE TABLE source_projects(source_sha256 TEXT,project_id TEXT);
                   CREATE TABLE spans(rowid INTEGER PRIMARY KEY,span_id TEXT,source_sha256 TEXT,locator TEXT,page INTEGER,original_text TEXT);
                   CREATE VIRTUAL TABLE spans_fts USING fts5(original_text,content='spans',content_rowid='rowid');
                """)
                for key,value in {"schema":recover.INDEX_SCHEMA,"candidate_only":True,"source_manifest_sha256":"c"*64}.items():
                    db.execute("INSERT INTO metadata VALUES (?,?)",(key,json.dumps(value)))
                db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?)",(sha,"pdf","INDEXED",None,"test",1))
                db.execute("INSERT INTO source_projects VALUES (?,?)",(sha,"AU-good"))
                db.execute("INSERT INTO spans VALUES (1,'one',?,?,3,'mineral resource TREO JORC')",(sha,"PDF page 3"))
                db.execute("INSERT INTO spans_fts(spans_fts) VALUES('rebuild')")
            db.close()
            fake=[{"project_id":"AU-good","column":"TREO品位_pct","value":"0.5","source_sha256":sha,
                   "pdf_page":"3","locator":"p3","method":"SOURCE_REPORTED","scope":"MRE"},
                  {"project_id":"AU-other","column":"礦石蘊藏量_Mt","value":"1","source_sha256":sha,
                   "pdf_page":"3","locator":"p3","method":"SOURCE_REPORTED","scope":"MRE"},
                  {"project_id":"AU-good","column":"Nd2O3_氧化釹_kt","value":"3","source_sha256":"d"*64,
                   "pdf_page":"3","locator":"p3","method":"DERIVED","scope":"MRE"},
                  {"project_id":"AU-good","column":"總TREO蘊藏量_kt","value":"1","source_sha256":"",
                   "pdf_page":"3","locator":"p3","method":"SOURCE_REPORTED","scope":"MRE"},
                  {"project_id":"AU-good","column":"國家","value":"澳洲","source_sha256":sha,
                   "pdf_page":"","locator":"","method":"SCOPE","scope":""}]
            with patch.object(recover,"_read_v2",return_value=("e"*64,fake)):
                out=root/"output"
                result=recover.run(v2_root=root/"readonly-v2",index_path=index,out=out)
                self.assertEqual(result["numeric_value_hints"],4)
                self.assertEqual(result["formal_scientific_values_added"],0)
                self.assertEqual(result["unique_source_project_work_items"], 1)
                queue=json.loads((out/"source_project_work_queue.json").read_text())
                self.assertEqual(len(queue["rows"]), 1)
                self.assertEqual(queue["rows"][0]["value_hints"], 1)
                self.assertTrue(queue["rows"][0]["read_original_cas_again"])
                self.assertFalse(queue["rows"][0]["formal_admission"])
                leads=[json.loads(x) for x in (out/"legacy_value_leads.jsonl").read_text().splitlines()]
                self.assertEqual(leads[0]["status"],"SOURCE_AND_LEGACY_PAGES_LOCATED")
                self.assertEqual(leads[1]["status"],"PROJECT_SOURCE_ASSOCIATION_MISSING")
                self.assertEqual(leads[2]["status"],"SOURCE_EDITION_NOT_IN_V3")
                self.assertEqual(leads[3]["status"],"NO_LEGACY_SOURCE_SHA")
                self.assertFalse(any(row["formal_observation_created"] for row in leads))
                self.assertFalse(any({"legacy_display_hint", "legacy_scope_hint", "legacy_locator_hint"} & row.keys()
                                     for row in leads))
                self.assertEqual(result["leads_sha256"],__import__("hashlib").sha256((out/"legacy_value_leads.jsonl").read_bytes()).hexdigest())

    def test_immutable_prior_results_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            out=root/"prior-result"
            out.mkdir()
            marker=out/"previous.json"
            marker.write_text("untouched")
            with self.assertRaises(FileExistsError):
                recover.run(v2_root=root,index_path=root/"not-needed.sqlite",out=out)
            self.assertEqual(marker.read_text(), "untouched")

    def test_strict_page_and_column_selection(self):
        self.assertEqual(recover._pages("4; 5,6"),[4,5,6])
        self.assertEqual(recover._pages("bad 4"),[])
        self.assertEqual(recover._target("CeO2_氧化鈰_kt"),"OXIDE")
        self.assertEqual(recover._target("開發商"),None)
        self.assertEqual(recover._pages("²"), [])

    def test_v2_checkout_cannot_be_an_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            with self.assertRaisesRegex(ValueError, "read-only V2 checkout"):
                recover.run(v2_root=root, index_path=root / "unused.sqlite", out=output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

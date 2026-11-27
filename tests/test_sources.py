from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from au_ree_v3.sources import ContentStore, HttpResult, IntegrityError, acquire_sources, combine_acquisition, discover_sources, validate_capture_proof


PDF_A = b"%PDF-1.4\nsynthetic source one\n%%EOF"
PDF_B = b"%PDF-1.4\nsynthetic source two\n%%EOF"
BEFORE = "2026-11-27T18:00:00+00:00"
AFTER = "2026-11-28T16:00:01+00:00"
PROFILE = SimpleNamespace(profile_id="synthetic-test", research_cutoff=date(2026, 11, 28), timezone=ZoneInfo("Asia/Taipei"))


def seed(project, url, expected="pdf"):
    return {"project_ids": [project], "source_key": project, "url": url, "kind": expected,
            "priority_by_project": {project: 1}, "historical_sha256": [], "reference": "synthetic"}


def fetched(url, body=PDF_A, at=BEFORE, mime="application/pdf"):
    return HttpResult("FETCHED", url, at, 200, mime, body)


class SourceCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ContentStore(self.root / "store")

    def acquire(self, name, seeds, results):
        ids = tuple(dict.fromkeys(project for entry in seeds for project in entry["project_ids"]))
        with patch("au_ree_v3.sources._profile_inputs", return_value=(PROFILE, ids, seeds)):
            return acquire_sources(Path("fake-profile"), self.root / name, store=self.store.root,
                                   fetcher=lambda url, method, timeout, max_bytes: results[url])

    def test_same_url_changed_bytes_adds_immutable_edition(self):
        url = "https://publisher.test/report.pdf"
        first = self.acquire("first", [seed("P1", url)], {url: fetched(url)})
        second = self.acquire("second", [seed("P1", url)], {url: fetched(url, PDF_B, AFTER)})
        old, new = first["editions"][0], second["editions"][0]
        self.assertNotEqual(old["edition_id"], new["edition_id"])
        self.assertEqual(new["same_url_predecessor_sha256"], [old["sha256"]])
        self.assertEqual(new["version_status"], "NEW_SHA")
        self.assertTrue(new["source_changed"])
        self.assertEqual(new["temporal_decision"]["status"], "CANDIDATE")
        self.assertEqual(self.store.read(old["sha256"]), PDF_A)
        self.assertEqual(self.store.read(new["sha256"]), PDF_B)
        self.assertEqual(len(list((self.store.root / "captures").iterdir())), 2)

    def test_alternate_url_same_sha_reuses_one_object_and_earlier_exact_public_capture(self):
        original = "https://publisher.test/report.pdf"
        alternate = "https://archive.test/report.pdf"
        first = self.acquire("first", [seed("P1", original)], {original: fetched(original)})
        second = self.acquire("second", [seed("P1", alternate)], {alternate: fetched(alternate, at=AFTER)})
        old, new = first["editions"][0], second["editions"][0]
        self.assertEqual(new["sha256"], old["sha256"])
        self.assertEqual(new["version_status"], "EXACT_SHA_REUSED")
        self.assertEqual(new["reacquired_at"], AFTER)
        self.assertEqual(new["temporal_decision"]["earliest_proven_public_date"], "2026-11-28")
        self.assertEqual(new["temporal_decision"]["status"], "ELIGIBLE")
        self.assertEqual(len(new["temporal_evidence"]), 2)
        self.assertEqual(len(list((self.store.root / "objects").iterdir())), 3)

    def test_taipei_date_and_both_explicit_offsets_survive(self):
        url = "https://publisher.test/report.pdf"
        original = "2026-11-28T02:00:00+08:00"
        observed = self.acquire("fresh", [seed("P1", url)], {url: fetched(url, at=original)})
        edition = observed["editions"][0]
        self.assertEqual(edition["temporal_evidence"][0]["public_on"], "2026-11-28")
        self.assertEqual(observed["events"][0]["acquired_at"], original)
        self.assertEqual(observed["events"][0]["acquired_at_utc"], BEFORE)
        self.assertEqual(observed["events"][0]["acquired_at_research_timezone"], original)
        self.assertIsNone(edition["temporal_decision"]["available_date"])
        self.assertEqual(observed["replica_inventory"]["status"], "REPLICA_UNAVAILABLE")

    def test_error_rows_are_per_project_and_invalid_html_is_not_an_edition(self):
        letter_codes = ["ok", "notfound", "forbidden", "timeout", "badmime", "interrupted"]
        outcomes = {
            "notfound": "HTTP_404", "forbidden": "HTTP_403", "timeout": "TIMEOUT",
            "interrupted": "DOWNLOAD_INTERRUPTED",
        }
        urls = {key: f"https://{key}.test/report.pdf" for key in letter_codes}
        results = {urls[key]: HttpResult(status, urls[key], BEFORE) for key, status in outcomes.items()}
        results[urls["badmime"]] = fetched(urls["badmime"], b"<!doctype html><html><title>Unauthorized</title>", mime="text/html")
        results[urls["ok"]] = fetched(urls["ok"])
        result = self.acquire("mixed", [seed(key, urls[key]) for key in letter_codes], results)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["counts"]["projects_searched"], len(letter_codes))
        self.assertEqual(result["counts"]["projects_with_source"], 1)
        self.assertEqual(result["counts"]["event_statuses"], {
            "PUBLIC_CAPTURED": 1, "HTTP_404": 1, "HTTP_403": 1, "TIMEOUT": 1,
            "MIME_MISMATCH": 1, "DOWNLOAD_INTERRUPTED": 1,
        })

    def test_discovery_receipt_distinguishes_unchecked_project(self):
        url = "https://publisher.test/report.pdf"
        attempted = []

        def fetch(url, method, timeout, max_bytes):
            attempted.append(method)
            return HttpResult("SOURCE_LOCATOR_REACHABLE", url, BEFORE, 200, "application/pdf")

        with patch("au_ree_v3.sources._profile_inputs", return_value=(PROFILE, ("P1", "P2"), [seed("P1", url)])):
            report = discover_sources(Path("profile"), self.root / "discover", fetcher=fetch)
        self.assertEqual(attempted, ["HEAD"])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual([item["status"] for item in report["projects"]], ["SOURCE_LOCATOR_REACHABLE", "NOT_SEARCHED"])

    def test_capture_proof_requires_exact_primary_object_read_back(self):
        url = "https://publisher.test/report.pdf"
        result = self.acquire("good", [seed("P1", url)], {url: fetched(url)})
        edition = result["editions"][0]
        digest = sha256(PDF_A).hexdigest()
        self.assertEqual(validate_capture_proof(self.store, edition["receipt_sha256"], digest, PROFILE.timezone).source_sha256, digest)
        self.store.path(digest).write_bytes(PDF_B)
        with self.assertRaisesRegex(IntegrityError, "SHA-256 mismatch"):
            validate_capture_proof(self.store, edition["receipt_sha256"], digest, PROFILE.timezone)

    def test_combiner_revalidates_captures_and_preserves_every_attempt(self):
        first_url = "https://publisher-one.test/report.pdf"
        second_url = "https://publisher-two.test/report.pdf"
        self.acquire("first", [seed("P1", first_url)], {first_url: fetched(first_url)})
        self.acquire("second", [seed("P2", second_url)], {
            second_url: HttpResult("HTTP_403", second_url, BEFORE, 403),
        })
        first = self.root / "first/source_manifest.json"
        second = self.root / "second/source_manifest.json"
        with patch("au_ree_v3.sources._profile_inputs", return_value=(PROFILE, ("P1", "P2"), [])):
            combined = combine_acquisition(Path("profile"), [first, second, first], self.root / "combined",
                                           store=self.store.root)
            self.assertEqual(combined["counts"]["projects_searched"], 2)
            self.assertEqual(combined["counts"]["event_statuses"], {"PUBLIC_CAPTURED": 1, "HTTP_403": 1})
            self.assertEqual(combined["primary_readback"]["verified_objects"], 2)
            tampered = json.loads(first.read_bytes())
            tampered["editions"][0]["temporal_decision"]["status"] = "CANDIDATE"
            first.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(IntegrityError, "Temporal decision cannot be reproduced"):
                combine_acquisition(Path("profile"), [first, second], self.root / "invalid", store=self.store.root)

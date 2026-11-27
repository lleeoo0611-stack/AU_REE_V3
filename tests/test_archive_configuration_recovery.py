from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.recover_archive_configuration import _store_exact, recover_bytes, recover_configuration


class ConfigurationRecoveryTests(unittest.TestCase):
    def test_exact_fixed_bytes_are_preserved(self):
        raw = b'{\n  "version": 1\n}\n'
        data, method = recover_bytes("research.json", raw, sha256(raw).hexdigest())
        self.assertEqual(data, raw)
        self.assertEqual(method, "EXACT_FIXED_SCIENCE_GIT_BLOB")

    def test_summary_rules_crlf_is_allowed_only_for_an_exact_recorded_digest(self):
        raw = b'{\n  "version": 1\n}\n'
        archived = raw.replace(b"\n", b"\r\n")
        expected = sha256(archived).hexdigest()
        self.assertEqual(recover_bytes("summary_rules.json", raw, expected),
                         (archived, "FIXED_SCIENCE_GIT_BLOB_LF_TO_CRLF"))
        with self.assertRaisesRegex(ValueError, "Archived configuration SHA unavailable"):
            recover_bytes("research.json", raw, expected)
        with self.assertRaisesRegex(ValueError, "Archived configuration SHA unavailable"):
            recover_bytes("summary_rules.json", raw, "0" * 64)

    def test_content_address_replay_does_not_overwrite(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "asset"
            _store_exact(target, b"frozen")
            _store_exact(target, b"frozen")
            with self.assertRaisesRegex(ValueError, "Conflicting bytes"):
                _store_exact(target, b"different")
            self.assertEqual(target.read_bytes(), b"frozen")

    def test_output_inside_v2_is_rejected_before_writing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "work"
            with self.assertRaisesRegex(ValueError, "outside the V2"):
                recover_configuration(root, out)
            self.assertFalse(out.exists())

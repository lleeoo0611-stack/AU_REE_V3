"""Verify the read-only importer on the fixed Mt Weld V2 resource statement."""

import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from au_ree_v3.contracts import AcceptedResearchValue, Entity, EvidenceAnchor, RawObservation, ResourceScope, Source
from au_ree_v3.migration.crosswalk import digest
from au_ree_v3.migration.v2_import import import_v2_observations, read_v2_sqlite
from au_ree_v3.store import Store
from v2_resource_fixture import SCIENCE_REF, V2_ENTRY, extract_reference, fixture_config, git

ENGINEERING = "2af6f611e51eb4ac9dda73419d1afeba70d1307e"
ARCHIVE = "results/ree-project-census/coverage-locked-20261125/resource_statement_observations.csv"
BINDING = Path(__file__).resolve().parents[1] / "config/migration/mt_weld_resource_binding_v1.json"
VARIABLES = {"ore_mt", "grade", "contained_treo_kt"}
EXPORT_V2 = """
import json
import sys
from au_ree_v2.observation_pipeline import canonical_resource_observation
from au_ree_v2.observation_schema import payload
from au_ree_v2.observation_store import open_observation_workspace
with open_observation_workspace(sys.argv[1]) as workspace:
    for identity in sys.argv[2:]:
        raw = canonical_resource_observation(workspace.db, identity)
        print(json.dumps({**payload(raw), "raw_id": raw.raw_id}, sort_keys=True))
"""


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def export_v2(root, python, database, identities, destination):
    # Bridge through the fixed V2 API instead of fabricating a typed V2 payload.
    with tempfile.TemporaryDirectory(prefix="au-ree-frozen-v2-") as directory:
        checkout = Path(directory)
        extract_reference(root, checkout, ENGINEERING, "src")
        result = subprocess.run(
            [str(python.absolute()), "-c", EXPORT_V2, str(database.resolve()), *sorted(identities)],
            cwd=checkout, env={**os.environ, "PYTHONPATH": str(checkout / "src")},
            capture_output=True, check=True,
        )
    destination.write_bytes(result.stdout)
    return {row["observation_id"]: row for row in map(json.loads, result.stdout.splitlines())}


def build_v2_database(args, scratch):
    checkout = scratch / "science"
    checkout.mkdir()
    extract_reference(args.v2_root, checkout, SCIENCE_REF, "src", "config", "data/claims.json")
    database = scratch / "v2.sqlite"
    runner = Path(__file__).with_name("v2_resource_fixture.py").resolve()
    result = subprocess.run(
        [str(args.v2_python.absolute()), "-B", str(runner), str(checkout),
         str(args.binding.resolve()), str(args.asset.resolve()), str(database)],
        cwd=checkout, env={**os.environ, "PYTHONPATH": str(checkout / "src")},
        check=True, capture_output=True,
    )
    return database, json.loads(result.stdout)


def verify(args, database, recomputed):
    binding_set, binding = fixture_config(args.binding)
    check(binding["legacy_claim_reference"]["commit"] == SCIENCE_REF, "Unexpected V2 science reference")
    source = Source(**binding["source"])
    project = Entity(**binding["project"], kind="PROJECT")
    scope_label = binding["scope_name"]
    scope = ResourceScope(scope_id=digest(["v2-scope", project.entity_id, scope_label]),
                          project_id=project.entity_id, name=scope_label)
    source_id = (source.source_key, source.sha256)
    check(file_sha(args.asset) == source.sha256, "Mt Weld asset SHA-256 mismatch")
    archive = git(args.v2_root, "cat-file", "blob", f"{ENGINEERING}:{ARCHIVE}")
    rows = list(csv.DictReader(io.StringIO(archive.decode("utf-8-sig"))))
    selected = [row for row in rows if (
        row["project_id"] == project.entity_id and row["source_sha256"] == source.sha256
        and row["statement_id"] == binding["statement_id"] and row["scope"] == scope_label
        and row["variable"] in VARIABLES and row["selected_for_census"] == "1"
    )]
    check(len(selected) == len(VARIABLES) and {row["variable"] for row in selected} == VARIABLES,
          "Frozen V2 archive does not resolve this resource binding uniquely")
    expected = {row["observation_id"]: row for row in selected}
    if recomputed is not None:
        check(recomputed["ids"] == sorted(expected), "Strict V2 selection disagrees with archived Observation IDs")
        check({key: Decimal(value) for key, value in recomputed["values"].items()}
              == {row["variable"]: Decimal(row["value"]) for row in selected},
              "Strict V2 selection disagrees with archived scientific values")
    before = file_sha(database)
    for row in read_v2_sqlite(database, sorted(expected)):
        check(row["status"] == "READ", "V2 SQLite evidence is absent")
        observed = row["observation"]
        archived = expected[row["observation_id"]]
        for key in ("scope", "statement_id", "category", "effective_date", "locator", "variable", "unit", "basis"):
            check(observed[key] == archived[key], f"V2 SQLite {key} differs from the archive")
        check(observed["entity_id"] == project.entity_id and observed["source_sha"] == source.sha256,
              "V2 SQLite observation links differ from the archive")
        check(row["source"]["source_key"] == source.source_key and row["source"]["url"] == source.url,
              "V2 SQLite source metadata differs from the binding")
    args.out.mkdir(parents=True, exist_ok=True)
    export = args.out / "v2_observations.jsonl"
    raw_expected = export_v2(args.v2_root, args.v2_python, database, expected, export)
    check(raw_expected.keys() == expected.keys(), "V2 export dropped or added observation IDs")
    with tempfile.TemporaryDirectory(prefix="au-ree-v3-contract-") as temp:
        with Store(Path(temp) / "store.sqlite") as store:
            with store.transaction():
                store.add(project)
                store.add(scope)
            options = {
                "sources": {source_id: source},
                "assets": {source_id: args.asset},
                "scope_links": {(project.entity_id, scope_label): scope.scope_id},
            }
            first = import_v2_observations(store, export, **options)
            second = import_v2_observations(store, export, **options)
            check(first["status"] == second["status"] == "PASS", "V3 importer did not verify the sample")
            check(all(row["inserted"] for row in first["results"])
                  and all(not row["inserted"] for row in second["results"]), "Replay was not idempotent")
            check(not store.records(AcceptedResearchValue), "Import incorrectly created accepted research values")
            integrity = store.integrity()
            check(integrity == {"integrity_check": ["ok"], "foreign_key_check": []},
                  "V3 SQLite integrity or foreign-key check failed")
            records = []
            for row in store.records(RawObservation):
                original = raw_expected[row.observation_id]
                archived = expected[row.observation_id]
                check(row.native_raw_id == original["raw_id"], "V2 raw identity changed")
                check(row.raw_value == original["original_value"] == (archived["original_token"] or None),
                      "V2 original token or null semantics changed")
                check(row.raw_unit == original["original_unit"] == (archived["original_unit"] or None),
                      "V2 original unit changed")
                check(row.effective_date == original["effective_date"]
                      and row.reported_publication_date == original["publication_date"]
                      and row.acquisition_date == original["acquisition_date"], "V2 date roles changed")
                check(json.loads(row.original_payload) == original, "Original evidence payload changed")
                anchor = store.get(EvidenceAnchor, row.anchor_id)
                check((anchor.page, anchor.locator, anchor.source_sha)
                      == (original["evidence"]["page"], archived["locator"], source.sha256),
                      "Source anchor changed")
                records.append({
                    "observation_id": row.observation_id, "native_raw_id": row.native_raw_id,
                    "variable": row.variable, "original_token": row.raw_value,
                    "original_unit": row.raw_unit, "status": row.status,
                })
    check(file_sha(database) == before, "V2 SQLite content was modified")
    return {
        "status": "PASS", "gate_scope": "G1 real evidence and identity import subcheck",
        "engineering_ref": ENGINEERING, "science_ref": binding["legacy_claim_reference"]["commit"],
        "source_sha256": source.sha256, "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "v2_database_sha256": before, "typed_export_sha256": file_sha(export),
        "v2_entry": V2_ENTRY if recomputed is not None else None,
        "strict_v2_selection": recomputed,
        "v2_raw_schema": first["schema_version"], "research_cutoff": binding_set["research_cutoff"],
        "sqlite_integrity": integrity, "first_import": first, "repeat_import": second, "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("v2-root", "asset", "out"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--binding", type=Path, default=BINDING)
    parser.add_argument("--database", type=Path, help="Existing V2 database; default rebuilds in scratch")
    parser.add_argument("--v2-python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    check(not args.out.resolve().is_relative_to(args.v2_root.resolve()), "Output must be outside the V2 reference")
    with tempfile.TemporaryDirectory(prefix="au-ree-v2-fixture-") as scratch:
        database, recomputed = args.database, None
        if database is None:
            database, recomputed = build_v2_database(args, Path(scratch))
        result = verify(args, database, recomputed)
    receipt = args.out / "frozen_import_receipt.json"
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(receipt)


if __name__ == "__main__":
    main()

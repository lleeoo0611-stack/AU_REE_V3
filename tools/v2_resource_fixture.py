"""Run the pinned V2 source replay in an isolated checkout and database."""

from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

SCIENCE_REF = "0dff6beb5aca52e565c37289e3ab295737e76942"
V2_ENTRY = "store.connect / evidence.ingest_claims / evidence.resource_values"
RESULT_FIELDS = ("project_id", "statement_id", "scope", "effective_date", "source_sha256", "basis")


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"},
    ).stdout


def extract_reference(root, checkout, commit, *paths):
    archive = io.BytesIO(git(root, "archive", commit, *paths))
    with tarfile.open(fileobj=archive) as source:
        source.extractall(checkout, filter="data")


def fixture_config(path):
    config = json.loads(path.read_bytes())
    binding, = config["bindings"]
    return config, binding


def v2_replay(root, config, binding, asset, database):
    from au_ree_v2.evidence import claim_evidence_ids, ingest_claims, resource_values
    from au_ree_v2.store import connect

    source = binding["source"]
    sha = source["sha256"]
    if hashlib.sha256(asset.read_bytes()).hexdigest() != sha:
        raise ValueError("Source digest mismatch")
    snapshot = dict(source, key=source["source_key"], path=str(asset))
    cutoff = config["research_cutoff"]
    native_id = binding["project"]["native_id"]
    claim_reference = binding["legacy_claim_reference"]
    if claim_reference["commit"] != SCIENCE_REF or claim_reference["path"] != "data/claims.json":
        raise ValueError("Unexpected V2 scientific claim reference")
    claim_bytes = (root / claim_reference["path"]).read_bytes()
    if hashlib.sha256(claim_bytes).hexdigest() != claim_reference["sha256"]:
        raise ValueError("Frozen V2 claim archive fingerprint mismatch")
    claims = json.loads(claim_bytes)
    claims["projects"] = [row for row in claims["projects"] if row["id"] == native_id]
    if len(claims["projects"]) != 1:
        raise ValueError("Frozen V2 claims do not resolve one native project")
    db = connect(database)
    try:
        with db:
            db.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                sha, snapshot["key"], source["url"], source["publication_date"],
                source["acquired_at"], str(asset), source["kind"], "{}",
            ))
            ingest_claims(db, root, [snapshot], cutoff, project=native_id, claims_data=claims)
            row = resource_values(
                db, cutoff, {sha}, strict=True, active_claim_ids=claim_evidence_ids(claims),
            )[binding["project"]["entity_id"]]
    finally:
        db.close()
    return {
        "identity": {key: row[source_key] for key, source_key in zip(
            RESULT_FIELDS, ("entity_id", "statement_id", "scope", "date", "source_sha", "basis"), strict=True)},
        "ids": sorted(row["obs_ids"]),
        "values": {key: str(Decimal(str(row[column])).normalize()) for key, column in
                   (("ore_mt", "ore_mt"), ("grade", "grade_pct"), ("contained_treo_kt", "contained_treo_kt"))},
    }


if __name__ == "__main__":
    checkout, bindings, asset, database = map(Path, sys.argv[1:])
    config, binding = fixture_config(bindings)
    print(json.dumps(v2_replay(checkout, config, binding, asset, database)))

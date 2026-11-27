# AU_REE_V3

AU_REE_V3 is rebuilding the Australian rare-earth research pipeline against three fixed AU_REE_V2 commits. The [implementation report](docs/migration/implementation_report.md) records the current acceptance status. The [scientific contract](docs/migration/scientific_contract.md) defines evidence, identity, dates, precision and release semantics.

## Run migration checks

Use Python 3.11 or later and Git. From the repository root:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m unittest discover -s tests -v
```

Use a separate V2 Git repository containing the three full SHAs in the [baseline manifest](src/au_ree_v3/migration/baselines_v1.json). Commands read pinned Git objects, so the V2 worktree needs no changes. Set `V2_ROOT` to the V2 repository and, when available, `ASSET_LIBRARY` to an exact-byte archive with files named by SHA-256.

```sh
au-ree-v3 migration audit --v2-root "$V2_ROOT" --out work/migration/audit
au-ree-v3 migration verify-crosswalk --v2-root "$V2_ROOT" --out work/migration/crosswalk
au-ree-v3 migration build-golden --v2-root "$V2_ROOT" --out work/migration/golden-review
au-ree-v3 migration verify-baseline --v2-root "$V2_ROOT" \
  --profile archived-census-2026-11-13 --out work/migration/archived-census
au-ree-v3 migration verify-baseline --v2-root "$V2_ROOT" \
  --profile phase22-2026-11-05 --assets "$ASSET_LIBRARY" --out work/migration/phase22
au-ree-v3 migration compare-oracle --expected tests/golden/golden_expected.jsonl \
  --actual work/migration/v3-observations.jsonl --out work/migration/oracle
```

`verify-baseline` and `build-golden` accept optional `--assets`. Archive integrity and source availability have separate receipts. Comparison can be limited with a repeatable `--case-id`. Exit codes 0–4 correspond to `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`, and `UNMEASURED`. See the [acceptance checks](docs/migration/acceptance.md) for each gate’s evidence requirements.

## Recompute the verified Mt Weld slice

The current resource binding covers the 2024 Mt Weld statement and three resource fields at the 2026-11-13 cutoff. It requires Poppler `pdftotext` and the issuer PDF with the SHA-256 listed in the binding. The replay writes an experimental research release and evidence, a three-column Census projection, and Oracle-ready observations and cells.

```sh
au-ree-v3 replay-resource --bindings config/migration/mt_weld_resource_binding_v1.json \
  --assets "$ASSET_LIBRARY" --database work/migration/mt-weld/store.sqlite \
  --out work/migration/mt-weld --code-commit "$(git rev-parse HEAD)"

AU_REE_REPLAY_ASSETS="$ASSET_LIBRARY" \
  python -m unittest discover -s tests -p 'test_pipeline.py' -v
```

The integration suite uses named Golden cases for independent comparisons and includes two clean replays and source-error isolation. The [temporal evidence audit](docs/migration/temporal/README.md) documents exact-edition cutoff decisions and how to reproduce the frozen row-by-row comparison.

## Start the independent 2026-11-28 research baseline

The profile uses Asia/Taipei for the research day. Its project IDs and column headings are pinned to the historical identity contract. Public captures produce new source editions and temporal evidence for V3. Use a separate output directory for each acquisition run and keep the content store at a stable location.

```sh
PROFILE=config/research_profiles/au_ree_2026-11-28.json
au-ree-v3 registry import-projects --v2-root "$V2_ROOT" --profile "$PROFILE" --out work/fresh/registry
au-ree-v3 sources discover --profile "$PROFILE" --out work/fresh/discovery
au-ree-v3 sources acquire --profile "$PROFILE" --store work/fresh/source-store --out work/fresh/acquisition
```

The [fresh baseline report](docs/research/fresh_baseline.md) records the F0–F5 gates and versioned outputs. Historical G0–G5 remain in the migration reports.

The `sources combine` command verifies and merges recorded capture runs without another network request. Pass `--manifest` once per capture manifest, the same `--store`, and a new `--out` directory. It preserves original attempts and verifies the linked objects and temporal evidence.

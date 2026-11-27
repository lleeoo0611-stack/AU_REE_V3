# Research website publication

The website loads a single version from its manifest and verifies each data file. Controlled preview reads `data/preview.json`. Formal browsing reads `data/latest.json`. The formal homepage has its own empty state while no release has passed F5.

## Build and verify a controlled preview

After `research export-web` produces verified data for one or more releases, use a separate local output directory. The second build preserves the first release and the same frontend. The site verifier rejects undeclared files.

```bash
au-ree-v3 research build-site --mode candidate --data work/census-web/data \
  --release-id "$FIRST_CANDIDATE_ID" --out work/census-web/preview
au-ree-v3 research build-site --mode candidate --data work/census-web/data \
  --release-id "$NEXT_CANDIDATE_ID" --out work/census-web/preview
au-ree-v3 research verify-site --mode candidate --site work/census-web/preview \
  --out work/census-web/preview-verification
python -m http.server 8000 --bind 127.0.0.1 --directory work/census-web/preview
```

Open `http://127.0.0.1:8000/?preview=1` on the serving machine. The default URL uses the formal release channel. The separate empty formal site can be built with `research build-site --mode empty --out work/census-web/formal-empty`.

## Formal synchronization and rollback

`research publish` runs the existing remote replica readback, offline replay and rollback rehearsal gates. Successful publication seals the authorization in the trusted research archive and atomically commits its SHA with the research pointer. The formal website build compares its exact profile, build receipt and output hashes with that authorization. It stages and verifies immutable files, then atomically replaces one web pointer containing the active release and entire catalog. A failed stage preserves the previous pointer. A separate local preview build has no access to the formal channel.

Once the approved research pointer is current, run the formal build with `--release-root`, `--store`, `--profile`, `--research-build`, and `--release-id` from the trusted archive. Supply `--expected-current` with the previous website release ID on subsequent updates. Older release URLs continue to resolve through the catalog. For rollback, `research rollback` first restores the earlier authorized research pointer. Then `research rollback-web` requires that same current research ID and the expected current website ID. Rollback changes only `data/latest.json`.

`research snapshot-site` checks the current research authorization, each formal catalog link and the complete site inventory, then freezes a new static directory for deployment. The snapshot excludes archive receipts, raw sources, the SQLite database and preview data.

## GitHub Actions

The default verification job tests the linkage and builds the empty public homepage. A manual `verify_empty` dispatch may upload that empty shell. Real preview stays on an authorized local or private surface.

A formal Pages dispatch requires authorized deployment configuration. The private runner group `au-ree-governed-publication` must be restricted to this workflow on `main`, and `research-web-publication` and `github-pages` must be approved environments. After configuring Pages for GitHub Actions and approved archive access, set the runner's trusted archive, object store, build and site paths via `AU_REE_PUBLICATION_ROOT`, `AU_REE_PUBLICATION_STORE`, `AU_REE_PUBLICATION_BUILDS` and `AU_REE_FORMAL_SITE`. Each approved build directory is named by its research release ID and contains the exact `profile.json` with its pinned companion files and the `build/` directory. The workflow remains disabled for formal deployment until `AU_REE_WEB_DEPLOYMENT_AUTHORIZED` is set to `true`. The Pages job consumes a complete, verified formal snapshot from the same run.

With the present research archive, F5 returns `BLOCKED` and Pages deployment is `DEPLOYMENT_NOT_AUTHORIZED`.

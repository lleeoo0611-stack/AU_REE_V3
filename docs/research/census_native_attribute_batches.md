# Native Census attribute batches

The opt-in display profile uses the existing 93 Projects, 51 core columns, 144 exact source SHA-256s, and the November 28, 2026 research cutoff. It adds 16 validated descriptive cells from seven independently supported assertions. The present core cells move from 1,122 to 1,138 of 4,743. The Projects with at least one formal numeric or validated descriptive cell move from 9 to 12 of 93. Charley Creek, Cummins Range, and Dubbo reach that threshold in this candidate.

| Project | Verified historical or geological evidence | Physical page |
| --- | --- | --- |
| Charley Creek | Alluvial REE deposit hosted by alluvial fans | 26 |
| North Stanmore | Mixed clay regolith-hosted REO mineralisation | 129, with the preceding Geology row |
| Cummins Range | REO deposit within the named carbonatite complex | 30 |
| West Balranald | Beach-deposited heavy-mineral strandline | 33 |
| Goschen Area 1 | Fine-grained heavy-mineral sands in an offshore depositional setting | 23 |
| Browns Range, Wolverine scope | DFS completed in August 2025, with Wolverine identified as the basis of the study | 171, supported by page 7 |
| Dubbo | Historical whole-Project DFS completed in 2013 | 23 |

The source routes pin captures, native subject references, row labels, physical line bounds, and commentary column starts. A common parser identifies the classification or the event from the original text. The routes contain no supplied outcome, stage code, evidence quote, or interpreted event date. Study dates use the explicitly reported year or month. Date roles, historical scope, native quotes, selection purpose, and evidence coordinates remain attached to each projected cell.

Browns Range uses the JORC commentary column at page 171. The extraction follows one bounded native layout region through lines 28–30 and excludes the adjacent explanation column. Dubbo uses a complete numbered issuer history subsection, anchored by its opening named-Project sentence. The earlier short-distance reading remains in the preceding profile and retains its original disposition. The batch applies the declared section scope to the historical DFS and keeps the separate first-phase scoping study outside this claim.

Cowalinya’s investigated model and Cascade’s interpretation remain candidates. The Nowie report’s Goschen-related passage remains an adjacent-Project reference. Charley Creek’s stated scoping level supplies no dated PFS or DFS completion. The batch retains read-back coordinates for these refusals. Donald’s phase-level studies and Koppamurra’s undated and planned studies keep their prior outcomes.

## Verification

| Reviewed native surface | Fixed cases | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: | ---: |
| JORC geology commentary | 8 | 5 | 0 | 0 | 3 |
| JORC study status commentary | 2 | 1 | 0 | 0 | 1 |
| Numbered issuer Project history | 1 | 1 | 0 | 0 | 0 |

These are independently checked, purposively selected fixed-source cases. Cohort-wide and unseen-family precision and recall remain unmeasured. The prior 11 descriptive values and their source anchors, 16 direct numeric cells, three derived cells, and six comparable MCDA keys retain their prior results. No new source edition, raw numeric observation, formal derived value, or MCDA input comes from the batch.

Build `au_ree_census_evidence_v1.json` and `au_ree_census_display_v1.json` using the same committed code, `resource_bindings_v4.json`, and the fixed source manifest and CAS. Read all original evidence and compare the complete baseline and candidate ledgers with the independent verifier.

```bash
python tools/verify_census_attribute_batches.py \
  --before "$baseline_build" --after "$display_build" \
  --assets "$source_assets" --out "$native_attribute_oracle_receipt"
```

## File lifecycle

| File | Disposition |
| --- | --- |
| `attribute_batches.py` | Added reusable, layout-bound issuer classifiers and scope, publication-date, and milestone readers |
| `project_attributes.py` | Extended the typed assertion, validation, selection, and cell checks with a separate native batch policy |
| `registry.py` | Added a SHA-pinned opt-in batch input |
| `au_ree_census_display_v1.json` | Added the display profile with the prior science and text inputs |
| `census_attribute_batches_v1.json` | Added physical source routes and subject anchors |
| `test_attribute_batches.py` | Added real-source and scope, layout, and date regressions |
| `test_web_publication.py` | Extended expected fixed-source display cell totals |
| `verify_census_attribute_batches.py` | Added the independent original-document and frozen-ledger comparison |
| `census_native_attribute_batches.md` | Recorded the method and measured coverage |

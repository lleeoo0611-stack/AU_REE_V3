# Acceptance checks

[Migration specification v1.1](migration_specification_v1_1.md) defines each gate. The [implementation report](implementation_report.md) records the observed results for this revision.

| Gate | Run | Evidence required |
| --- | --- | --- |
| G0 | `migration audit`, `migration verify-baseline` for both profiles and `migration build-golden` | Fixed Git identities, rehashed frozen artifacts, reconciliation of Census states and both denominators, source availability by SHA and traceable expected fixtures |
| G1 | `migration verify-crosswalk` and contract and store tests | Verified real sample, lossless IDs and raw values, independently marked negative cases, transactional rollback and SQLite integrity |
| G2 | Two clean exact-source experimental runs and `migration compare-oracle` | V3 recomputation from exact bytes, independent comparisons of admission and rejection, cell states, cutoff, and fault isolation |
| G3 | Frozen subset replay | Exact source set, configuration, source-derived Census and verified 16-panel recomputation |
| G4 | Archived release replay | Complete 93×51 comparison from original bytes and compatible historical settings, all affected MCDA results, both coverage denominators |
| G5 | Publication and rollback rehearsal | Verified declared scope, complete capability coverage and mappings, successful CI, immutable release manifests and pointer-only rollback |

Keep the 2026-11-05 and 2026-11-13 cutoffs separate. Actual runs belong under `work/migration/`. Hash checks on existing files, computations from archived matrices and replay from primary source bytes carry different evidence.

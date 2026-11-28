# Versioned Census display fields

The v2 display contract is opt-in. The historical Project order, 51 original headings, Census CSV, SQLite schema and MCDA keep their existing contracts. Each display bundle links to one verified research candidate and emits a separate matrix, exact typed facts and CSV. The core coverage denominator is always 4,743. The extension reports its own denominator of 93 Projects per field.

| Group | Fields selected for evidence qualification | Distinct information |
|---|---|---|
| Resource | Reported category, estimate effective date, cutoff, named statement scope, and grade chemistry and material basis | A resource statement's native scope and interpretation. Its source and estimate roles stay distinct. |
| Study | Reported completion date | Search and sort scoping with native calendar precision. The original historical study label and the date refer to the same assertion. |
| Forecast | Product and basis, scenario or period, and reported annual rate | Each native statement can be inspected with its material and role. Forecast and engineering design retain independent evidence. |

The extension requires original quote, token, unit when relevant, scope, publication date, exact edition and capture fingerprints, an eligible temporal decision, and a field-specific evidence role. Each quantity stays in its native unit and each date keeps its native year, month or day precision. Missing evidence yields `UNKNOWN`. A study event date never stands for the current Project status.

Distinct eligible statements can share a display cell. The display groups their values without numerical aggregation and carries every statement link. A review-only panel can carry unresolved evidence with an explicit reason and status. It contributes neither known cells nor scientific values. The date field aids chronological retrieval and contributes no additional independent assertion. Source publication date is already present in evidence and stays there. Official point labels stay in the review panel pending role and location evidence.

Only the defined v2 schema and canonical column fingerprint are accepted. CSV and public JSON are regenerated from linked facts and compared exactly during verification.

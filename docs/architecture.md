# AU_REE_V3 architecture

The CLI dispatches commands and exposes machine-readable verification status through exit codes. Migration tools read the exact V2 Git objects designated by the baseline manifest. They write receipts beneath the configured output directory and use a separate V3 store for imports. The reference comparator reads frozen expected results and candidate outputs independently of V3 scientific processing.

The V3 scientific contracts retain original identity, source editions and measurement tokens. Source and document processing produce evidence and candidates. Evidence validation and selection decide eligibility under an explicit, versioned scope and rule. The SQLite store records versioned lineage and commits or rolls back each batch. Downstream Census projections read values selected for that release and preserve cell-state semantics.

Research profiles and release manifests bind inputs, configuration, code and cutoff. A V2 artifact can be independently rehashed even when an original source or historical configuration needed for replay is unavailable. An experimental case or profile stays separate from a formal release.

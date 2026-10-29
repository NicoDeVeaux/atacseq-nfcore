# Focused module regression checks

Run `python3 tests/regressions/preseq_retry.py` with Python 3 and Nextflow installed. The check uses offline mode, so its Nextflow distribution must already be cached. `--nextflow-jar /path/to/nextflow-one.jar` selects an explicit distribution; `--output /empty/directory` retains task commands, logs, traces and results in a chosen directory.

The check executes the actual PRESEQ module on the local executor without containers or Conda. Its stand-in executable records arguments, simulates an initial complexity-model failure, and succeeds only when original options and the retry flag are present. It checks normal success, retry recovery and an intentionally broken mutation. It does not validate scientific estimates or the complete ATAC pipeline.

The real-data comparisons and wider review are recorded in `PR-DRAFT.md` and `docs/module-update-audit.md`.

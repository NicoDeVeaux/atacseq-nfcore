# Scientific change PoC

This fork-only experiment trials scientific change reports and small behavioral guards against [#474](https://github.com/nf-core/atacseq/pull/474), [#476](https://github.com/nf-core/atacseq/pull/476), and [#469](https://github.com/nf-core/atacseq/pull/469). The branch adds test/report code and a dedicated fork workflow. Production files and existing snapshots are unchanged.

Frozen targets are in [targets.json](targets.json): common base 73155d, module repairs f28734, SAF correction 9a255c, provenance restoration 740a8a. The older fourth open PR, [#452](https://github.com/nf-core/atacseq/pull/452), is outside this three-case trial; mixed SE/PE counting is unqualified here.

[Measured local results and receipts](evidence/local-20261104/README.md): all twelve expected observations passed, alongside 47 unit controls. The initial harness ERROR is retained separately from the corrected trial.

## What runs

| Guard | Fresh execution | Independent requirement |
|---|---|---|
| Trimming | Actual Trim Galore module, production configuration/defaults, declared image | Exact paired-read IDs, sequences and qualities, including retention of legitimate R1 poly-G and R2 5-prime poly-C under the earlier pipeline policy |
| Duplicate marking | Actual Nextflow production selectors, then real Picard | Technical runs share library identity and distinct read-group IDs; only the intended technical duplicate is marked; a different biological library at the same coordinates remains |
| SAF | Exact production converter expression, real Subread on a tiny SAM/BAM | BED start+1, unchanged end/identity, boundary read excluded, positive controls and all alignment records preserved |
| Versions | Exact production collation expression and actual version helper in a minimal Nextflow workflow | Workflow pipeline/engine versions present, fixture tool retained |

All inputs for fresh guards are explicitly synthetic. The trimming fixture has 32 pairs per case; the duplicate fixture has five pairs; the SAF fixture has three single-base alignments. No full pipeline, mapping, peak calling or new cohort execution is performed.

The duplicate guard executes production selectors and constructs Picard argv in Python; it does **not** execute the vendored Picard Nextflow module bodies. A future wrapper dropping a flag needs a further actual-module integration test. Its runtime must match both module declarations; unsupported runtime/source changes produce ERROR.

The SAF and provenance probes extract bounded expressions from the actual source and retain their hashes. They are not general Nextflow parsers. Unsupported source shapes produce ERROR for review.

## Run locally

Requires Python with PyYAML 6.0.3, Git, Docker and cached Nextflow 25.10.4. Colima is the local default; Linux Docker normally uses context default.

    uv run --no-project --with PyYAML==6.0.3 python -B -m unittest discover \
      --start-directory tests/scientific_poc --pattern 'test_*.py'

Fetch the frozen fork revisions if they are absent. Then pull only the declared small-tool images:

    python3 -B tests/scientific_poc/prepare_images.py --repo . --docker-context colima

Run into a new output directory:

    uv run --no-project --with PyYAML==6.0.3 python -B tests/scientific_poc/run_trial.py \
      --repo . --output /tmp/atacseq-scientific-trial \
      --docker-context colima --nextflow-version 25.10.4

The runner creates detached source worktrees under the output directory and retains them. A supported JDK can be selected with --java-home. Each guard can also run independently with --repo and --output; run its --help for options.

The twelve expected observations are four broken-source failures, four repaired-source passes, two planted-regression failures, and two broken-source failures after copying the fixed snapshot files over the broken tree. That last control establishes independence from snapshot expectations; it is **not** an ordinary nf-test rerun or an actual automatic snapshot regeneration.

## Scientific change reports

    python3 -B tests/scientific_poc/change_report.py \
      --repo . --base 73155d --head 740a8a --output /tmp/pr469-report

Seven pipeline snapshot files are inspected. Task counts, path inventories, software versions and opaque file fingerprints are reported separately. A hash change remains unexplained without its payload. Repeated basenames, including library/replicate size-factor files, are reported as ambiguous instead of assigning their hashes arbitrarily.

Optional featureCounts payloads are compared by an explicit key:

    python3 -B tests/scientific_poc/change_report.py \
      --repo . --base 73155d --head f28734 \
      --counts repaired-counts before.tsv after.tsv --counts-key interval \
      --output /tmp/pr474-count-report

Use interval identity when peak sets change and Interval_N labels are renumbered. Use geneid only when the named feature identity is the intended comparison contract, for example the isolated SAF coordinate intervention. Complete unique sample basenames and paired numeric values are preserved; duplicate/ambiguous identities are rejected. Feature metadata changes, missing/extra regions and renamed IDs remain visible. Numeric comparison is exact; there is no tolerance or rounding policy.

JSON records complete sample-header aliases, exact delta totals and bounded feature/identity examples. Complete feature mappings remain in the two hash-pinned input payloads.

## Optional public historical data

The [public evidence descriptor](public_evidence.json) pins an existing 14.8 MB archive and the two public reference tables:

    python3 -B tests/scientific_poc/public_payloads.py --output /tmp/atacseq-public-counts

Add --public-counts /tmp/atacseq-public-counts to run_trial.py to reanalyse those payloads. The downloader checks their exact sizes and SHA-256 values. Historical source attribution is declared from linked published receipts; this downloader does not independently reverify that execution/source bridge.

The historical tables cover the repaired 299,580-cell archived-count restoration and the separate SAF intervention. Those are retained full-workflow/count-only data from earlier pinned runs, not new complete executions of the three current heads. The full source/runtime limitations remain in the descriptor and generated report.

## Meaning of a result

- PASS or FAIL on an individual guard concerns its bounded scientific contract.
- ERROR concerns a missing runtime, failed execution, or unsupported harness/source context.
- Malformed, missing or corrupt product output after successful tool execution is a scientific FAIL.
- A complete change report is an observation, with scientific acceptance UNKNOWN unless a separate contract establishes it.
- A campaign PASS means all expected positive and negative controls behaved correctly. It does **not** qualify the entire base pipeline or approve any upstream PR.

The dedicated workflow runs only in this fork and only for this PoC branch/manual dispatch. It runs the tiny guards and unit controls without the optional public cohort download. Existing CI and snapshots are not weakened.

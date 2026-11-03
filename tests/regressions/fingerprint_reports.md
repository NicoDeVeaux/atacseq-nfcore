# Fingerprint report regression

Run the label probe with Nextflow 25.10.4 and 26.10.5, then run the report check with the pinned MultiQC 1.13 image. Each invocation requires a fresh output directory and retains its commands, logs and result JSON.

The `fingerprint-reports` CI matrix runs these checks on both engines and blocks `confirm-pass` on failure. Its receipts, subprocess logs and fingerprint tables are retained for seven days.

```bash
audit=/absolute/path/to/new-evidence
mkdir -p "$audit"
NXF_VER=25.10.4 nextflow -C /dev/null run tests/regressions/fingerprint_labels.nf \
  --modules_config "$PWD/conf/modules.config" --output_json "$audit/labels-25.json"
NXF_VER=26.10.5 nextflow -C /dev/null run tests/regressions/fingerprint_labels.nf \
  --modules_config "$PWD/conf/modules.config" --output_json "$audit/labels-26.json"

docker run --platform linux/amd64 --network none --cpus 1 --memory 2g \
  --mount "type=bind,source=$PWD,target=/repo,readonly" \
  --mount "type=bind,source=$audit,target=/evidence" \
  --entrypoint python \
  quay.io/biocontainers/multiqc@sha256:db913f47894a386040d76ae5f46b19149a71377f5107327d15ec89d3a1ec3f5f \
  /repo/tests/regressions/fingerprint_reports.py \
  --labels-json /evidence/labels-25.json --output /evidence/report-25
```

Repeat the final command with `labels-26.json` and a fresh `report-26` output. The label receipt binds the actual configuration and probe bytes; the runner verifies both before using their output. It also checks all pinned code and fixture bytes again after execution.

| Contract                                         | Check                                                                                                                                                                |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A shared control retains its comparison identity | Execute the actual `ext.args` closure; retain four distinct treatment/control labels, including a legal sample-ID namespace collision witness.                       |
| Numeric curves retain their values               | Parse each fixture separately with the original MultiQC CLI; compare the combined proposal's complete sample/X/Y maps and numeric plot to those independent results. |
| File order does not select a different curve     | Run the proposal with forward and reversed input-file arguments; require identical numeric curves and table bytes.                                                   |
| The table contains the original numeric cells    | Require unique columns, all source X/Y values exactly equal, and no blank series. A blank cell is allowed only when that sample lacks the column's X coordinate.     |
| Other configured formats retain their bytes      | Observe the actual assets config without a format override; compare explicit YAML and JSON original/wrapped exports byte-for-byte.                                   |
| Inputs and failures remain visible               | Verify fixture hashes and pinned source hashes; retain all subprocess exits, standard output and errors.                                                             |

The normal run requires eleven negative controls to fail: an invalid colon-bearing sample ID, original CLI numeric export, legacy shared-control labels, the previous treatment-prefix cleaning collision, unprotected marker-first labels with empty-name fallbacks, a blank series, duplicate X column, changed numeric value, missing sample, corrupted fixture and numeric-key/string-key collision. The actual samplesheet checker must also accept the legal delimiter-collision, `CASE.mLb.clN`, and `mLb_A`/`mLb_B` witnesses. Generated comparison-control identities retain their full metadata through the fingerprint parser; both cleaning-sensitive cases must preserve every original per-role numerical curve. Existing aliases between different cleaned treatment IDs remain outside this check. Explicit filename naming, full names, custom replacements, directory prefixes and cleanup-option restoration are checked against the original library. These metadata fixtures reference filenames only and do not read FASTQs. The runner exercises the production normalizer's non-mutation property directly, and requires the original numerical plot-export bytes to remain unchanged.

To reproduce the serializer failure as a nonzero process exit, add `--baseline` and use a fresh output directory. Its expected error is `Duplicate fingerprint TSV columns`; a setup or execution error is not a valid red result. The old label closure can independently be tested by exporting the baseline `conf/modules.config` to evidence and passing it to the probe; its comparison-identity assertion must fail.

The two small fixtures duplicate genuine retained integer bin-count vectors into treatment/control columns. Only report headers are replaced with labels from the actual closure. Their source hashes and derivation are in `fingerprint_fixtures/MANIFEST.json`. They are parser fixtures, not the missing historical controls-CI raw files or a BAM-level scientific oracle.

For a future focused nf-test fixture, wrap the label probe and this bounded report stage in one isolated workflow, using these fixtures and the immutable image. Keep the semantic checks above as assertions, then snapshot only the validated numeric table and numeric plot export. Generate the candidate from retained outputs after both engine lanes agree, and require an ordinary no-update nf-test run against it in both lanes. Existing pipeline snapshots are untouched by this regression; full controls snapshots require the separate CI input/output capture and validation workflow.

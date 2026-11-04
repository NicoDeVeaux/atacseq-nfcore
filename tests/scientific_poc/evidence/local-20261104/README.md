# Local scientific PoC results

**12/12 expected observations passed. Campaign success includes deliberate failures of the broken application; it is not full-pipeline or merge approval.**

| Check | Before | Repaired |
|---|---|---|
| Trimming sequence/default policy (#474) | FAIL | PASS |
| Technical-library duplicate membership (#474) | FAIL | PASS |
| Workflow provenance (#469) | FAIL | PASS |
| SAF coordinates and boundary count (#476) | FAIL | PASS |

The missing-poly-G-option and overmerged-library mutants fail. Both scientific #474 guards still fail after fixed snapshots replace the broken tree's expectations.

## Reanalysis of existing public count payloads

| Comparison | Named cells compared | Changed cells | Affected shared features | Total shared-cell delta |
|---|---:|---:|---:|---:|
| 474-broken-to-fixed | 278,384 | 6,982 | 5,373 | -1378 |
| 474-archived-to-fixed | 299,580 | 0 | 0 | 0 |
| 476-SAF-correction | 299,580 | 2,280 | 2,223 | -2293 |

These are reanalysed historical public payloads: full matched pipelines/count-only interventions from earlier pinned executions. They are not new whole-pipeline executions of the current PR heads. Original feature mappings remain in the SHA-256-pinned public tables; this export bounds large missing/extra feature lists.

In #474 the changed peak set renumbers Interval_N IDs; the comparison uses exact chromosome/start/end/strand keys. The archived-versus-fixed comparison has all 299,580 values equal. In #476 the isolated intervention retains feature IDs, increments starts and changes lengths; 2,280 cells across 2,223 regions change, with total delta -2,293.

## Snapshot expectation diagnostics

| PR | Changed filename groups | Hash expectations removed | Added |
|---|---:|---:|---:|
| #474 | 613 | 624 | 624 |
| #476 | 92 | 106 | 106 |
| #469 | 0 | 0 | 0 |

Repeated size-factor basenames are ambiguous without path-qualified payloads. A changed hash remains unexplained by itself. #469 restores Workflow provenance without changing any recorded scientific file hash.

## Scope and evidence

- Trimming uses the actual Nextflow module/configuration. Both explicit R2 clipping controls pass on both source versions; the reproduced regression is automatic poly-G/poly-C policy.
- Duplicate marking uses actual production selector evaluation and real Picard; Python constructs argv. Vendored Picard Nextflow module bodies are not exercised.
- SAF runs the exact extracted converter and real Subread on three synthetic alignments: the boundary region changes 2 to1 while the separate positive control remains1.
- Provenance runs the actual collation expression/helper in a minimal workflow, not the full pipeline.
- The 47 unit controls, actionlint and whitespace checks passed locally. The dedicated fork workflow is configured for separate Linux-hosted validation.
- Malformed successful product output is FAIL; unsupported source/runtime contexts are ERROR. No snapshots were regenerated or changed in the PoC branch.

Full bounded receipts are in [receipts.tar.gz](receipts.tar.gz), with member identities in [MANIFEST.json](MANIFEST.json); [ATTEMPTS.json](ATTEMPTS.json) preserves the initial harness ERROR separately. The original and public hashes differ where declared redaction/curation was applied. Extract the archive into an empty directory to inspect the JSON records.

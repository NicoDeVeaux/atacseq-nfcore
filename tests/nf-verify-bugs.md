# nf-verify bug pins

Each test in `tests/nf-verify-bugs/` asserts the **correct** behaviour for one bug that
[nf-verify](https://github.com/seqeralabs/nf-verify) found in this pipeline, so it **fails on `dev` today** and passes
once the bug is fixed. All are tagged `nf-verify-bug` plus a per-bug tag. Details:
[bugs-found.md](https://github.com/seqeralabs/nf-verify/blob/integration/docs/bugs-found.md),
[findings.md](https://github.com/seqeralabs/nf-verify/blob/integration/docs/findings.md).

```bash
nf-test test --tag nf-verify-bug                                  # all pins
nf-test test --tag nf-verify-bug-single-replicate-control         # one pin
```

| Bug | Test file | Tag (`nf-verify-bug-…`) | Fails on dev? | Why it fails |
|---|---|---|---|---|
| A single-replicate control drops merged-replicate peak calls (R2); `assets/samplesheet_with_control.csv` triggers it | `single_replicate_control.nf.test` (2 tests) | `single-replicate-control` | yes | merged-replicate MACS3 runs 0 tasks; expected `TREATED_A`, `UNTREATED_A` |
| Consensus featureCounts runs `-p` for single-end libraries (R1, #464) | `consensus_featurecounts_paired_flag.nf.test` | `consensus-featurecounts-paired-flag` | yes | both consensus featureCounts tasks get meta `[id:consensus_peaks]` (no `single_end`), so the module adds `-p` |
| A control's layout is never checked against its treatment's (R1) | `control_layout_unchecked.nf.test` | `control-layout-unchecked` | yes | single-end `TREATED` is peak-called against paired-end `INPUT` (library and replicate level) |
| Replicates naming different controls: merged control depends on scheduling (R3) | `replicates_different_controls.nf.test` | `replicates-different-controls` | yes | merged `WT` is silently called against `INPUT_A` or `INPUT_B` (both seen across runs) |
| Zero-peak libraries and controls get no ataqv QC (R2) | `ataqv_zero_peak_libraries.nf.test` | `ataqv-zero-peak-libraries` | yes | ataqv runs for `KO_REP1, KO_REP2, WT_REP1`; expected all 6 libraries |
| Zero-peak libraries are dropped from the consensus count matrix (R2) | `consensus_zero_peak_libraries.nf.test` | `consensus-zero-peak-libraries` | yes | featureCounts counts `KO_REP1, KO_REP2, WT_REP1`; `WT_REP2` (0 peaks) missing |
| Runs of one replicate naming different controls collide on output names (R1) | `runs_different_controls.nf.test` | `runs-different-controls` | yes | library `S_REP1` merged twice; run dies on a file-name collision in `PICARD_MERGESAMFILES_REPLICATE` |
| A control that itself names a control is never offered as a control (R2) | `control_with_a_control.nf.test` | `control-with-a-control` | yes | run succeeds with no peak calls for `TREATED_A_REP1/2` |
| `--shift_reads`: the two consensus count matrices count different reads | `shift_reads_consensus.nf.test` | `shift-reads-consensus` | yes | merged-library consensus counts `*.shifted.sorted.bam`, merged-replicate consensus the unshifted `*.mLb.clN.sorted.bam` |
| CI never runs `--with_control` | `ci_with_control.nf.test` | `ci-with-control` | yes | under `-profile test_controls` the samplesheet is checked without `--with_control`, the controls are peak-called as libraries, and `tests/controls.nf.test` does not select `test_controls` |
| `prepare_genome.nf` reads `params.bwa_index` instead of its take (latent) | `prepare_genome_bwa_index_param.nf.test` | `prepare-genome-bwa-index-param` | yes | `Argument of file() function cannot be null` |
| The software-versions YAML lacks the `Workflow:` block | `versions_yaml_workflow_block.nf.test` | `versions-yaml-workflow-block` | yes | `nf_core_atacseq_software_mqc_versions.yml` has process versions only |

Every pin was also run against throwaway local fixes (not committed) and passed, so each fails because of its bug,
not because of the test.

## How the pins work

- They test `workflows/atacseq.nf:ATACSEQ` (or `PREPARE_GENOME`) with `-stub`, tiny samplesheets in `samplesheets/`
  and placeholder references and FastQs written by `tests/lib/NfVerifyBugs.groovy`. A run takes 5-25 s.
- `tests/nf-verify-bugs/nextflow.config` instruments a few processes for the tests only: it records each task's
  `meta` and `ext.args` in the work dir, and writes one peak into MACS3's stub output (none for ids in
  `params.nf_verify_zero_peak_ids`), so the zero-peak filters see libraries with and without peaks.
- Assertions read the work dir (`NfVerifyBugs.tasks`): which tasks ran, with which meta and staged inputs.
- Where a fix could reasonably reject the input instead (layout mismatch, different controls, a control's own
  control), the pin accepts a run that fails with a message mentioning the control, and nothing else.
- No snapshots, so nothing buggy is baked in.

## Gaps

- **#464 (`-p`)**: `-stub` runs the stub, not the featureCounts command, so the test rebuilds the flag the way the
  module does (`meta.single_end ? '' : '-p'`, plus `ext.args`). A fix that sets the flag some other way would need
  the test updated.
- **`--shift_reads`**: the pin checks which BAMs each consensus counts, not read contents.
- **`MACS3_CONSENSUS`, `GET_AUTOSOMES` and `SAMPLESHEET_CHECK`** have no stub block, so the pins run their real
  scripts: they need `mergeBed`, `Rscript`, `python` and `python3` (present with `-profile docker`/`conda`/`singularity`). Without containers, put
  `tests/nf-verify-bugs/shims` first on `PATH`; the shims stand in for `mergeBed`, `R`, `Rscript` and `python` and
  are never used otherwise.

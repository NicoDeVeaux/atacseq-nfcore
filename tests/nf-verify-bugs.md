# nf-verify bug pins

This directory holds one nf-test per bug that nf-verify found in this pipeline. Each test asserts the **correct**
behaviour, so it **fails on `dev` today** and will pass once the bug is fixed. Every test was also run against a
throwaway local fix and passed, so each one fails because of its bug, not because of the test.

All the bugs have one thing in common: **the run still reports `SUCCESS`**. Samples are dropped, paired with the
wrong partner, or processed with the wrong settings, and nothing fails. That is why the existing tests, which check
that runs succeed and that outputs match snapshots, do not catch them.

## What nf-verify is

nf-verify is a Seqera research tool, in a private repository, that checks a Nextflow pipeline's dataflow without
running it. It reads the pipeline with Nextflow's own parser and looks for patterns that lose or mix up samples. The
patterns come from measuring how Nextflow's channel operators actually behave. Some of its rules are proved correct
in the Lean theorem prover. Every finding below was then reproduced by running the real channel code in Nextflow
with stand-in processes. This page is self-contained; the links into nf-verify are for readers with access.

## The rules

Each bug is labelled with the rule that found it.

| Rule | What it checks | Example in this pipeline |
|---|---|---|
| **R1** merged inputs agree | When data from different samplesheet rows comes together (a merge, a `groupTuple`, a join, a hand-built `meta`), every `meta` field a downstream tool reads must agree across the rows, or something must check it | the consensus featureCounts `meta` has no `single_end`, so the module adds `-p` (#464) |
| **R2** no silent drops | An inner `join`, a `combine(by:)`, a `groupTuple` without `remainder`, a `filter`, or a `map` that returns null must not silently drop an item that should be processed | a control with one replicate never gets a merged BAM, so every treatment that names it loses its merged peak calls |
| **R3** no arrival-order dependence | No result may depend on the order tasks finish: inputs paired by position, `metas[0]` after `groupTuple`, `first()` of a per-sample channel | the merged replicate takes `metas[0]`'s control, which can be either replicate's |
| **R5** CI covers every branch | Every `if` branch the pipeline can take is run by at least one test | `--with_control` is never run in CI |
| **R8** order-dependent outputs | A list collected in task-completion order must not reach a tool unsorted, or sorted in a way that is not stable across runs | the Picard merges sort BAM paths by their work directories |
| *audit* | found by reading the code, then reproduced | |
| *history replay* | found by replaying past commits in Nextflow and comparing their behaviour | |

(nf-verify's other rules, R4 task counts and R9 parameters that do nothing, found nothing here that needed a pin.)

## Running the pins

```bash
nf-test test --tag nf-verify-bug                                  # all pins, about 4 minutes
nf-test test --tag nf-verify-bug-single-replicate-control         # one pin
```

They run with `-stub` and tiny inputs, so they need no data. With `-profile docker`, `conda` or `singularity` they
run as they are. Without containers, first put the stand-in tools on `PATH`:
`export PATH="$PWD/tests/nf-verify-bugs/shims:$PATH"`.

## The bugs

| # | Bug | Rule | Test file (tag `nf-verify-bug-…`) | What the test sees on `dev` |
|---|---|---|---|---|
| 1 | A control with one replicate cancels merged-replicate peak calling; the shipped `assets/samplesheet_with_control.csv` triggers it | R2 | `single_replicate_control.nf.test`, 2 tests (`single-replicate-control`) | merged-replicate MACS3 runs 0 tasks; expected `TREATED_A`, `UNTREATED_A` |
| 2 | Consensus featureCounts runs `-p` for single-end libraries (#464) | R1 | `consensus_featurecounts_paired_flag.nf.test` (`consensus-featurecounts-paired-flag`) | both consensus featureCounts tasks get `meta = [id: consensus_peaks]` with no `single_end`, so the module adds `-p` |
| 3 | A control's layout is never checked against its treatment's | R1 | `control_layout_unchecked.nf.test` (`control-layout-unchecked`) | single-end `TREATED` is peak-called against paired-end `INPUT` |
| 4 | Replicates naming different controls: the merged control depends on scheduling | R3 | `replicates_different_controls.nf.test` (`replicates-different-controls`) | merged `WT` is silently called against `INPUT_A` or `INPUT_B` |
| 5 | Libraries with 0 peaks, and every control, get no ataqv QC | R2 | `ataqv_zero_peak_libraries.nf.test` (`ataqv-zero-peak-libraries`) | ataqv runs for 3 of 6 libraries |
| 6 | Libraries with 0 peaks are left out of the consensus count matrix | R2 | `consensus_zero_peak_libraries.nf.test` (`consensus-zero-peak-libraries`) | featureCounts counts 3 of 4 libraries; `WT_REP2` (0 peaks) is missing |
| 7 | Runs of one replicate naming different controls collide on output names | R1 | `runs_different_controls.nf.test` (`runs-different-controls`) | library `S_REP1` is merged twice, and the run dies on a file-name collision |
| 8 | A control that itself names a control is never offered as a control | R2 | `control_with_a_control.nf.test` (`control-with-a-control`) | the run succeeds with no peak calls for `TREATED_A` |
| 9 | With `--shift_reads`, the two consensus count matrices count different reads | audit | `shift_reads_consensus.nf.test` (`shift-reads-consensus`) | merged-library consensus counts shifted BAMs; merged-replicate consensus counts unshifted ones |
| 10 | CI never runs `--with_control` | R5 / audit | `ci_with_control.nf.test` (`ci-with-control`) | under `-profile test_controls` the samplesheet is checked without `--with_control`, and `tests/controls.nf.test` does not select that profile |
| 11 | `prepare_genome.nf` reads `params.bwa_index` instead of its input (latent) | history replay | `prepare_genome_bwa_index_param.nf.test` (`prepare-genome-bwa-index-param`) | `Argument of file() function cannot be null` |
| 12 | The software-versions YAML lacks the `Workflow:` block (pipeline and Nextflow versions) | history replay | `versions_yaml_workflow_block.nf.test` (`versions-yaml-workflow-block`) | `nf_core_atacseq_software_mqc_versions.yml` has process versions only |
| 13 | The Picard merges order their inputs by work directory, not by name, so the merged BAM is not reproducible | R8 | `picard_merge_input_order.nf.test` (`picard-merge-input-order`) | `--INPUT` order is `T3, T4, T2, T1` for runs `T1..T4` |

## Details

Line numbers are for `dev` at `6a93077`.

1. **Single-replicate control (R2).** `workflows/atacseq.nf:509-514` keeps only samples with more than one
   replicate for the replicate merge (a `map` that returns nothing otherwise). A control with one replicate never
   gets a merged BAM, so the `combine` at `:578`, which pairs each treatment with its control's merged BAM, silently
   drops every treatment that names it. *Likely fix:* when a control has no merged BAM, use its single library BAM.
2. **Consensus `-p` (R1, #464).** The consensus `meta` is built as `[id: 'consensus_peaks']`
   (`subworkflows/local/bed_consensus_quantify_qc_bedtools_featurecounts_deseq2.nf:28-35`) with no `single_end`.
   The featureCounts module adds `-p` whenever `meta.single_end` is falsy, so single-end libraries are counted as
   paired fragments. *Likely fix:* set `single_end` from the libraries, and stop with an error on a mixed samplesheet.
3. **Control layout (R1).** Neither `bin/check_samplesheet.py` nor `workflows/atacseq.nf:365-381` compares the
   `single_end` of a treatment and its control, and MACS3 reads both with the treatment's `--format` (`BAM` vs
   `BAMPE`). The shipped `assets/samplesheet_with_control.csv` pairs single-end `TREATED_A` with paired-end
   `INPUT_A`. *Likely fix:* reject mismatched layouts in the samplesheet check.
4. **Different controls per replicate (R3).** `workflows/atacseq.nf:498-515` groups replicates and keeps
   `metas[0]`, so the merged replicate's control is whichever replicate's `meta` arrived first. *Likely fix:* stop
   with an error when replicates of one sample name different controls.
5. **ataqv and zero-peak libraries (R2).** ataqv's input is the MarkDuplicates BAMs inner-joined with the MACS3
   peaks (`workflows/atacseq.nf:454-459`), and `bam_peaks_call_qc_annotate_macs3_homer.nf:41-48` drops empty peak
   files. A library with 0 peaks, and every control (controls are never peak-called), silently gets no ataqv report.
   *Likely fix:* join with `remainder: true` and run ataqv without peaks when there are none.
6. **Consensus counts and zero-peak libraries (R2).** The same empty-peak filter, then
   `bed_consensus_quantify_qc_bedtools_featurecounts_deseq2.nf:63-64` inner-joins the BAMs with the peaks, so a
   library with 0 peaks is missing from the featureCounts matrix and DESeq2 QC instead of counting 0 reads in peaks.
   *Likely fix:* count every library against the consensus peaks, whatever its own peak count.
7. **Runs naming different controls (R1).** `bin/check_samplesheet.py` accepts two runs of one replicate that name
   different controls. The library merge at `workflows/atacseq.nf:241-254` groups by the whole `meta`, so the control
   splits them into two library items with the same id, which overwrite each other's outputs or collide on a file
   name. *Likely fix:* reject this in the samplesheet check.
8. **Control of a control (R2).** `workflows/atacseq.nf:366-371` offers only libraries without a control of their
   own as controls. If `INPUT_A` names a control and is also `TREATED_A`'s control, the `combine` at `:378` finds no
   partner for `TREATED_A`, which silently gets no peak calls. *Likely fix:* allow it, or reject it in the
   samplesheet check.
9. **`--shift_reads` (audit).** The merged-library consensus counts the shifted, 0-120 bp filtered BAMs
   (`workflows/atacseq.nf:325-335`, `:436-438`). The merged-replicate consensus counts BAMs built from the unshifted
   filter output (`:498-515`, `:615-617`). *Likely fix:* count the same kind of BAM in both, or document why not.
10. **CI and `--with_control` (R5, audit).** `conf/test_controls.config` never sets `with_control = true`, so its
    samplesheet's control columns are ignored and the controls are peak-called as ordinary libraries. And
    `tests/controls.nf.test` runs `nf-test.config`'s default profile (`test`), not `test_controls`. No CI job runs the
    `--with_control` code, which is where bugs 1, 3, 4 and 8 live. *Likely fix:* set `with_control = true` in that
    config and select the profile in the test.
11. **`bwa_index` read from params (history replay).** `subworkflows/local/prepare_genome.nf:165` calls
    `file(params.bwa_index)` where it should use its `bwa_index` input. `main.nf` passes `params.bwa_index`, so the
    pipeline works today; any other caller that passes an index without setting the param fails.
    *Likely fix:* use the input.
12. **Versions YAML (history replay).** Since #448 moved software versions to the topic channel,
    `workflows/atacseq.nf:668-682` collates only process versions. `softwareVersionsToYAML()`, which also appended
    `workflowVersionToYAML()`, was dropped, so the versions file and MultiQC's versions table no longer show the
    pipeline or Nextflow version. *Fix:* mix `workflowVersionToYAML()` back in before `collectFile` (prepared on the
    branch `fix-versions-yaml-workflow-block`).
13. **Picard merge order (R8).** `workflows/atacseq.nf:249` and `:508` collect BAMs in task-completion order, and the
    nf-core `picard/mergesamfiles` module sorts them with `bams.sort()`. For staged inputs, that compares their full
    source paths, i.e. the upstream work directories, whose hashes change on every fresh run. So MergeSamFiles gets
    its inputs in a different order from run to run (9-10 distinct orders in 10 fresh runs, measured). The order
    decides the read-group order in the merged header, and ties between equal coordinates. *Likely fix:*
    `bams.sort { it.name }`, in nf-core/modules.

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
- **Picard input order**: the pin checks the `--INPUT` order Picard receives, via a `picard` stand-in in `shims/`. That
  this changes the merged BAM (read-group order in the header, ties between equal coordinates) follows from
  MergeSamFiles merging inputs in order; it was not checked with real Picard here. The fix belongs in nf-core/modules.

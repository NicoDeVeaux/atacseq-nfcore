# Draft PR: fix module-update regressions after #448

Target: `nf-core/atacseq:dev`  
Related issue: [#473](https://github.com/nf-core/atacseq/issues/473)  
Status: versioned proposal on the fork; broader behavior review remains open.

## Problem and changes

The module update in #448 changed scientific output and optional-QC failure behavior. This branch contains three corrections:

- Update the complete Trim Galore module to **2.3.0**, which fixes the upstream wrong-end R2 clipping bug, and pass **`--no_poly_g`** to preserve the pipeline's earlier default. Updating the version alone does not preserve that default.
- Give Chromap technical runs from the same biological library a shared **`RGLB`**, while retaining distinct **`RGID`** values. Picard can then mark duplicates across those runs. This is independent of trimming.
- Preserve PRESEQ's original command options on retry and append **`-defects`**. The updated wrapper calls `String.join` with no elements, producing an empty string and dropping `-bam`, `-verbose` and `-seed 1`. Record the local module patch in `modules.json`.

The PRESEQ correction preserves the module's new retry policy. The old ignore-to-new-retry policy, which can disable the estimate's defect check, is a separate documented behavior change; successful completion does not establish the validity of every estimate.

## Validation

- Four full-input HIV workflows succeeded: BWA and Chromap on reviewed dev `73155d` and the frozen two-fix proposal `a9b588`. Each processed **87,089,251 pairs** across four libraries.
- The fixed BWA library-consensus matrix exactly matches the archived original dev matrix: **74,895 regions and all 299,580 count cells**. The unfixed matrix matches the archived PR448 matrix. This is an archived-matrix comparison, not a fresh run of the immediate pre-448 parent; historical FASTQ byte identity is unestablished.
- With identical real alignments from two resequenced yeast libraries, pre-448 and fixed duplicate marking agrees exactly. Unfixed dev retains **2,553 and 1,865 additional duplicate pairs**.
- Three yeast core workflow runs restore individual peak coordinates and all **1,934 consensus regions**. One count cell differs by one, traced to an alignment/duplicate-representative change. Optional QC exclusions were applied equally. The HIV cohort has one technical run per library and cannot isolate the library-ID defect.
- `python3 tests/regressions/preseq_retry.py` exercises the actual PRESEQ wrapper: normal success, retry recovery and an intentionally broken mutation. Its executable is an argument-checking stand-in, so it does not validate numerical estimates.
- Changed-file pre-commit checks, including Nextflow lint, pass. Full nf-core/tools 4.1.0 lint reports 13 failures: five template differences and eight container-config differences. A separate run on immutable `a9b588` reproduces the same 13 failures. These remain documented baseline debt; full lint is not reported as passing. Its formatter setup also encountered a local npm/prek error, while the separately executed repository formatter hook passes.

The historical full-data runs remain pinned to `a9b588`; they have not been rerun or relabeled as testing the later PRESEQ correction. [Reproducibility repository](https://github.com/NicoDeVeaux/atacseq-modules-update-verification). The full output-tree archive is being prepared in an unpublished draft release.

## Broader review and opening gates

The [module audit](docs/module-update-audit.md) accounts for all **267 changed module/subworkflow files**, **70 components**, **16 changed declared dependency versions**, and subsequent histories for **36 shared module paths and seven shared subworkflows**.

- [x] Reproduce the two original regressions and validate their mitigations.
- [x] Scan every declared version change and later merged module fix.
- [x] Reproduce PRESEQ retry option loss and exercise its correction.
- [x] Classify the tested SAMtools and Picard candidates as inherited defects using old/selected binary controls.
- [ ] Resolve remaining deepTools and GFFREAD candidates through targeted checks or source-backed exclusions.
- [ ] Finish the alignment/peak review and document expected changes from the upgrades themselves.
- [ ] Finish and link the downloadable computation/output archive.
- [ ] Record final branch validation and update this description before opening the nf-core PR.

Chromap 0.3.2 includes a mapping-pair correction. Local tests on the real residual pair reproduce an alignment difference independently of trimming and read-group assignment. Complete alignment identity with 0.2.4 is therefore not promised. Later upstream fixes are leads to investigate, not proof that every affected version causes a new pipeline regression. This branch does not blanket-upgrade other tools or resolve the separate mixed SE/PE and MultiQC issues.

This is written by GPT-6 (Codex) on Nico DeVeaux's behalf.

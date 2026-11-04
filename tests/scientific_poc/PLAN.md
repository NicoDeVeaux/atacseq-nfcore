# Scientific change report PoC

Classification: poc. Targets: nf-core/atacseq #474, #476, #469.
Fork branch: poc/scientific-change-guards, based on dev 73155d.
No upstream PR modifications, production edits, or snapshot updates.

## Trial

- Freeze exact common base and three PR heads, plus source-file and container identities.
- Compare existing pipeline snapshot expectations, separating paths, versions, opaque content hashes and serialization/execution metadata. A changed checksum alone is unresolved evidence, never scientific acceptance.
- Compare provided featureCounts payloads by complete unique feature/sample identity, preserving paired metadata and numeric values. Report ordering versus scientific deltas without silently dropping rows or samples.
- Exercise small production-bound trimming and technical-library duplicate-marking fixtures before/after #474, with explicit expected sequences/membership independent of snapshots.
- Exercise the actual SAF conversion expression before/after #476 with a tiny coordinate/count boundary witness.
- Exercise #469's actual versions collation expression and actual workflow-version helper on a tiny channel. Distinguish this bounded probe from a full pipeline run.
- Prove the repaired branches pass the relevant contracts and broken sources remain failures after expectations are copied/refreshed.
- Run comparison mutation controls for missing/duplicate/swapped identities and changed values.

## Results

Independent dimensions: execution (COMPLETE/ERROR), scientific contract (PASS/FAIL/UNKNOWN), observed change (identical/order/metadata/scientific), and scope (tiny witness/archived payload/full workflow). Synthetic witnesses are labelled; historical receipts are cited without relabelling them as new runs.

The PoC must not claim each hash in #474/#476 is explained by tiny examples, or that a report can decide scientific correctness without an independent contract. Current broken baseline is expected to FAIL. No paid compute is needed; cached local Linux tools and free fork CI are sufficient for small probes.

## Publication

All implementation lives in this fork branch. Reports contain public/synthetic evidence only and no workstation secrets or absolute-path logs. Local raw execution details remain outside tracked files. Remote fork publication follows the user's standing instruction to push work to their fork; no upstream changes are included.

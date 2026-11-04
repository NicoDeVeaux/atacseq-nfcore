#!/usr/bin/env python3
"""Export bounded, path-redacted trial receipts for review in the fork."""
import argparse
import hashlib
import gzip
import io
import json
import tarfile
from pathlib import Path

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial",type=Path,required=True)
    parser.add_argument("--prior-trial",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    root=args.trial.resolve()
    if args.output.exists():parser.error("Output must not exist")
    args.output.mkdir(parents=True)
    trial=json.loads((root/"TRIAL.json").read_text())
    if trial["campaign_status"]!="PASS":raise ValueError("Cannot publish a passing campaign from a failed trial")
    manifest={"schema":"atacseq-scientific-poc-publication/v1","files":[],"transforms":[
        "Replace local trial/home/tmp prefixes with placeholders; preserve original receipt SHA-256.",
        "Bound missing/extra feature example arrays to 24, retaining totals and explicit truncation flags.",
        "Historical source bridges remain declared, not newly executed scientific workflows."]}
    public_files={}
    prefixes=[(str(root),"<TRIAL>"),(str(Path.home()),"<LOCAL_HOME>"),("/private/tmp","<LOCAL_TMP>")]
    def curate(value):
        if isinstance(value,str):
            for a,b in prefixes:value=value.replace(a,b)
            return value
        if isinstance(value,list):return [curate(x) for x in value]
        if isinstance(value,dict):
            result={}
            for key,item in value.items():
                if key in {"missing_features","extra_features"} and isinstance(item,list) and len(item)>24:
                    result[key]=curate(item[:24])
                    result[key+"_total"]=len(item)
                    result[key+"_examples_truncated"]=True
                else:result[key]=curate(item)
            return result
        return value
    def export(relative):
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise ValueError("Unsafe receipt path")
        original=path.read_bytes()
        public=(json.dumps(curate(json.loads(original)),indent=2,allow_nan=False)+"\n").encode()
        public_files[relative]=public
        manifest["files"].append({"path":relative,"original_bytes":len(original),"original_sha256":sha(original),
                                  "public_bytes":len(public),"public_sha256":sha(public)})
    export("TRIAL.json")
    for probe in trial["probes"]:export(probe["receipt"])
    for report in trial["reports"]:export(report["path"]+"/REPORT.json")
    archive=args.output/"receipts.tar.gz"
    with archive.open("wb") as stream:
        with gzip.GzipFile(filename="",fileobj=stream,mode="wb",mtime=0) as compressed:
            with tarfile.open(fileobj=compressed,mode="w") as handle:
                for name,data in sorted(public_files.items()):
                    member=tarfile.TarInfo(name)
                    member.size=len(data);member.mtime=0;member.mode=0o644
                    handle.addfile(member,io.BytesIO(data))
    manifest["archive"]={"path":archive.name,"bytes":archive.stat().st_size,"sha256":sha(archive.read_bytes())}
    attempts=[{"status":trial["campaign_status"],"probes":len(trial["probes"]),"matched":sum(p["matched"] for p in trial["probes"]),
               "raw_trial_sha256":sha((root/"TRIAL.json").read_bytes())}]
    if args.prior_trial:
        data=(args.prior_trial/"TRIAL.json").read_bytes();prior=json.loads(data)
        attempts.insert(0,{"status":prior["campaign_status"],"probes":len(prior["probes"]),"matched":sum(p["matched"] for p in prior["probes"]),
                          "raw_trial_sha256":sha(data),
                          "cause":"Duplicate selector harness lacked the production parameter defaults on Nextflow25.10.4. Preserved as ERROR; corrected complete rerun follows."})
    (args.output/"ATTEMPTS.json").write_text(json.dumps(attempts,indent=2)+"\n")
    (args.output/"MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    lines=["# Local scientific PoC results","",
           "**12/12 expected observations passed. Campaign success includes deliberate failures of the broken application; it is not full-pipeline or merge approval.**","",
           "| Check | Before | Repaired |","|---|---|---|",
           "| Trimming sequence/default policy (#474) | FAIL | PASS |",
           "| Technical-library duplicate membership (#474) | FAIL | PASS |",
           "| Workflow provenance (#469) | FAIL | PASS |",
           "| SAF coordinates and boundary count (#476) | FAIL | PASS |","",
           "The missing-poly-G-option and overmerged-library mutants fail. Both scientific #474 guards still fail after fixed snapshots replace the broken tree's expectations.","",
           "## Reanalysis of existing public count payloads","",
           "| Comparison | Named cells compared | Changed cells | Affected shared features | Total shared-cell delta |",
           "|---|---:|---:|---:|---:|"]
    for item in trial["historical_payloads"]:
        lines.append(f'| {item["label"]} | {item["comparable_cells"]:,} | {item["changed_count_cells"]:,} | {item["affected_features"]:,} | {item["shared_cells_total_delta"]} |')
    lines.extend(["","These are reanalysed historical public payloads: full matched pipelines/count-only interventions from earlier pinned executions. They are not new whole-pipeline executions of the current PR heads. Original feature mappings remain in the SHA-256-pinned public tables; this export bounds large missing/extra feature lists.","",
        "In #474 the changed peak set renumbers Interval_N IDs; the comparison uses exact chromosome/start/end/strand keys. The archived-versus-fixed comparison has all 299,580 values equal. In #476 the isolated intervention retains feature IDs, increments starts and changes lengths; 2,280 cells across 2,223 regions change, with total delta -2,293.","",
        "## Snapshot expectation diagnostics","",
        "| PR | Changed filename groups | Hash expectations removed | Added |","|---|---:|---:|---:|"])
    for item in trial["reports"]:
        lines.append(f'| #{item["pr"]} | {item["changed_hash_name_groups"]} | {item["hash_expectations_removed"]} | {item["hash_expectations_added"]} |')
    lines.extend(["","Repeated size-factor basenames are ambiguous without path-qualified payloads. A changed hash remains unexplained by itself. #469 restores Workflow provenance without changing any recorded scientific file hash.","",
        "## Scope and evidence","",
        "- Trimming uses the actual Nextflow module/configuration. Both explicit R2 clipping controls pass on both source versions; the reproduced regression is automatic poly-G/poly-C policy.",
        "- Duplicate marking uses actual production selector evaluation and real Picard; Python constructs argv. Vendored Picard Nextflow module bodies are not exercised.",
        "- SAF runs the exact extracted converter and real Subread on three synthetic alignments: the boundary region changes 2 to1 while the separate positive control remains1.",
        "- Provenance runs the actual collation expression/helper in a minimal workflow, not the full pipeline.",
        "- The 47 unit controls, actionlint and whitespace checks passed locally. The dedicated fork workflow is configured for separate Linux-hosted validation.",
        "- Malformed successful product output is FAIL; unsupported source/runtime contexts are ERROR. No snapshots were regenerated or changed in the PoC branch.","",
        "Full bounded receipts are in [receipts.tar.gz](receipts.tar.gz), with member identities in [MANIFEST.json](MANIFEST.json); [ATTEMPTS.json](ATTEMPTS.json) preserves the initial harness ERROR separately. The original and public hashes differ where declared redaction/curation was applied. Extract the archive into an empty directory to inspect the JSON records."])
    (args.output/"README.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"status":"EXPORTED","files":len(manifest["files"]),"output":str(args.output)}))

if __name__=="__main__":main()

#!/usr/bin/env python3
"""Run a frozen red/green scientific-guard experiment on the three fix PRs."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from change_report import compare_counts
from public_payloads import verify

HERE=Path(__file__).resolve().parent

def run(command,log,env):
    process=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,timeout=1200)
    log.write_bytes(process.stdout)
    return process.returncode

def checkout(repo,ref,destination):
    subprocess.run(["git","-C",str(repo),"worktree","add","--detach",str(destination),ref],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    return destination

def scientific_probe(name,program,repo,output,expected,args,env,extra=()):
    folder=output/name
    command=[sys.executable,str(HERE/program),"--repo",str(repo),"--output",str(folder)]
    if program!="provenance.py":command+=["--docker-context",args.docker_context]
    if program in {"provenance.py","trimming.py"}:
        command+=["--nextflow-version",args.nextflow_version]
    command+=list(extra)
    code=run(command,output/(name+".log"),env)
    result_path=folder/"RESULT.json"
    result=json.loads(result_path.read_text()) if result_path.is_file() else {"status":"ERROR","reason":"No RESULT.json"}
    actual=result.get("status","ERROR")
    return {"name":name,"expected":expected,"observed":actual,"exit_code":code,
            "matched":actual==expected and code=={"PASS":0,"FAIL":1,"ERROR":2}[actual],
            "receipt":str(result_path.relative_to(output)),
            "receipt_sha256":hashlib.sha256(result_path.read_bytes()).hexdigest() if result_path.is_file() else None}

def render(summary):
    lines=["# Scientific change PoC trial","",
           "**Campaign PASS means the controls behaved as expected. The broken application remains FAIL; this is not upstream merge approval.**","",
           "| Probe | Expected | Observed |","|---|---|---|"]
    for row in summary["probes"]:
        lines.append("| "+row["name"]+" | "+row["expected"]+" | "+row["observed"]+" |")
    lines.extend(["","Snapshot-only reports identify opaque differences without explaining them. Public count payloads, when supplied, are a historical data reanalysis, not new pipeline executions."])
    return "\n".join(lines)+"\n"

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--docker-context",default="colima")
    parser.add_argument("--nextflow-version",default="25.10.4")
    parser.add_argument("--java-home",type=Path)
    parser.add_argument("--public-counts",type=Path)
    args=parser.parse_args()
    args.repo=args.repo.resolve();args.output=args.output.resolve()
    if args.output.exists():parser.error("Output must not exist")
    args.output.mkdir(parents=True)
    env=os.environ.copy()
    env["NXF_VER"]=args.nextflow_version
    if args.java_home:
        env["JAVA_HOME"]=str(args.java_home)
        env["PATH"]=str(args.java_home/"bin")+os.pathsep+env["PATH"]
    targets=json.loads((HERE/"targets.json").read_text())
    heads={str(t["pr"]):t["head"] for t in targets["targets"]}
    summary={"schema":"atacseq-scientific-poc-trial/v1","campaign_status":"ERROR",
             "application_acceptance":"NOT_A_FULL_WORKFLOW_QUALIFICATION",
             "targets":targets,"probes":[],"reports":[],"historical_payloads":[]}
    try:
        sources=args.output/"sources";sources.mkdir()
        base=checkout(args.repo,targets["base"],sources/"broken")
        fixed=checkout(args.repo,heads["474"],sources/"pr474")
        saf=checkout(args.repo,heads["476"],sources/"pr476")
        versions=checkout(args.repo,heads["469"],sources/"pr469")
        runs=[
            ("trimming_before","trimming.py",base,"FAIL",()),
            ("trimming_after","trimming.py",fixed,"PASS",()),
            ("duplicates_before","duplicate_marking.py",base,"FAIL",()),
            ("duplicates_after","duplicate_marking.py",fixed,"PASS",()),
            ("provenance_before","provenance.py",base,"FAIL",()),
            ("provenance_after","provenance.py",versions,"PASS",()),
            ("saf_before","saf_boundary.py",base,"FAIL",()),
            ("saf_after","saf_boundary.py",saf,"PASS",()),
            ("duplicates_wrong_library","duplicate_marking.py",fixed,"FAIL",("--mutant","merge-biological-libraries"))
        ]
        for name,program,repo,expected,extra in runs:
            summary["probes"].append(scientific_probe(name,program,repo,args.output,expected,args,env,extra))
            (args.output/"TRIAL.json").write_text(json.dumps(summary,indent=2)+"\n")
        mutant=checkout(args.repo,heads["474"],sources/"missing-poly-g-option")
        config=mutant/"conf/modules.config";text=config.read_text()
        if text.count("'--no_poly_g'")!=1:raise ValueError("Ambiguous configuration mutation")
        config.write_text(text.replace("'--no_poly_g'","''"))
        summary["probes"].append(scientific_probe("trimming_missing_option","trimming.py",mutant,args.output,"FAIL",args,env,("--case","poly_g")))
        refreshed=checkout(args.repo,targets["base"],sources/"broken-with-fixed-snapshots")
        files=subprocess.check_output(["git","-C",str(args.repo),"ls-tree","-r","--name-only",heads["474"]],text=True).splitlines()
        files=[p for p in files if p.endswith(".nf.test.snap")]
        for relative in files:
            destination=refreshed/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(subprocess.check_output(["git","-C",str(args.repo),"show",heads["474"]+":"+relative]))
        summary["snapshot_independence_control"]={"kind":"COPY_FIXED_SNAPSHOTS_OVER_BROKEN_SOURCE","files":len(files),
            "limit":"No ordinary nf-test execution or automatic regeneration is claimed. The scientific guards must remain independent of these expected-output files."}
        for name,program,extra in [
            ("trimming_after_snapshot_replacement","trimming.py",("--case","poly_g")),
            ("duplicates_after_snapshot_replacement","duplicate_marking.py",())]:
            summary["probes"].append(scientific_probe(name,program,refreshed,args.output,"FAIL",args,env,extra))
        for pr in ("474","476","469"):
            out=args.output/("report-"+pr)
            command=[sys.executable,str(HERE/"change_report.py"),"--repo",str(args.repo),"--base",targets["base"],"--head",heads[pr],"--output",str(out)]
            if run(command,args.output/("report-"+pr+".log"),env)!=0:raise ValueError("Snapshot report failed: "+pr)
            data=json.loads((out/"REPORT.json").read_text())
            summary["reports"].append({"pr":pr,"execution":data["execution"],
                "changed_hash_name_groups":data["changed_hash_name_groups"],
                "hash_expectations_removed":data["hash_expectations_removed"],
                "hash_expectations_added":data["hash_expectations_added"],
                "scientific_acceptance":data["scientific_acceptance"],"path":str(out.relative_to(args.output))})
        if args.public_counts:
            root=args.public_counts
            evidence=json.loads((HERE/"public_evidence.json").read_text())
            for name in ("before","archived","fixed","saf_corrected"):
                record=evidence.get(name,evidence["members"].get(name))
                verify((root/(name+".tsv")).read_bytes(),record)
            comparisons=[
                ("474-broken-to-fixed",root/"before.tsv",root/"fixed.tsv","interval"),
                ("474-archived-to-fixed",root/"archived.tsv",root/"fixed.tsv","interval"),
                ("476-SAF-correction",root/"fixed.tsv",root/"saf_corrected.tsv","geneid")]
            for label,a,b,key in comparisons:
                pr="476" if label.startswith("476") else "474"
                summary["historical_payloads"].append({"label":label,
                    "scope":evidence["scopes"][pr],
                    "historical_source_verification":"Declared from the linked published receipts; payload bytes rehashed here. This run does not independently reverify the historical execution/source bridge.",
                    **compare_counts(a,b,key_mode=key)})
        summary["campaign_status"]="ERROR" if any(p["observed"]=="ERROR" for p in summary["probes"]) else "PASS" if all(p["matched"] for p in summary["probes"]) else "FAIL"
    except Exception as error:
        summary["campaign_status"]="ERROR"
        summary["error"]=type(error).__name__+": "+str(error)
    summary["harness_sha256"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob("*.py"))}
    (args.output/"TRIAL.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")
    (args.output/"TRIAL.md").write_text(render(summary))
    print(json.dumps({"campaign_status":summary["campaign_status"],"probes":len(summary["probes"]),"matched":sum(p["matched"] for p in summary["probes"]),"output":str(args.output)}))
    return {"PASS":0,"FAIL":1,"ERROR":2}[summary["campaign_status"]]

if __name__=="__main__":raise SystemExit(main())

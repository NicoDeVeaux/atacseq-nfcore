#!/usr/bin/env python3
"""Explain snapshot and featureCounts changes without approving new baselines."""
import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

PIPELINE_CASES = ("default", "controls", "bowtie2", "chromap", "star", "skip_trimming", "skip_consensus_peaks")
FEATURE_FIELDS = ("Geneid", "Chr", "Start", "End", "Strand", "Length")

class EvidenceError(ValueError):
    pass

def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result

def load_json(text):
    try:
        result = json.loads(text, object_pairs_hook=unique_pairs)
    except json.JSONDecodeError as error:
        raise EvidenceError("Malformed snapshot JSON") from error
    if not isinstance(result, dict):
        raise EvidenceError("Snapshot root must be an object")
    return result

def fingerprint(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

def read_counts(path, key_mode="geneid"):
    data = Path(path).read_bytes()
    try:
        lines = data.decode("utf-8-sig").splitlines()
    except UnicodeError as error:
        raise EvidenceError("Counts payload is not UTF-8 text") from error
    rows = csv.reader((line for line in lines if not line.startswith("#")), delimiter="\t")
    header = next(rows, None)
    if header is None or tuple(header[:6]) != FEATURE_FIELDS or len(header) < 7:
        raise EvidenceError("Expected featureCounts header and at least one sample")
    samples = [Path(name).name for name in header[6:]]
    if any(not name for name in samples) or len(set(samples)) != len(samples):
        raise EvidenceError("Empty or ambiguous sample basenames")
    records, order = {}, []
    for number, row in enumerate(rows, 2):
        if len(row) != len(header):
            raise EvidenceError(f"Ragged featureCounts row {number}")
        feature = row[0]
        if not feature or feature in records:
            raise EvidenceError(f"Empty or duplicate feature identity at row {number}")
        values = {}
        for sample, text in zip(samples, row[6:]):
            try:
                value = Decimal(text)
            except InvalidOperation as error:
                raise EvidenceError(f"Nonnumeric count at {feature}/{sample}") from error
            if not value.is_finite() or value < 0:
                raise EvidenceError(f"Invalid count at {feature}/{sample}")
            values[sample] = value
        records[feature] = {"id": feature, "metadata": dict(zip(FEATURE_FIELDS[1:], row[1:6])), "values": values}
        order.append(feature)
    if not records:
        raise EvidenceError("Empty featureCounts matrix")
    if key_mode == "interval":
        keyed, keyed_order = {}, []
        for feature in order:
            record = records[feature]
            key = tuple(record["metadata"][field] for field in ("Chr", "Start", "End", "Strand"))
            if any(";" in value or not value for value in key):
                raise EvidenceError("Interval matching requires one complete interval per feature")
            if key in keyed:
                raise EvidenceError("Ambiguous duplicate interval identity")
            keyed[key] = record
            keyed_order.append(key)
        records, order = keyed, keyed_order
    elif key_mode != "geneid":
        raise EvidenceError("Unknown feature comparison key")
    return {"file": Path(path).name, **fingerprint(data), "samples": samples,
            "sample_headers": header[6:], "feature_order": order, "records": records}

def compare_counts(before, after, example_limit=12, key_mode="geneid"):
    left, right = read_counts(before, key_mode), read_counts(after, key_mode)
    lfeatures, rfeatures = set(left["records"]), set(right["records"])
    lsamples, rsamples = set(left["samples"]), set(right["samples"])
    missing_features, extra_features = sorted(lfeatures-rfeatures), sorted(rfeatures-lfeatures)
    missing_samples, extra_samples = sorted(lsamples-rsamples), sorted(rsamples-lsamples)
    changed, metadata, affected, metadata_by_field, renamed_examples = [], [], set(), Counter(), []
    delta, changed_cells, comparable_cells, metadata_fields, renamed = Decimal(0), 0, 0, 0, 0
    for feature in sorted(lfeatures & rfeatures):
        old, new = left["records"][feature], right["records"][feature]
        renamed += old["id"] != new["id"]
        if old["id"] != new["id"] and len(renamed_examples) < example_limit:
            renamed_examples.append({"feature_key": feature, "before_feature_id": old["id"], "after_feature_id": new["id"]})
        for field in FEATURE_FIELDS[1:]:
            if old["metadata"][field] != new["metadata"][field]:
                metadata_fields += 1
                metadata_by_field[field] += 1
                if len(metadata) < example_limit:
                    metadata.append({"feature": feature, "field": field, "before": old["metadata"][field], "after": new["metadata"][field]})
        for sample in sorted(lsamples & rsamples):
            comparable_cells += 1
            a, b = old["values"][sample], new["values"][sample]
            if a != b:
                changed_cells += 1
                affected.add(feature)
                delta += b-a
                if len(changed) < example_limit:
                    changed.append({"feature": feature, "before_feature_id": old["id"], "after_feature_id": new["id"], "sample": sample, "before": str(a), "after": str(b), "delta": str(b-a)})
    identity_change = any((missing_features, extra_features, missing_samples, extra_samples))
    if identity_change:
        kind = "IDENTITY_CHANGED"
    elif changed_cells or metadata_fields:
        kind = "SCIENTIFIC_CONTENT_CHANGED"
    elif renamed:
        kind = "FEATURE_LABELS_CHANGED"
    elif left["sha256"] == right["sha256"]:
        kind = "BYTE_IDENTICAL"
    elif left["feature_order"] != right["feature_order"] or left["samples"] != right["samples"]:
        kind = "ORDER_ONLY_WITH_POSSIBLE_SERIALIZATION_CHANGES"
    else:
        kind = "SERIALIZATION_OR_PATH_ONLY"
    return {
        "kind": kind, "scientific_acceptance": "UNKNOWN",
        "comparison_key": key_mode,
        "normalization": "Rows joined by the declared complete unique feature key; samples joined by unique exact basename. Counts compared exactly with Decimal; metadata and original feature IDs preserved.",
        "before": {k: left[k] for k in ("file", "bytes", "sha256")},
        "after": {k: right[k] for k in ("file", "bytes", "sha256")},
        "features_before": len(lfeatures), "features_after": len(rfeatures),
        "samples_before": len(lsamples), "samples_after": len(rsamples),
        "sample_names": sorted(lsamples | rsamples),
        "sample_header_aliases": {"before": dict(zip(left["samples"], left["sample_headers"])), "after": dict(zip(right["samples"], right["sample_headers"]))},
        "missing_features": missing_features, "extra_features": extra_features,
        "missing_samples": missing_samples, "extra_samples": extra_samples,
        "identity_preserved": not identity_change, "comparable_cells": comparable_cells,
        "changed_count_cells": changed_cells, "unchanged_count_cells": comparable_cells-changed_cells,
        "affected_features": len(affected), "shared_cells_total_delta": str(delta),
        "changed_metadata_fields": metadata_fields, "metadata_examples": metadata,
        "metadata_changes_by_field": dict(metadata_by_field),
        "renamed_feature_ids": renamed,
        "renamed_feature_examples": renamed_examples,
        "count_examples": changed, "examples_truncated": changed_cells > len(changed) or metadata_fields > len(metadata) or renamed > len(renamed_examples),
        "full_feature_identity_location": "The complete feature/coordinate mapping remains in the two SHA-256-bound input payloads; JSON contains bounded examples and complete sample aliases.",
        "reason": "Equality or a delta is an observation; scientific correctness requires an independent contract."
    }

def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

def source_text(repo, commit, path):
    return subprocess.check_output(["git", "-C", str(repo), "show", f"{commit}:{path}"])

def hashes_in(value):
    found = []
    if isinstance(value, str) and ":md5," in value:
        name, digest = value.rsplit(":md5,", 1)
        if len(digest) != 32 or any(c not in "0123456789abcdef" for c in digest):
            raise EvidenceError("Malformed snapshot MD5")
        found.append((name, digest))
    elif isinstance(value, list):
        for item in value: found.extend(hashes_in(item))
    elif isinstance(value, dict):
        for item in value.values(): found.extend(hashes_in(item))
    return found

def version_diff(left, right):
    return [{"group": process, "before": left.get(process), "after": right.get(process),
             "kind": "WORKFLOW_PROVENANCE_CHANGED" if process == "Workflow" else "SOFTWARE_EXPECTATION_CHANGED"}
            for process in sorted(set(left) | set(right)) if left.get(process) != right.get(process)]

def compare_snapshot_case(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise EvidenceError("Snapshot case must be an object")
    old, new = left.get("content"), right.get("content")
    if not isinstance(old, list) or not isinstance(new, list):
        raise EvidenceError("Snapshot case content is not an array")
    lh, rh = hashes_in(old), hashes_in(new)
    lmap, rmap = defaultdict(list), defaultdict(list)
    for name, digest in lh: lmap[name].append(digest)
    for name, digest in rh: rmap[name].append(digest)
    changes = []
    for name in sorted(set(lmap) | set(rmap)):
        a, b = lmap.get(name, []), rmap.get(name, [])
        ambiguous = len(a) > 1 or len(b) > 1
        if a == b or (not ambiguous and Counter(a) == Counter(b)): continue
        changes.append({"file": name, "before": a, "after": b,
                        "kind": "AMBIGUOUS_REPEATED_FILENAME" if ambiguous else "OPAQUE_HASH_CHANGED",
                        "expectations_removed": sum((Counter(a)-Counter(b)).values()),
                        "expectations_added": sum((Counter(b)-Counter(a)).values()),
                        "scientific_acceptance": "UNKNOWN",
                        "reason": "Payload comparison required; a checksum does not explain causation."})
    standard = len(old) == len(new) == 4 and isinstance(old[0], int) and isinstance(new[0], int) and isinstance(old[1], dict) and isinstance(new[1], dict) and isinstance(old[2], list) and isinstance(new[2], list) and all(isinstance(x,str) for x in old[2]+new[2])
    output = {"hash_changes": changes, "content_identical": old == new,
              "execution_metadata_changed": left.get("meta") != right.get("meta") or left.get("timestamp") != right.get("timestamp"),
              "scientific_acceptance": "UNKNOWN"}
    if standard:
        old_paths, new_paths = Counter(old[2]), Counter(new[2])
        output.update({"successful_tasks": {"before": old[0], "after": new[0]},
                       "version_changes": version_diff(old[1], new[1]),
                       "paths_removed": list((old_paths-new_paths).elements()),
                       "paths_added": list((new_paths-old_paths).elements()),
                       "scope": "Four-part pipeline assertion: task count, versions, path inventory, stable-file fingerprints."})
        output["only_provenance_expectation_changed"] = old[0] == new[0] and old[2:] == new[2:] and bool(output["version_changes"]) and all(c["group"] == "Workflow" for c in output["version_changes"])
    else:
        output["scope"] = "Nonstandard/stub assertion; raw content comparison only."
    return output

def compare_snapshots(repo, base, head):
    files = []
    for name in PIPELINE_CASES:
        path = f"tests/{name}.nf.test.snap"
        a, b = source_text(repo, base, path), source_text(repo, head, path)
        left, right = load_json(a), load_json(b)
        cases = []
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                cases.append({"name": key, "kind": "TEST_CASE_ADDED_OR_REMOVED", "scientific_acceptance": "UNKNOWN"})
            else:
                cases.append({"name": key, **compare_snapshot_case(left[key], right[key])})
        files.append({"path": path, "before": fingerprint(a), "after": fingerprint(b), "cases": cases})
    return files

def render(report):
    tick = chr(96)
    lines = ["# Scientific change report", "", f"Source comparison: {tick}{report['base'][:6]}{tick} to {tick}{report['head'][:6]}{tick}.", "",
             "**This report describes evidence. It does not approve a snapshot refresh or establish full-workflow equivalence.**", "",
             f"Changed snapshot filename groups: **{report['changed_hash_name_groups']}**; hash expectations removed/added: **{report['hash_expectations_removed']}/{report['hash_expectations_added']}**. Repeated basenames remain ambiguous without path-qualified payloads. Payloads absent for a hash remain unexplained.", ""]
    for item in report["snapshots"]:
        versions = sum(len(c.get("version_changes", [])) for c in item["cases"])
        hashes = sum(len(c.get("hash_changes", [])) for c in item["cases"])
        lines.append(f"- {tick}{item['path']}{tick}: {hashes} hash changes; {versions} version/provenance group changes.")
    for item in report["counts"]:
        lines.extend(["", f"## Count payload: {item['label']}", "",
                      f"Classification: **{item['kind']}**. Identity preserved: **{item['identity_preserved']}**.",
                      f"Compared {item['comparable_cells']:,} named cells; {item['changed_count_cells']:,} differ across {item['affected_features']:,} features; shared-cell total delta {item['shared_cells_total_delta']}.",
                      f"Feature metadata fields changed: {item['changed_metadata_fields']:,}. Scientific acceptance remains **UNKNOWN** without an independent contract."])
        if item["count_examples"]:
            lines.extend(["", "| Feature | Sample | Before | After | Delta |", "|---|---|---:|---:|---:|"])
            for row in item["count_examples"]:
                escaped = [str(row[k]).replace("|",r"\|").replace("\n"," ") for k in ("feature","sample","before","after","delta")]
                lines.append("| "+" | ".join(escaped)+" |")
    lines.extend(["", "Payload SHA-256 values, complete sample aliases, omissions and bounded identity examples are in REPORT.json. Complete feature mappings remain in the hash-pinned input payloads."])
    return "\n".join(lines)+"\n"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--counts", nargs=3, action="append", metavar=("LABEL","BEFORE","AFTER"), default=[])
    parser.add_argument("--counts-key", choices=("geneid", "interval"), default="geneid")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error("Output must not exist")
    args.output.mkdir(parents=True)
    report = {"schema": "atacseq-scientific-change-report/v1", "execution": "ERROR", "scientific_acceptance": "UNKNOWN"}
    try:
        report["base"] = git(args.repo, "rev-parse", "--verify", "--end-of-options", args.base+"^{commit}")
        report["head"] = git(args.repo, "rev-parse", "--verify", "--end-of-options", args.head+"^{commit}")
        report["snapshots"] = compare_snapshots(args.repo, report["base"], report["head"])
        report["counts"] = [{"label":label, **compare_counts(before,after,key_mode=args.counts_key)} for label,before,after in args.counts]
        changes = [change for f in report["snapshots"] for c in f["cases"] for change in c.get("hash_changes", [])]
        report["changed_hash_name_groups"] = len(changes)
        report["hash_expectations_removed"] = sum(c["expectations_removed"] for c in changes)
        report["hash_expectations_added"] = sum(c["expectations_added"] for c in changes)
        report["execution"] = "COMPLETE"
        (args.output/"REPORT.md").write_text(render(report))
    except EvidenceError as error:
        report["execution"] = "COMPLETE"
        report["scientific_acceptance"] = "FAIL"
        report["evidence_failure"] = str(error)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    (args.output/"REPORT.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"execution":report["execution"],"scientific_acceptance":report["scientific_acceptance"],"output":str(args.output)}))
    return 2 if report["execution"]!="COMPLETE" else 1 if report["scientific_acceptance"]=="FAIL" else 0

if __name__ == "__main__":
    raise SystemExit(main())

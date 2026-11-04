#!/usr/bin/env python3
"""Retrieve only hash-pinned public count tables for the retrospective trial."""
import argparse
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path

def verify(data, record):
    if len(data)!=record["bytes"] or hashlib.sha256(data).hexdigest()!=record["sha256"]:
        raise ValueError("Public evidence identity mismatch")
    return data

def retrieve(record):
    if not record["url"].startswith("https://raw.githubusercontent.com/NicoDeVeaux/atacseq-modules-update-verification/"):
        raise ValueError("Unsupported evidence origin")
    with urllib.request.urlopen(record["url"],timeout=90) as response:
        data=response.read(record["bytes"]+1)
    return verify(data,record)

def materialize(output, archive=None):
    metadata=json.loads((Path(__file__).with_name("public_evidence.json")).read_text())
    output=Path(output)
    if output.exists():raise ValueError("Output must not exist")
    output.mkdir(parents=True)
    data=verify(Path(archive).read_bytes(),metadata["archive"]) if archive else retrieve(metadata["archive"])
    archive_path=output/"public-counts.tar.gz"
    archive_path.write_bytes(data)
    records={}
    with tarfile.open(archive_path,"r:gz") as handle:
        for name,record in metadata["members"].items():
            member=handle.getmember(record["member"])
            if not member.isfile() or member.size!=record["bytes"]:
                raise ValueError("Unexpected public archive member")
            stream=handle.extractfile(member)
            if stream is None:raise ValueError("Missing public archive member")
            value=verify(stream.read(record["bytes"]+1),record)
            destination=output/(name+".tsv")
            destination.write_bytes(value)
            records[name]={"path":destination.name,**record}
    for name in ("before","archived"):
        destination=output/(name+".tsv")
        destination.write_bytes(retrieve(metadata[name]))
        records[name]={"path":destination.name,**metadata[name]}
    (output/"MANIFEST.json").write_text(json.dumps({"schema":metadata["schema"],"files":records,"scopes":metadata["scopes"],
        "historical_source_verification":"Payload identities verified here. Historical source attribution is declared from the linked published receipts, not independently re-executed or reverified by this downloader.",
        "source_receipts":{"before":metadata["before_source_receipt"],"post_split":metadata["post_split_receipt"],"SAF_bridge":metadata["SAF_source_bridge"]}},indent=2)+"\n")
    return records

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--archive",type=Path)
    args=parser.parse_args()
    result=materialize(args.output,args.archive)
    print(json.dumps({"status":"COMPLETE","files":len(result),"output":str(args.output)}))

if __name__=="__main__":main()

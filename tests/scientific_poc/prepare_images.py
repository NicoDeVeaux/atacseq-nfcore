#!/usr/bin/env python3
"""Pull the small trial's declared tool images; never launch a scientific job."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from duplicate_marking import PICARD, SAMTOOLS
from saf_boundary import SUBREAD, SAMTOOLS as SAF_SAMTOOLS

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,required=True)
    parser.add_argument("--docker-context",default="default")
    args=parser.parse_args()
    targets=json.loads(Path(__file__).with_name("targets.json").read_text())
    refs=[targets["base"],next(t["head"] for t in targets["targets"] if t["pr"]==474)]
    images={PICARD,SAMTOOLS,SUBREAD,SAF_SAMTOOLS}
    for ref in refs:
        module=subprocess.check_output(["git","-C",str(args.repo),"show",ref+":modules/nf-core/trimgalore/main.nf"],text=True)
        candidates={image for image in re.findall(r"'([^']*trim-galore:[^']+)'",module) if not image.startswith("https://")}
        if len(candidates)!=1:raise ValueError("Ambiguous source-declared Trim Galore image")
        images.update(candidates)
    for image in sorted(images):
        subprocess.run(["docker","--context",args.docker_context,"pull","--platform","linux/amd64",image],check=True)

if __name__=="__main__":main()

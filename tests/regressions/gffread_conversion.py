#!/usr/bin/env python3
"""Check the actual GFFREAD module's packaged GFF-to-GTF conversion."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--nextflow', default='nextflow')
    parser.add_argument('--nextflow-jar', type=Path)
    parser.add_argument('--docker-context')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = (args.output or Path(tempfile.mkdtemp(prefix='atacseq-gffread-conversion-'))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('Use an empty output directory')
    module = args.repo/'modules/nf-core/gffread/main.nf'
    shutil.copyfile(module, root/'module.nf')
    (root/'annotation.gff3').write_text('''##gff-version 3
chr1\tfixture\tgene\t1\t100\t.\t+\t.\tID=gene1
chr1\tfixture\tmRNA\t1\t100\t0.5\t+\t.\tID=tx1;Parent=gene1
chr1\tfixture\texon\t1\t25\t.\t+\t.\tParent=tx1
chr1\tfixture\texon\t50\t100\t.\t+\t.\tParent=tx1
''')
    (root/'main.nf').write_text("include { GFFREAD } from './module'\nworkflow {\n GFFREAD(Channel.of([[id:'annotation'], file('annotation.gff3')]), [])\n GFFREAD.out.versions_gffread.view { process_name, tool, version -> \"OBSERVED_GFFREAD_VERSION ${version}\" }\n}\n")
    (root/'nextflow.config').write_text("process { executor='local'; cpus=1; memory='1 GB'; ext.args='--keep-exon-attrs -F -T'; ext.prefix='converted' }\ndocker.enabled=true\ndocker.runOptions='--network none --platform linux/amd64'\nconda.enabled=false\nnextflow.enable.telemetry=false\ntrace.fields='task_id,hash,name,status,exit,container'\n")
    command = (['java', '-jar', str(args.nextflow_jar.resolve())] if args.nextflow_jar
               else [shutil.which(args.nextflow) or args.nextflow])
    env = {key: os.environ[key] for key in ('HOME', 'PATH', 'JAVA_HOME', 'LANG') if key in os.environ}
    env.update(NXF_OFFLINE='true', NXF_ANSI_LOG='false', NXF_DISABLE_CHECK_LATEST='true')
    if args.docker_context:
        env['DOCKER_CONTEXT'] = args.docker_context
    result = subprocess.run(command+['run', 'main.nf', '-ansi-log', 'false', '-with-trace', 'trace.tsv', '-work-dir', 'work'],
                            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    (root/'run.out').write_bytes(result.stdout)
    result.check_returncode()
    outputs = list((root/'work').rglob('converted.gtf'))
    if len(outputs) != 1:
        raise AssertionError('Expected exactly one converted GTF')
    body = outputs[0].read_text()
    rows = [line.split('\t') for line in body.splitlines() if line and not line.startswith('#')]
    exons = {(row[0], int(row[3]), int(row[4]), row[6]) for row in rows if row[2] == 'exon'}
    match = re.search(rb'OBSERVED_GFFREAD_VERSION\s+(\d+)\.(\d+)\.(\d+)', result.stdout)
    gates = {'two_exon_coordinates_preserved': exons == {('chr1', 1, 25, '+'), ('chr1', 50, 100, '+')},
             'gene_and_transcript_ids_preserved': all('transcript_id "tx1"' in row[8] and 'gene_id "gene1"' in row[8] for row in rows if row[2] == 'exon'),
             'packaged_fixed_runtime_observed': bool(match and tuple(map(int, match.groups())) >= (0, 12, 9))}
    record = {'gates': gates, 'source_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
              'gtf_sha256': hashlib.sha256(body.encode()).hexdigest(),
              'runtime': match.group(0).decode() if match else None,
              'scope': 'Actual module and packaged tool, benign annotation conversion; not a full-pipeline or all-input equivalence test'}
    (root/'result.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record))
    return 0 if all(gates.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check real Trim Galore module outputs against explicit paired-read semantics."""
import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


R1 = 'TACCTGACTGTCAGTACGATGCTACCTAGTCACTGACCTGATCGTACGACTACGTC'
R2 = 'CGTATGACCTACAGTCGATCCTAGTCAGTTCGACATGCTGACCTAGTACGTCAGTC'
CASES = {
    'r2_5prime': {'read1': R1, 'read2': R2, 'expected1': R1, 'expected2': R2[7:], 'clip_r2': 7},
    'r2_3prime': {'read1': R1, 'read2': R2, 'expected1': R1, 'expected2': R2[:-9], 'three_prime_clip_r2': 9},
    'poly_g': {'read1': R1 + 'G' * 24, 'read2': 'C' * 24 + R2,
               'expected1': R1 + 'G' * 24, 'expected2': 'C' * 24 + R2},
}
SOURCE_FILES = ('conf/modules.config', 'modules/nf-core/trimgalore/main.nf', 'nextflow.config',
                'modules/nf-core/trimgalore/environment.yml')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def records(name, sequence, mate, count=32):
    return [(f'{name}_{index:03}/{mate}', sequence, 'I' * len(sequence)) for index in range(count)]


def write_fastq(path, rows):
    content = ''.join(f'@{name}\n{sequence}\n+\n{quality}\n' for name, sequence, quality in rows)
    Path(path).write_bytes(gzip.compress(content.encode(), mtime=0))


def read_fastq(path):
    with gzip.open(path, 'rt', encoding='ascii') as stream:
        lines = stream.read().splitlines()
    if len(lines) % 4:
        raise ValueError('Incomplete FASTQ record')
    result = []
    for offset in range(0, len(lines), 4):
        name, sequence, plus, quality = lines[offset:offset + 4]
        if not name.startswith('@') or not plus.startswith('+') or len(sequence) != len(quality):
            raise ValueError('Malformed FASTQ record')
        result.append((name[1:], sequence, quality))
    return result


def compare_records(expected, observed):
    wanted = {row[0]: row[1:] for row in expected}
    found = {row[0]: row[1:] for row in observed}
    failures = []
    if len(found) != len(observed):
        failures.append({'reason': 'duplicate_read_id'})
    for name in sorted(set(wanted) | set(found)):
        if wanted.get(name) != found.get(name):
            failures.append({'reason': 'read_record_differs', 'id': name,
                             'expected': wanted.get(name), 'observed': found.get(name)})
    return {'passed': not failures, 'expected_records': len(expected), 'observed_records': len(observed),
            'mismatched_records': sum(row['reason'] == 'read_record_differs' for row in failures),
            'failures': failures[:8], 'expected': expected, 'observed': observed}


def assess_fastq(expected, path):
    try:
        return compare_records(expected, read_fastq(path))
    except (ValueError, OSError, UnicodeError, EOFError) as error:
        return {'passed': False, 'reason': 'invalid_fastq_output', 'error': str(error), 'expected': expected}


def git_identity(repo):
    def git(*arguments):
        completed = subprocess.run(['git', '-C', str(repo), *arguments], capture_output=True, text=True)
        return completed.stdout.strip() if completed.returncode == 0 else None
    head = git('rev-parse', 'HEAD')
    status = git('status', '--porcelain')
    return {'observed_head': head, 'clean': status == '' if status is not None else None,
            'clean_commit': head if status == '' else None}


def source_hashes(repo):
    return {name: digest(repo / name) for name in SOURCE_FILES}


def invoke(command, folder, env=None, timeout=180):
    completed = subprocess.run(command, cwd=folder, env=env, capture_output=True, timeout=timeout)
    return completed


def configuration(repo, folder, case):
    parameters = {'trim_nextseq': 0, 'clip_r1': 0, 'clip_r2': 0, 'three_prime_clip_r1': 0,
                  'three_prime_clip_r2': 0, 'save_trimmed': True, 'aligner': 'bwa',
                  'publish_dir_mode': 'copy', 'outdir': str(folder / 'published')}
    parameters.update({key: value for key, value in CASES[case].items() if key.startswith(('clip_', 'three_prime_clip_'))})
    defaults = re.findall(r'(?ms)^params\s*\{.*?^\}', (repo / 'nextflow.config').read_text())
    if len(defaults) != 1:
        raise ValueError('Cannot isolate the production top-level parameter defaults')
    encoded = '\n'.join(f'params.{key} = {json.dumps(value)}' for key, value in parameters.items())
    return defaults[0] + '\n' + encoded + '\nincludeConfig ' + json.dumps(str(repo / 'conf/modules.config')) + '''
process.executor = 'local'
process.cpus = 1
process.memory = '2 GB'
process.time = '2 min'
process.maxForks = 1
conda.enabled = false
docker.enabled = true
docker.runOptions = '--platform linux/amd64 --network none --cpus 1 --memory 2g'
nextflow.enable.telemetry = false
trace.fields = 'task_id,hash,name,status,exit,container,workdir,attempt'
'''


def run_case(args, root, case, env, image):
    folder = root / case
    folder.mkdir()
    spec = CASES[case]
    inputs = [folder / 'input_1.fastq.gz', folder / 'input_2.fastq.gz']
    for mate, path in enumerate(inputs, 1):
        write_fastq(path, records(case, spec[f'read{mate}'], mate))
    config = folder / 'probe.config'
    config.write_text(configuration(args.repo, folder, case))
    receipt = folder / 'process.json'
    command = [args.nextflow, '-C', str(config), 'run', str(Path(__file__).with_name('trim_probe.nf')),
               '-ansi-log', 'false', '-with-trace', 'trace.tsv', '-work-dir', str(folder / 'work'),
               '--repo', str(args.repo), '--case_name', case, '--read1', str(inputs[0]),
               '--read2', str(inputs[1]), '--receipt', str(receipt)]
    execution = invoke(command, folder, env, args.timeout)
    (folder / 'stdout.log').write_bytes(execution.stdout)
    (folder / 'stderr.log').write_bytes(execution.stderr)
    result = {'status': 'ERROR', 'command': command, 'exit': execution.returncode,
              'configuration_sha256': digest(config),
              'inputs': [{'path': str(path), 'sha256': digest(path)} for path in inputs]}
    if execution.returncode:
        result['error'] = 'Nextflow/module execution failed; no scientific verdict'
        return result
    with (folder / 'trace.tsv').open() as stream:
        trace = list(csv.DictReader(stream, delimiter='\t'))
    tasks = [row for row in trace if row['name'].split(' (')[0].split(':')[-1] == 'TRIMGALORE']
    if len(tasks) != 1 or tasks[0]['status'] != 'COMPLETED' or tasks[0]['exit'] != '0':
        result['error'] = 'Expected one completed, uncached real TRIMGALORE task'
        result['trace'] = trace
        return result
    task = tasks[0]
    if task['container'] != image:
        raise ValueError('Actual task container differs from the source-declared image')
    work = Path(task['workdir'])
    result['task'] = task
    result['task_files'] = {name: {'path': str(work / name), 'sha256': digest(work / name)}
                            for name in ('.command.sh', '.command.run', '.command.out', '.command.err', '.exitcode')}
    result['actual_command'] = (work / '.command.sh').read_text()
    emitted = [Path(path) for path in json.loads(receipt.read_text())['reads']]
    result['mates'] = []
    for mate in (1, 2):
        paths = [path for path in emitted if path.name.startswith(f'{case}_{mate}_')]
        expected = records(case, spec[f'expected{mate}'], mate)
        if len(paths) != 1:
            result['mates'].append({'passed': False, 'reason': 'missing_or_ambiguous_mate',
                                    'mate': mate, 'emitted': [str(path) for path in emitted], 'expected': expected})
            continue
        path = paths[0]
        assessment = assess_fastq(expected, path)
        result['mates'].append({'mate': mate, 'path': str(path), 'sha256': digest(path), **assessment})
    result['status'] = 'PASS' if all(mate['passed'] for mate in result['mates']) else 'FAIL'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--docker-context', default='colima')
    parser.add_argument('--nextflow', default='nextflow')
    parser.add_argument('--nextflow-version', default='25.10.4')
    parser.add_argument('--java-home', type=Path)
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error('--output must be empty; retained evidence is never overwritten')
    result = {'schema': 'atacseq-trimming-scientific-poc/v1', 'status': 'ERROR', 'cases': {},
              'scope': 'Real Nextflow module and full production modules config, declared Docker image, synthetic paired reads. No whole-pipeline or biological count qualification.',
              'limits': 'Fixed high-quality adapter-free witnesses; no exhaustive adapter/quality/SE coverage, no legacy standalone tool claimed as branch execution.'}
    try:
        before = source_hashes(args.repo)
        result.update(source_root=str(args.repo), source_hashes_before=before, git=git_identity(args.repo),
                      harness_sha256={path.name: digest(path) for path in (Path(__file__), Path(__file__).with_name('trim_probe.nf'))})
        module = (args.repo / SOURCE_FILES[1]).read_text()
        images = [value for value in re.findall(r"'([^']*trim-galore:[^']+)'", module) if not value.startswith('https://')]
        if len(set(images)) != 1:
            raise ValueError('Cannot identify a unique declared Trim Galore Docker image')
        image = images[0]
        docker = ['docker', '--context', args.docker_context]
        inspected = invoke(docker + ['image', 'inspect', image], root)
        if inspected.returncode:
            raise ValueError('Source-declared image is not cached in the requested Docker context')
        identity = json.loads(inspected.stdout)[0]
        result['container'] = {'declared_reference': image, 'id': identity['Id'],
                               'repo_digests': identity['RepoDigests'], 'architecture': identity['Architecture'],
                               'os': identity['Os'], 'docker_context': args.docker_context}
        binary_command = docker + ['run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
                                  '--cpus', '1', '--memory', '2g', '--entrypoint', 'sh', image, '-c',
                                  'command -v trim_galore; sha256sum "$(command -v trim_galore)"; trim_galore --version']
        binary = invoke(binary_command, root)
        (root / 'tool.stdout.log').write_bytes(binary.stdout)
        (root / 'tool.stderr.log').write_bytes(binary.stderr)
        if binary.returncode:
            raise ValueError('Tool identity command failed')
        result['binary_identity'] = {'command': binary_command, 'stdout': binary.stdout.decode(), 'exit': binary.returncode}
        env = {**os.environ, 'DOCKER_CONTEXT': args.docker_context, 'NXF_VER': args.nextflow_version,
               'NXF_HOME': str(root / 'nxf-home'), 'NXF_OFFLINE': 'true', 'NXF_ANSI_LOG': 'false',
               'NXF_DISABLE_CHECK_LATEST': 'true', 'NXF_JVM_ARGS': '-Xms128m -Xmx1g'}
        framework = Path(os.environ.get('NXF_DIST', str(Path.home() / '.nextflow/framework')))
        env['NXF_DIST'] = str(framework)
        if args.java_home:
            env['JAVA_HOME'] = str(args.java_home.resolve())
        version = invoke([args.nextflow, '-version'], root, env)
        if version.returncode:
            raise ValueError('Nextflow launcher preflight failed: ' + version.stderr.decode()[:500])
        result['nextflow'] = {'requested': args.nextflow_version, 'version_stdout': version.stdout.decode(),
                              'launcher': shutil.which(args.nextflow), 'framework': str(framework),
                              'java_home': env.get('JAVA_HOME')}
        if f'version {args.nextflow_version}' not in version.stdout.decode():
            raise ValueError('Actual Nextflow version differs from the requested version')
        jar = framework / args.nextflow_version / f'nextflow-{args.nextflow_version}-one.jar'
        if jar.exists():
            result['nextflow']['framework_jar'] = {'path': str(jar), 'sha256': digest(jar)}
        for case in args.case or CASES:
            result['cases'][case] = run_case(args, root, case, env, image)
        after = source_hashes(args.repo)
        result['source_hashes_after'] = after
        if before != after:
            raise ValueError('Source changed during execution')
        result['harness_sha256_after'] = {path.name: digest(path) for path in
                                         (Path(__file__), Path(__file__).with_name('trim_probe.nf'))}
        if result['harness_sha256'] != result['harness_sha256_after']:
            raise ValueError('Harness changed during execution')
        states = [case['status'] for case in result['cases'].values()]
        result['status'] = 'ERROR' if 'ERROR' in states else 'FAIL' if 'FAIL' in states else 'PASS'
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result['status'] = 'ERROR'
        result['error'] = f'{type(error).__name__}: {error}'
    (root / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'cases': {name: case['status'] for name, case in result['cases'].items()},
                      'result': str(root / 'RESULT.json')}))
    return {'PASS': 0, 'FAIL': 1, 'ERROR': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())

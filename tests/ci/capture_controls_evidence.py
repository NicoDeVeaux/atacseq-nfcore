#!/usr/bin/env python3
"""Retain a small allowlist of actual controls reporting inputs and outputs."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import stat
import sys

SOURCE_FILES = (
    'assets/multiqc_config.yml', 'conf/modules.config', 'conf/test_controls.config',
    'tests/nextflow.config', 'nf-test.config', 'bin/multiqc_fingerprint.py',
    'modules/local/multiqc.nf', 'modules/nf-core/deeptools/plotfingerprint/main.nf',
)
EXPORTS = ('deeptools_plot_fingerprint_counts_mlib_deeptools.txt',
           'mqc_deeptools_fingerprint_plot_1.txt')
VERSIONS = 'nf_core_atacseq_software_mqc_versions.yml'
TASKS = {'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT', 'MULTIQC'}
HASH = re.compile(r'([0-9a-f]{2})/([0-9a-f]{6,30})')
UNSAFE = re.compile(
    r'/wt/|/ephemeral/|(?:https?|docker|oras|s3|gs)://[^/\s]+@|'
    r'(?i:[?&](?:x-amz-[^=\s]+|x-goog-[^=\s]+|sig|signature|token|access_token|awsaccesskeyid)=)|'
    r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16})|'
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:bearer\s+[A-Za-z0-9._~+/=-]{8,})|'
    r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b|'
    r'(?im:^(?:export\s+)?(?:AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GITHUB_TOKEN|GH_TOKEN)\s*=\s*[\"\']?[A-Za-z0-9/+_=.-]{8,})'
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Capture:
    def __init__(self, output):
        self.output = output
        self.files = []
        self.issues = []

    def issue(self, role, reason):
        self.issues.append({'role': role, 'reason': reason})

    def read(self, path, boundaries, role, limit=8 * 1024 * 1024):
        try:
            target = path.resolve(strict=True)
            if not any(target.is_relative_to(boundary) for boundary in boundaries):
                self.issue(role, 'outside_allowed_boundary'); return None
            if target.name != path.name:
                self.issue(role, 'unexpected_symlink_target_name'); return None
            before = target.stat()
            if not stat.S_ISREG(before.st_mode):
                self.issue(role, 'not_regular'); return None
            if before.st_size > limit:
                self.issue(role, 'size_limit'); return None
            with target.open('rb') as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                self.issue(role, 'size_limit'); return None
            after = target.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                self.issue(role, 'changed_during_read'); return None
            return data
        except (OSError, RuntimeError):
            self.issue(role, 'missing_or_unreadable'); return None

    def copy(self, path, relative, boundaries, role, limit=8 * 1024 * 1024):
        data = self.read(path, boundaries, role, limit)
        if data is None:
            return
        try:
            text = data.decode('utf-8')
        except UnicodeError:
            self.issue(role, 'not_utf8'); return
        if UNSAFE.search(text):
            self.issue(role, 'credential_or_capability_text_omitted'); return
        target = self.output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.issue(role, 'duplicate_destination'); return
        with target.open('xb') as stream:
            stream.write(data)
        resolved = path.resolve()
        boundary = next((b for b in boundaries if resolved.is_relative_to(b)), None)
        if boundary is None:
            self.issue(role, 'source_changed_after_read')
        self.files.append({'path': str(relative), 'role': role, 'bytes': len(data), 'sha256': digest(data),
                           'was_symlink': path.is_symlink(),
                           'resolved_relative_path': str(resolved.relative_to(boundary)) if boundary else None})


def test_roots(workspace, workdir, home):
    raw = Path(workdir)
    literal = raw if raw.is_absolute() else workspace / raw
    candidates = [literal / 'tests']
    if workdir == '~' or workdir.startswith('~/'):
        candidates.append(home / workdir[2:] / 'tests' if workdir != '~' else home / 'tests')
    unique = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def identity(capture, case, workspace):
    path = case / 'meta/nextflow.log'
    data = capture.read(path, [case], 'case_metadata')
    if data is None:
        return None
    try:
        text = data.decode('utf-8')
        line = next(line.split('$> ', 1)[1] for line in text.splitlines()
                    if 'nextflow.cli.Launcher - $> ' in line)
        args = shlex.split(line)
        profiles = args[args.index('-profile') + 1].split(',')
        if not all(re.fullmatch(r'[A-Za-z0-9_-]+', profile) and not UNSAFE.search(profile) for profile in profiles):
            raise ValueError('Invalid profiles')
        script = Path(args[args.index('run') + 1]).resolve()
        if script != workspace / 'main.nf' or 'test_controls' not in profiles:
            return None
        version = re.search(r'N E X T F L O W\s+~\s+version\s+([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?)', text)
        return {'profiles': profiles, 'nextflow_version': version[1] if version else None,
                'metadata_sha256': digest(data)}
    except (ValueError, StopIteration, IndexError, UnicodeError):
        capture.issue('case_metadata', 'unrecognized_launcher_metadata')
        return None


def work_directory(case, value):
    match = HASH.fullmatch(value)
    if match is None:
        return None
    parent = case / 'work' / match[1]
    if not parent.resolve().is_relative_to(case / 'work') or not parent.is_dir():
        return None
    found = [p for p in parent.glob(match[2] + '*') if re.fullmatch(r'[0-9a-f]{30}', p.name)
             and not p.is_symlink() and p.is_dir()]
    return found[0] if len(found) == 1 else None


def raw_inputs(capture, directory, relative, case, role):
    if not directory.resolve().is_relative_to(case / 'work'):
        capture.issue(role, 'outside_allowed_boundary'); return
    files = sorted(directory.glob('*.plotFingerprint.raw.txt'))
    if not files:
        capture.issue(role, 'raw_matrix_missing')
    for path in files:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,179}\.plotFingerprint\.raw\.txt', path.name) or UNSAFE.search(path.name):
            capture.issue(role, 'unsupported_raw_matrix_name'); continue
        capture.copy(path, relative / path.name, [case / 'work'], role)


def task_evidence(capture, case, relative, workspace):
    data = capture.read(case / 'meta/trace.csv', [case], 'selected_task_metadata', 2 * 1024 * 1024)
    if data is None:
        return []
    tasks = []
    try:
        rows = csv.DictReader(io.StringIO(data.decode('utf-8')), delimiter='\t')
        for row in rows:
            process = row.get('name', '').split(' (', 1)[0].rsplit(':', 1)[-1]
            if process not in TASKS:
                continue
            task_id, task_hash = row.get('task_id', ''), row.get('hash', '')
            if not task_id.isdecimal() or HASH.fullmatch(task_hash) is None:
                capture.issue(process, 'invalid_task_identity'); continue
            if len(tasks) >= 100:
                capture.issue('trace', 'selected_task_limit'); break
            reference = row.get('container')
            container_state = 'from_trace' if reference else 'not_present_in_trace'
            if reference and (UNSAFE.search(reference) or re.match(r'[^/\s]+:[^/\s]+@', reference)
                              or not re.fullmatch(r'[A-Za-z0-9:/@._+-]+', reference)):
                reference = None
                container_state = 'unsafe_reference_omitted'
            task = {'task_id': task_id, 'hash': task_hash, 'process': process,
                    'status': row.get('status') if row.get('status') in {'COMPLETED', 'CACHED', 'FAILED', 'ABORTED', 'RUNNING', 'SUBMITTED'} else None,
                    'exit': row.get('exit') if re.fullmatch(r'-|[0-9]+', row.get('exit', '')) else None,
                    'container_from_trace': reference, 'container_identity': container_state,
                    'trace_sha256': digest(data)}
            name = row.get('name', '')
            if re.fullmatch(r'[A-Za-z0-9_.:+() -]{1,240}', name) and not UNSAFE.search(name):
                task['name'] = name
            tasks.append(task)
            directory = work_directory(case, task_hash)
            if directory is None:
                capture.issue(process + '/' + task_hash, 'missing_or_ambiguous_work_directory'); continue
            destination = relative / 'tasks' / (task_id + '-' + task_hash.replace('/', '-'))
            capture.copy(directory / '.command.sh', destination / 'command.sh', [directory], process + '/command', 128 * 1024)
            if process == 'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT':
                raw_inputs(capture, directory, destination / 'raw', case, process + '/raw')
            else:
                raw_inputs(capture, directory / 'deeptools', destination / 'staged_raw', case, 'MULTIQC/staged_raw')
                config = directory / 'multiqc_config.yml'
                capture.copy(config, destination / 'multiqc_config.yml', [directory, workspace / 'assets'], 'MULTIQC/config', 128 * 1024)
                capture.copy(directory / VERSIONS, destination / VERSIONS,
                             [case / 'work', case / 'output/pipeline_info'], 'MULTIQC/observed_tool_versions', 128 * 1024)
                data_dirs = sorted(p for p in directory.glob('*_data')
                                   if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*_data', p.name) and not UNSAFE.search(p.name))
                if not data_dirs:
                    capture.issue('MULTIQC/exports', 'data_directory_missing')
                for data_dir in data_dirs:
                    for name in EXPORTS:
                        capture.copy(data_dir / name, destination / 'exports' / data_dir.name / name,
                                     [directory], 'MULTIQC/' + name)
    except (UnicodeError, csv.Error):
        capture.issue('trace', 'unrecognized_trace_format')
    for process in TASKS:
        if not any(task['process'] == process for task in tasks):
            capture.issue(process, 'no_selected_task')
    return tasks


def collect(workspace, workdir, output, source_commit, since=None, home=None, requested=None, nf_test_outcome='unknown'):
    workspace = workspace.resolve()
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError('Output must be a new or empty regular directory')
    output.mkdir(parents=True, exist_ok=True)
    capture = Capture(output)
    if nf_test_outcome not in {'success', 'failure', 'cancelled', 'skipped', 'unknown'}:
        raise ValueError('Invalid nf-test outcome')
    report = {'schema': 'nf-core-atacseq-controls-evidence/v1', 'source_commit': source_commit,
              'collector_sha256': digest(Path(__file__).read_bytes()), 'python_version': sys.version.split()[0],
              'requested': requested or {}, 'nf_test_step_outcome': nf_test_outcome,
              'cases': [], 'files': capture.files, 'issues': capture.issues,
              'scope': 'Only controls task commands, raw fingerprint matrices, selected configuration/source, observed tool versions and the two fingerprint exports. No environment, launch wrappers or raw logs.'}
    seen = set()
    for root_index, root in enumerate(test_roots(workspace, workdir, home or Path.home())):
        if root.is_symlink() or not root.is_dir():
            continue
        for case in sorted(root.iterdir()):
            if not re.fullmatch(r'[0-9a-f]{32}', case.name) or case.is_symlink() or not case.is_dir():
                continue
            case = case.resolve()
            if case in seen:
                continue
            seen.add(case)
            log = case / 'meta/nextflow.log'
            if since is not None and log.exists() and log.stat().st_mtime_ns < since:
                continue
            start = len(capture.issues)
            info = identity(capture, case, workspace)
            if info is None:
                # Other nf-test cases share each shard; never copy their payloads.
                del capture.issues[start:]
                continue
            key = str(root_index) + '-' + case.name
            if info['nextflow_version'] is None:
                capture.issue(key, 'observed_nextflow_version_missing')
            tasks = task_evidence(capture, case, Path('cases') / key, workspace)
            report['cases'].append({'case_id': case.name, 'root_kind': 'literal' if root_index == 0 else 'expanded_home',
                                    **info, 'tasks': tasks, 'complete': len(capture.issues) == start})
    if report['cases']:
        for relative in SOURCE_FILES:
            source = workspace / relative
            capture.copy(source, Path('source') / relative.replace('/', '__'), [workspace], 'source/' + relative, 128 * 1024)
    tap = capture.read(workspace / 'test.tap', [workspace], 'test_selection', 512 * 1024) if (workspace / 'test.tap').exists() else None
    statuses = []
    if tap:
        for line in tap.decode('utf-8', errors='replace').splitlines():
            match = re.fullmatch(r'(not ok|ok)\s+([0-9]+)\s+(.+)', line)
            if match and '-profile test_controls' in match[3]:
                statuses.append({'number': int(match[2]), 'status': 'failed' if match[1] == 'not ok' else 'passed'})
    report['controls_tap'] = {'matched_tests': len(statuses), 'statuses': statuses}
    if len(statuses) > len(report['cases']):
        capture.issue('controls', 'selected_controls_case_not_identified')
    report['complete'] = not capture.issues
    report['capture_complete'] = report['complete']
    report['status'] = ('complete' if report['complete'] else 'partial') if report['cases'] else ('not_applicable' if report['complete'] else 'partial')
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--since-file', type=Path, required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--shard', required=True)
    parser.add_argument('--nextflow', required=True)
    parser.add_argument('--nf-test', required=True)
    parser.add_argument('--nf-test-outcome', choices=['success', 'failure', 'cancelled', 'skipped', 'unknown'], default='unknown')
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
            raise ValueError('Source identity unavailable')
        requested = {'profile': args.profile, 'shard': args.shard,
                     'nextflow': args.nextflow, 'nf_test': args.nf_test}
        if not all(re.fullmatch(r'[A-Za-z0-9_.+/-]{1,64}', value) and not UNSAFE.search(value)
                   for value in requested.values()):
            raise ValueError('Invalid requested identity')
        report = collect(args.workspace, args.workdir, args.output, args.source_commit,
                         since=args.since_file.stat().st_mtime_ns, requested=requested,
                         nf_test_outcome=args.nf_test_outcome)
    except (OSError, ValueError, RuntimeError):
        # Do not print exception text: an OS/parser message can contain an unsafe input.
        if args.output.is_dir() and not args.output.is_symlink():
            manifest = args.output / 'manifest.json'
            if not manifest.exists():
                manifest.write_text(json.dumps({'schema': 'nf-core-atacseq-controls-evidence/v1',
                                                'status': 'partial', 'complete': False,
                                                'capture_complete': False, 'nf_test_step_outcome': args.nf_test_outcome,
                                                'issues': [{'role': 'collector', 'reason': 'capture_failed_or_start_marker_missing'}]}, indent=2) + '\n')
        print('::warning::Controls evidence capture could not complete; see its manifest.')
        return 1
    print(json.dumps({'controls_cases': len(report['cases']), 'files': len(report['files']), 'status': report['status']}))
    if not report['complete']:
        print('::warning::Controls evidence is incomplete; see the allowlisted artifact manifest.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

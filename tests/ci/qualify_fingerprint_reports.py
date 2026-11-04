#!/usr/bin/env python3
"""Qualify captured fingerprint exports inside the pinned MultiQC 1.13 image.

The capture is read-only input. Only the trusted repository helper is executed;
captured shell commands supply parsed, allowlisted arguments, never shell code.
PASS concerns reporting semantics and exact exports, independently of nf-test's
original outcome. Native CI still owns the pipeline/snapshot gate. Unsupported
contexts fail closed. No snapshots are written.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shlex
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
IMAGE = 'quay.io/biocontainers/multiqc@sha256:db913f47894a386040d76ae5f46b19149a71377f5107327d15ec89d3a1ec3f5f'
RAW = 'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT/raw'


class EvidenceError(ValueError):
    pass


class UnknownContext(ValueError):
    pass


def require(value, reason):
    if not value:
        raise EvidenceError(reason)


def hashes(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'md5': hashlib.md5(data).hexdigest()}


def captured(root, record):
    relative = PurePosixPath(record['path'])
    require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe evidence path')
    path = root.joinpath(*relative.parts)
    require(path.resolve(strict=True) == path and path.is_file(), 'Missing or symlinked evidence')
    actual = hashes(path)
    require(actual['bytes'] == record['bytes'] and actual['sha256'] == record['sha256'], 'Captured bytes differ from manifest')
    return path


def invocation(text, executable):
    lines = text.replace('\\\n', ' ').splitlines()
    commands = []
    for line in lines:
        if not re.match(r'^\s*(?:[^\s]+/)?' + re.escape(executable) + r'(?:\s|$)', line):
            continue
        tokens = shlex.split(line, comments=True)
        if tokens and Path(tokens[0]).name == executable:
            commands.append(tokens)
    require(len(commands) == 1, 'Missing or ambiguous actual tool invocation')
    return commands[0]


def context(command):
    tokens = invocation(command, 'multiqc_fingerprint.py')[1:]
    configs, arguments, naming = [], [], {}
    values = {'--title', '--filename', '-n', '--comment', '-i', '--data-format'}
    flags = {'-f', '--force', '--flat', '--interactive', '--export', '--fullnames', '-s'}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if '=' in token and token.startswith('--'):
            option, value = token.split('=', 1)
            tokens[i:i + 1] = [option, value]
            token = option
        if token in {'--config', '-c'}:
            require(i + 1 < len(tokens), 'Missing config argument')
            name = tokens[i + 1]
            if PurePosixPath(name).name != name:
                raise UnknownContext('Config requires unavailable original path context')
            configs.append(name); arguments.extend([token, name]); i += 2
        elif token in values:
            require(i + 1 < len(tokens), 'Missing MultiQC option value')
            value = tokens[i + 1]
            require(not any(c in value for c in ['$', '`', '\n']), 'Unevaluated shell value in command')
            arguments.extend([token, value])
            if token == '--data-format': naming['data_format'] = value
            i += 2
        elif token in flags:
            arguments.append(token)
            if token in {'--fullnames', '-s'}: naming['fn_clean_sample_names'] = False
            i += 1
        elif token == '.':
            i += 1
        else:
            raise UnknownContext('Unsupported effective MultiQC argument')
    return {'arguments': arguments, 'config_names': configs, 'naming_options': naming}


def raw_columns(text):
    lines = [line for line in text.splitlines() if line and line != '#plotFingerprint --outRawCounts']
    require(len(lines) >= 2, 'Empty raw fingerprint matrix')
    labels = [s.strip("'") for s in lines[0].split('\t')]
    require(all(labels) and len(labels) == len(set(labels)), 'Duplicate or empty raw labels')
    columns = [[] for _ in labels]
    for line in lines[1:]:
        values = line.split('\t')
        require(len(values) == len(labels), 'Ragged raw fingerprint matrix')
        for column, value in zip(columns, values):
            number = float(value)
            require(math.isfinite(number) and number >= 0 and number.is_integer(), 'Invalid raw integer count')
            column.append(value)
    require(all(sum(map(float, column)) > 0 for column in columns), 'Zero-total raw fingerprint series')
    return labels, columns


def original_oracle(plan):
    import multiqc
    from multiqc import config
    from multiqc.modules.base_module import BaseMultiqcModule
    from multiqc.modules.deeptools.deeptools import MultiqcModule
    from multiqc.modules.deeptools.plotFingerprint import plotFingerprintMixin
    require(multiqc.__version__ == '1.13', 'Unexpected MultiQC version')
    config.mqc_load_userconfig(paths=plan['context']['config_names'])
    for key, value in plan['context']['naming_options'].items(): setattr(config, key, value)
    if config.prepend_dirs:
        raise UnknownContext('Directory-prefix naming needs original directory provenance')
    class Reader:
        anchor = 'deeptools'
        clean_s_name = BaseMultiqcModule.clean_s_name
        _int = MultiqcModule._int
    reader = Reader(); expected = {}; records = []
    for source in plan['raw']:
        path = Path(source['path']); labels, columns = raw_columns(path.read_text())
        for label, column in zip(labels, columns):
            f = {'f': "#plotFingerprint --outRawCounts\n'ORACLE_ROLE'\n" + '\n'.join(column) + '\n',
                 'fn': source['name'], 'root': 'deeptools', 'sp_key': 'deeptools/plotFingerprintOutRawCounts'}
            parsed = plotFingerprintMixin.parsePlotFingerprintOutRawCounts(reader, f)
            require(len(parsed) == 1, 'Original per-role parser lost a series')
            # Reserved producer labels are metadata identities, not filenames.
            protected = label.startswith('control::') and label.endswith('.mLb.clN') and config.fn_clean_sample_names
            filename_selected = config.use_filename_as_sample_name is True or isinstance(config.use_filename_as_sample_name, list) and f['sp_key'] in config.use_filename_as_sample_name
            clean = config.fn_clean_sample_names
            try:
                if protected and not filename_selected:
                    config.fn_clean_sample_names = False
                    name = reader.clean_s_name(label[:-len('.mLb.clN')], f)
                else:
                    name = reader.clean_s_name(label, f)
            finally:
                config.fn_clean_sample_names = clean
            require(name not in expected, 'Cleaned scientific role identities collide')
            expected[name] = {str(float(x)): float(y) for x, y in next(iter(parsed.values())).items()}
        records.append({'name': source['name'], **hashes(path), 'labels': labels, 'rows': len(columns[0])})
    return {'expected': expected, 'raw': records, 'multiqc_version': multiqc.__version__,
            'original_parser_sha256': hashes(Path(sys.modules[plotFingerprintMixin.__module__].__file__))['sha256'],
            'effective_data_format': config.data_format}


def replay(plan):
    from multiqc.modules.base_module import BaseMultiqcModule
    original = BaseMultiqcModule.find_log_files
    wanted = plan['order']; observed = []
    def ordered(self, key, *args, **kwargs):
        files = list(original(self, key, *args, **kwargs))
        if key == 'deeptools/plotFingerprintOutRawCounts':
            names = [f['fn'] for f in files]
            require(len(names) == len(wanted) and set(names) == set(wanted), 'Replay raw discovery differs from captured inputs')
            files.sort(key=lambda f: wanted.index(f['fn'])); observed.extend(f['fn'] for f in files)
        yield from files
    BaseMultiqcModule.find_log_files = ordered
    sys.argv = [str(REPO / 'bin/multiqc_fingerprint.py'), *plan['context']['arguments'], '--module', 'deeptools', '--outdir', 'report', *['deeptools/' + name for name in wanted]]
    try:
        runpy.run_path(str(REPO / 'bin/multiqc_fingerprint.py'), run_name='__main__')
    except SystemExit as error:
        require(error.code in {None, 0}, 'Repaired MultiQC replay failed')
    require(observed == wanted, 'Requested parser discovery order was not exercised')
    return {'actual_raw_parser_order': observed, 'ordering_control': 'Test harness orders only unchanged raw records returned to the installed parser'}


def worker(plan_path):
    plan = json.loads(plan_path.read_text())
    result = original_oracle(plan) if plan['mode'] == 'oracle' else replay(plan)
    Path('worker-result.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')


def run_worker(folder, plan, config_records, evidence):
    folder.mkdir()
    for record in config_records:
        source = captured(evidence, record)
        target = folder / source.name
        require(not target.exists(), 'Ambiguous captured config basename')
        shutil.copyfile(source, target)
    if plan['mode'] == 'replay':
        (folder / 'deeptools').mkdir()
        for raw in plan['raw']: shutil.copyfile(raw['path'], folder / 'deeptools' / raw['name'])
    (folder / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    with (folder / 'stdout.log').open('w') as stdout, (folder / 'stderr.log').open('w') as stderr:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', 'plan.json'], cwd=folder,
                                   env=dict(os.environ, HOME=str(folder / 'home')), stdout=stdout, stderr=stderr)
    require(completed.returncode == 0, 'Oracle or replay subprocess failed; inspect qualification logs')
    return json.loads((folder / 'worker-result.json').read_text())


def validator():
    spec = importlib.util.spec_from_file_location('fingerprint_validators', REPO / 'tests/regressions/fingerprint_reports.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module



DESIGNS = {
    'controls': {'SPT5_T0_REP1', 'SPT5_T0_REP2', 'SPT5_T15_REP1', 'SPT5_T15_REP2'},
    'default': {'OSMOTIC_STRESS_T0_PE_REP1', 'OSMOTIC_STRESS_T0_PE_REP2',
                'OSMOTIC_STRESS_T15_PE_REP1', 'OSMOTIC_STRESS_T100_SE_REP1',
                'OSMOTIC_STRESS_T100_SE_REP2', 'OSMOTIC_STRESS_T150_SE_REP1'},
}
CASES = {'default', 'controls', 'bowtie2', 'chromap', 'star', 'skip_consensus_peaks', 'skip_trimming'}


def check_selection(manifest):
    name = manifest.get('expected_test_case')
    require(name in CASES, 'Missing explicit expected matrix test case')
    require(manifest.get('requested', {}).get('test_file') in {'tests/' + name + '.nf.test', name + '.nf.test'}, 'Missing requested matrix test file')
    cases = [c for c in manifest.get('cases', []) if c.get('stub_run') is False]
    require(len(cases) == 1 and cases[0].get('test_case') == name, 'Real case selection differs from matrix test')
    require(manifest.get('requested', {}).get('nextflow') in {'25.10.4', '26.10.5'} and cases[0].get('nextflow_version') == manifest['requested']['nextflow'], 'Observed Nextflow differs from requested engine')
    tap = manifest.get('profile_tap', {}).get(name, {})
    require(tap.get('matched_tests') == 1 and len(tap.get('statuses', [])) == 1 and tap['statuses'][0].get('status') in {'passed', 'failed'}, 'Missing or ambiguous real-case TAP identity')
    require(cases[0].get('source_test_file') == 'tests/' + name + '.nf.test', 'Source test file identity mismatch')


def check_design(name, originals, raw):
    roles = DESIGNS['controls' if name == 'controls' else 'default']
    require(len(originals) == len(roles), 'Missing or extra fingerprint producer')
    require({n for n, _, _ in originals} == {role + '.mLb.clN.plotFingerprint.raw.txt' for role in roles}, 'Producer library set differs from frozen profile design')
    for record in raw:
        labels, _ = raw_columns(Path(record['path']).read_text())
        treatment = record['name'].removesuffix('.plotFingerprint.raw.txt')
        require(labels[0] == treatment and len(labels) == (2 if name == 'controls' else 1), 'Raw role design differs from frozen profile')
        if name == 'controls':
            require(labels[1] == 'control::' + treatment.removesuffix('.mLb.clN') + '::SPT5_INPUT_REP' + treatment.split('_REP')[1].split('.')[0] + '.mLb.clN', 'Control label differs from frozen comparison identity')


def validate_plot_table(path, expected):
    rows = [row for row in csv.reader(path.read_text().splitlines(), delimiter='\t') if row]
    require(len(rows) == 2 * len(expected), 'Numeric plot export loses a role')
    found = {}
    for i in range(0, len(rows), 2):
        header, row = rows[i:i + 2]
        require(header[0] == '' and len(header) == len(row) and row[0] not in found, 'Invalid or duplicate plot role')
        xs, ys = list(map(float, header[1:])), list(map(float, row[1:]))
        require(len(xs) == len(set(xs)) and all(map(math.isfinite, xs + ys)), 'Nonfinite or duplicate plot coordinates')
        found[row[0]] = {str(x): y for x, y in zip(xs, ys)}
    require(found == expected, 'Numeric plot TSV differs from original per-role parser')


def original_plot_md5(name):
    text = (REPO / 'tests' / (name + '.nf.test.snap')).read_text()
    values = set(re.findall(r'mqc_deeptools_fingerprint_plot_1\.txt:md5,([0-9a-f]{32})', text))
    require(len(values) == 1, 'Ambiguous original numeric plot snapshot constraint')
    return values.pop()


def qualify_case(evidence, manifest, case, folder):
    require(re.fullmatch(r'[0-9a-f]{32}', case.get('case_id', '')), 'Invalid captured case identity')
    require(case.get('stub_run') is False, 'Case missing explicit nonstub identity')
    require(case.get('complete') is True, 'Incomplete case capture')
    tasks = case['tasks']
    require(all(re.fullmatch(r'[0-9]+', t.get('task_id', '')) and re.fullmatch(r'[0-9a-f]{2}/[0-9a-f]{6,30}', t.get('hash', '')) for t in tasks), 'Invalid captured task identity')
    completed = [t for t in tasks if t['status'] in {'COMPLETED', 'CACHED'} and str(t['exit']) == '0']
    mqc = [t for t in completed if t['process'] == 'MULTIQC']; require(len(mqc) == 1, 'Missing or ambiguous completed MultiQC task')
    producers = [t for t in completed if t['process'] == 'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT']
    require(producers, 'No completed actual fingerprint producers')
    require({t.get('name') for t in tasks if t['process'] == 'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT'} <= {t.get('name') for t in producers}, 'Unrecovered fingerprint producer failure')
    keys = {PurePosixPath(r['path']).parts[1] for r in manifest['files']
            if len(PurePosixPath(r['path']).parts) > 2 and PurePosixPath(r['path']).parts[0] == 'cases'
            and re.fullmatch(r'[0-9]+-' + re.escape(case['case_id']), PurePosixPath(r['path']).parts[1])}
    require(len(keys) == 1, 'Missing or ambiguous captured case path')
    case_prefix = 'cases/' + keys.pop() + '/'
    def prefix(t): return case_prefix + 'tasks/' + t['task_id'] + '-' + t['hash'].replace('/', '-') + '/'
    mqc_records = [r for r in manifest['files'] if r['path'].startswith(prefix(mqc[0]))]
    commands = [r for r in mqc_records if r['role'] == 'MULTIQC/command']; require(len(commands) == 1, 'Missing actual MultiQC command')
    ctx = context(captured(evidence, commands[0]).read_text())
    configs = [r for r in mqc_records if r['role'].startswith('MULTIQC/config')]
    require(configs and set(ctx['config_names']) <= {Path(r['path']).name for r in configs}, 'Effective config capture is incomplete')
    staged = [r for r in mqc_records if r['role'] == 'MULTIQC/staged_raw']; require(staged, 'No actual staged raw matrices')
    original = []
    for task in producers:
        records = [r for r in manifest['files'] if r['path'].startswith(prefix(task))]
        raws = [r for r in records if r['role'] == RAW]; require(len(raws) == 1, 'Producer raw evidence is missing or ambiguous')
        cmd = [r for r in records if r['role'].endswith('/command')]; require(len(cmd) == 1, 'Missing producer command')
        raw = captured(evidence, raws[0]); labels, _ = raw_columns(raw.read_text()); tokens = invocation(captured(evidence, cmd[0]).read_text(), 'plotFingerprint')
        require('--labels' in tokens, 'Producer labels not explicit in actual command')
        start = tokens.index('--labels') + 1; observed = []
        for token in tokens[start:]:
            if token.startswith('-'): break
            observed.append(token)
        require(labels == observed, 'Raw labels differ from actual producer command')
        original.append((raw.name, raws[0]['sha256'], raws[0]['bytes']))
    require(len(original) == len(set(n for n,_,_ in original)), 'Duplicate producer raw basename')
    require(sorted(original) == sorted((Path(r['path']).name,r['sha256'],r['bytes']) for r in staged), 'Staged inputs differ from actual producer bytes')
    raw = [{'path': str(captured(evidence,r)), 'name': Path(r['path']).name} for r in sorted(staged,key=lambda r:r['path'])]
    require(len({r['name'] for r in raw}) == len(raw), 'Duplicate staged raw filename')
    check_design(case['test_case'], original, raw)
    folder.mkdir(); plan = {'mode':'oracle','context':ctx,'raw':raw}
    oracle = run_worker(folder/'oracle',plan,configs,evidence)
    values = oracle['expected']; require(len(values) == (8 if case['test_case'] == 'controls' else 6), 'Missing or merged scientific roles'); checks = validator(); results = []
    for name, order in [('forward',[r['name'] for r in raw]),('reverse',[r['name'] for r in reversed(raw)])]:
        replay_result = run_worker(folder/name,dict(plan,mode='replay',order=order),configs,evidence)
        report = folder/name/'report'; data = list(report.glob('*_data'))
        require(len(data) == 1, 'Ambiguous replay data directory')
        if data[0].name != 'multiqc_data': (report/'multiqc_data').symlink_to(data[0].name)
        result = checks.validate_report(report,values)
        counts = data[0]/(checks.read_curves(report)[2]+'.txt'); plots = list(data[0].glob('mqc_deeptools_fingerprint_plot*.txt'))
        require(len(plots) == 1, 'Missing numeric plot export')
        validate_plot_table(plots[0], values)
        if case['test_case'] != 'controls':
            require(hashes(plots[0])['md5'] == original_plot_md5(case['test_case']), 'No-control numeric plot differs from original snapshot constraint')
        exports = [counts,plots[0]]
        for export in exports:
            actual = [r for r in mqc_records if Path(r['path']).name == export.name and '/exports/' in r['path']]
            require(len(actual) == 1 and captured(evidence,actual[0]).read_bytes() == export.read_bytes(), 'Actual workflow export differs from qualified replay')
        results.append({'order':name,'semantic':result,'parser_order':replay_result,'exports':[{'name':p.name,**hashes(p)} for p in exports]})
    require(results[0]['exports'] == results[1]['exports'], 'Reversed raw parser order changes exports')
    return {'status':'PASS','test_case':case.get('test_case'),'case_id':case['case_id'],'nextflow_version':case['nextflow_version'],
            'source_commit':manifest['source_commit'],'source_test_file':case['source_test_file'],
            'selection_params':case.get('selection_params'),'params_metadata_sha256':case.get('params_metadata_sha256'),
            'observed_execution_metadata_sha256':case.get('metadata_sha256'),'oracle':oracle,'replays':results,'reporting_tasks':tasks,
            'captured_evidence':[r for r in manifest['files'] if r['path'].startswith(case_prefix)],
            'snapshot_admission':('Only the two exact captured export MD5s above' if case['test_case'] == 'controls' else 'Only counts TSV candidate; numeric plot must retain original snapshot MD5') + '; no snapshot was modified.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--evidence',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--worker',type=Path)
    args=parser.parse_args()
    if args.worker:
        worker(args.worker);return 0
    parser.error('Both --evidence and --output are required') if not args.evidence or not args.output else None
    require(not args.output.is_symlink(), 'Output must not be a symlink')
    require(not args.output.exists() or args.output.is_dir() and not any(args.output.iterdir()), 'Output must be an empty directory')
    args.output.mkdir(parents=True,exist_ok=True);args.output=args.output.resolve();evidence=args.evidence.resolve();result={'schema':'atacseq/native-fingerprint-qualification/v1','qualification_image_ref':IMAGE,'cases':[],'snapshots_modified':False}
    try:
        manifest_path=evidence/'manifest.json';manifest=json.loads(manifest_path.read_text());result['capture_manifest']=hashes(manifest_path);result['nf_test_step_outcome']=manifest.get('nf_test_step_outcome','unknown')
        require(manifest.get('capture_complete') is True, 'Global capture is incomplete')
        check_selection(manifest)
        require(re.fullmatch('[0-9a-f]{40}',manifest.get('source_commit','')), 'Missing actual source commit')
        for name in ['bin/multiqc_fingerprint.py','modules/local/multiqc.nf','conf/modules.config']:
            records=[r for r in manifest['files'] if r['role']=='source/'+name];require(len(records)==1,'Missing executed source evidence');source=captured(evidence,records[0]);require(source.read_bytes()==(REPO/name).read_bytes(),'Captured executed source differs from qualification source')
        result['source_evidence']=[r for r in manifest['files'] if r['role'].startswith('source/')]
        result['not_exercised']=manifest.get('excluded_cases',[])
        for case in manifest.get('cases',[]):
            if case.get('stub_run') is True:result['cases'].append({'case_id':case['case_id'],'status':'NOT_EXERCISED'});continue
            try: record=qualify_case(evidence,manifest,case,args.output/('case-'+case['case_id']))
            except UnknownContext as error:record={'case_id':case['case_id'],'status':'UNKNOWN','reason':str(error)}
            except Exception as error:record={'case_id':case['case_id'],'status':'FAIL','reason':type(error).__name__+': '+str(error)}
            result['cases'].append(record)
        require(any(r['status']=='PASS' for r in result['cases']),'No actual nonstub case qualified')
        result['status']='PASS' if all(r['status'] in {'PASS','NOT_EXERCISED'} for r in result['cases']) else 'FAIL'
    except Exception as error:result.update(status='FAIL',reason=type(error).__name__+': '+str(error))
    (args.output/'RESULT.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');print(json.dumps({'status':result['status'],'result':str(args.output/'RESULT.json')}));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    sys.exit(main())

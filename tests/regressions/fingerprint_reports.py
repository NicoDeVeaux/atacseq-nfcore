#!/usr/bin/env python3
"""Check fingerprint identity and numeric exports using real MultiQC 1.13 runs."""
import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(path, entry):
    require(digest(path) == entry['sha256'], 'Fixture input integrity check failed')
    return path.read_text().splitlines(True)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_curves(output):
    report = json.loads((output / 'multiqc_data/multiqc_data.json').read_text())
    keys = [key for key in report['report_saved_raw_data']
            if key.startswith('deeptools_plot_fingerprint_counts')]
    require(len(keys) == 1, 'Missing or ambiguous fingerprint dataset')
    curves = report['report_saved_raw_data'][keys[0]]
    require(curves and all(points for points in curves.values()), 'Empty fingerprint curves')
    require(all(finite_number(y) for points in curves.values() for y in points.values()),
            'Nonfinite or nonnumeric fingerprint value')
    return report, curves, keys[0]


def validate_table(path, expected):
    rows = list(csv.reader(path.open(), delimiter='\t'))
    require(rows and rows[0][0] == 'Sample', 'Missing fingerprint TSV header')
    header = rows[0]
    require(len(header) == len(set(header)), 'Duplicate fingerprint TSV columns')
    require(set(header[1:]) == {x for points in expected.values() for x in points},
            'Fingerprint TSV X-coordinate set differs')
    require(all(len(row) == len(header) for row in rows[1:]), 'Ragged fingerprint TSV')
    require(len(rows) - 1 == len(expected), 'Fingerprint TSV sample count differs')
    require(len({row[0] for row in rows[1:]}) == len(expected), 'Duplicate fingerprint TSV samples')
    require({row[0] for row in rows[1:]} == set(expected), 'Fingerprint TSV sample identities differ')
    cells = 0
    for row in rows[1:]:
        points = expected[row[0]]
        require(any(row[1:]), 'Blank fingerprint numeric series')
        for x, text in zip(header[1:], row[1:]):
            if x not in points:
                require(text == '', 'Value supplied outside a sample X grid')
                continue
            require(text != '', 'Missing fingerprint numeric value')
            try:
                value = float(text)
            except ValueError as error:
                raise ContractError('Nonnumeric fingerprint TSV value') from error
            require(math.isfinite(value) and value == points[x], 'Fingerprint TSV numeric value differs')
            cells += 1
    return cells


def validate_curves(output, expected):
    report, curves, key = read_curves(output)
    require(curves == expected, 'Parsed sample/curve set differs from the original per-file parser')
    plots = [value for key, value in report['report_plot_data'].items()
             if re.fullmatch(r'deeptools_fingerprint_plot(?:-\d+)?', key)]
    require(len(plots) == 1, 'Missing or ambiguous numeric fingerprint plot')
    plot = plots[0]
    require(len(plot['datasets']) == 1, 'Unexpected fingerprint plot datasets')
    series = plot['datasets'][0]
    require(len(series) == len(expected), 'Fingerprint plot sample count differs')
    plotted = {}
    for sample in series:
        name = sample['name']
        require(name not in plotted, 'Duplicate fingerprint plot sample')
        points = {}
        for x, y in sample['data']:
            require(finite_number(x) and finite_number(y), 'Nonnumeric fingerprint plot point')
            require(str(x) not in points, 'Duplicate fingerprint plot X coordinate')
            points[str(x)] = y
        plotted[name] = points
    require(plotted == expected, 'Numeric plot differs from the original per-file parser')
    return key


def validate_report(output, expected):
    key = validate_curves(output, expected)
    table = output / ('multiqc_data/' + key + '.txt')
    cells = validate_table(table, expected)
    return {'samples': len(expected), 'numeric_cells': cells, 'table_sha256': digest(table),
            'table': str(table), 'curve_sha256': hashlib.sha256(
                json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def reject(operation, label):
    try:
        operation()
    except ContractError as error:
        return {'control': label, 'rejected': True, 'reason': str(error)}
    raise ContractError('Negative control was accepted: ' + label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--labels-json', required=True, type=Path)
    parser.add_argument('--baseline', action='store_true', help='Expect the original CLI to satisfy the contract; exits nonzero on the known defect')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True, exist_ok=False)
    result = {'status': 'RUNNING', 'executions': [], 'negative_controls': [], 'baseline': args.baseline}
    try:
        import multiqc
        require(multiqc.__version__ == '1.13', 'Run this fixture with the pinned MultiQC 1.13 image')
        fixtures = Path(__file__).with_name('fingerprint_fixtures')
        manifest = json.loads((fixtures / 'MANIFEST.json').read_text())
        from multiqc.modules.deeptools import plotFingerprint
        require(digest(Path(plotFingerprint.__file__)) == manifest['parser_source_sha256'],
                'The pinned original parser source changed')
        labels = json.loads(args.labels_json.read_text())
        require(labels['source_sha256'] == digest(repo / 'conf/modules.config'), 'Label probe source is stale')
        probe = Path(__file__).with_name('fingerprint_labels.nf')
        require(labels['probe_source_sha256'] == digest(probe), 'Label probe implementation is stale')
        config = repo / 'assets/multiqc_config.yml'
        checker = repo / 'bin/check_samplesheet.py'
        pinned = [Path(__file__), probe, checker, repo / 'bin/multiqc_fingerprint.py', repo / 'conf/modules.config',
                  config, args.labels_json, fixtures / 'MANIFEST.json']
        pinned += [fixtures / row['fixture'] for row in manifest['fixtures']]
        result['inputs_before'] = {str(path): digest(path) for path in pinned}
        header = 'sample,fastq_1,fastq_2,replicate,control,control_replicate\n'
        metadata = ('CASE_A,a_1.fastq.gz,a_2.fastq.gz,1,CONTROL,1\n'
                    'CASE_A_REP1__control_CONTROL,b_1.fastq.gz,b_2.fastq.gz,1,CONTROL,1\n'
                    'CONTROL,c_1.fastq.gz,c_2.fastq.gz,1,,\n')
        for case, text in [('legal_namespace', metadata), ('invalid_colon', metadata.replace('CASE_A,', 'CASE::A,', 1))]:
            source = args.output / (case + '.csv')
            source.write_text(header + text)
            destination = args.output / case / 'validated.csv'
            command = [sys.executable, str(checker), str(source), str(destination), '--with_control']
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (args.output / (case + '.stdout.log')).write_bytes(completed.stdout)
            (args.output / (case + '.stderr.log')).write_bytes(completed.stderr)
            result['executions'].append({'case': case, 'command': command, 'exit': completed.returncode})
            if case == 'legal_namespace':
                require(completed.returncode == 0 and destination.is_file(), 'Real samplesheet checker rejected the legal namespace witness')
            else:
                require(completed.returncode != 0 and b'Sample name contains invalid characters!' in completed.stdout,
                        'Real samplesheet checker did not reject the colon namespace')
                result['negative_controls'].append({'control': 'invalid_colon_sample_id', 'rejected': True})
        records = []
        for entry in manifest['fixtures']:
            source = fixtures / entry['fixture']
            key = entry['fixture'].split('.')[0]
            pair = labels['comparisons'][key]['labels']
            require(len(pair) == 2 and all(not any(c in s for c in "\t\n'") for s in pair), 'Invalid source-produced labels')
            lines = load_fixture(source, entry)
            lines[1] = "'" + pair[0] + "'\t'" + pair[1] + "'\n"
            target = args.output / entry['fixture']
            target.write_text(''.join(lines))
            records.append(target)
        require(len(set(label for row in labels['comparisons'].values() for label in row['labels'])) == 4,
                'Control identities collide between comparisons')

        def run(name, inputs, wrapped, data_format='tsv'):
            root = args.output / name
            root.mkdir()
            output = root / 'output'
            executable = [sys.executable, str(repo / 'bin/multiqc_fingerprint.py')] if wrapped else [shutil.which('multiqc')]
            command = executable + ['--force', '--module', 'deeptools', '--config', str(config),
                                    '--outdir', str(output)]
            if data_format is not None:
                command += ['--data-format', data_format]
            command += [str(p) for p in inputs]
            environment = dict(os.environ, HOME=str(root / 'home'))
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
            (root / 'stdout.log').write_bytes(completed.stdout)
            (root / 'stderr.log').write_bytes(completed.stderr)
            result['executions'].append({'case': name, 'command': command, 'exit': completed.returncode})
            require(completed.returncode == 0, 'MultiQC execution failed: ' + name)
            return output

        expected = {}
        for index, source in enumerate(records):
            _, curves, _ = read_curves(run('original_parser_' + str(index), [source], False))
            require(not set(expected) & set(curves), 'The original parser cleaned comparison labels into a collision')
            expected.update(curves)
        require(len(expected) == 4, 'Original parser did not retain all comparison curves')
        original = run('original_cli_combined', records, False)
        if args.baseline:
            result['validated'] = validate_report(original, expected)
        else:
            result['negative_controls'].append(reject(lambda: validate_report(original, expected), 'original_CLI_numeric_export'))
            forward = run('proposal_forward', records, True)
            reverse = run('proposal_reverse', list(reversed(records)), True)
            result['forward'] = validate_report(forward, expected)
            result['reverse'] = validate_report(reverse, expected)
            require(result['forward']['table_sha256'] == result['reverse']['table_sha256'], 'Reversed file order changes the numeric table')
            require(result['forward']['curve_sha256'] == result['reverse']['curve_sha256'], 'Reversed file order changes the curves')
            original_plots = {p.name: digest(p) for p in (original / 'multiqc_data').glob('mqc_deeptools_fingerprint_plot*.txt')}
            proposed_plots = {p.name: digest(p) for p in (forward / 'multiqc_data').glob('mqc_deeptools_fingerprint_plot*.txt')}
            require(original_plots and original_plots == proposed_plots, 'Wrapper changes original numerical plot-export bytes')
            result['original_numeric_plot_bytes_preserved'] = True
            legacy = []
            for source in records:
                lines = source.read_text().splitlines(True)
                first = lines[1].split('\t')[0]
                lines[1] = first + "\t'CONTROL_SHARED.mLb.clN'\n"
                target = args.output / ('legacy_' + source.name)
                target.write_text(''.join(lines)); legacy.append(target)
            legacy_output = run('legacy_labels', legacy, True)
            result['negative_controls'].append(reject(lambda: validate_report(legacy_output, expected), 'legacy_shared_control_labels'))

            table = Path(result['forward']['table'])
            rows = list(csv.reader(table.open(), delimiter='\t'))
            mutants = {}
            blank = copy.deepcopy(rows); blank[1][1:] = [''] * (len(blank[1]) - 1); mutants['blank_numeric_series'] = blank
            duplicate = copy.deepcopy(rows); duplicate[0][2] = duplicate[0][1]; mutants['duplicate_X_column'] = duplicate
            numeric = copy.deepcopy(rows)
            at = next(i for i, text in enumerate(numeric[1][1:], 1) if text)
            numeric[1][at] = str(float(numeric[1][at]) + 1); mutants['changed_numeric_value'] = numeric
            mutants['missing_sample'] = [rows[0]] + rows[2:]
            for name, content in mutants.items():
                target = args.output / (name + '.tsv')
                with target.open('w', newline='') as handle:
                    csv.writer(handle, delimiter='\t', lineterminator='\n').writerows(content)
                result['negative_controls'].append(reject(lambda p=target: validate_table(p, expected), name))
            corrupt = args.output / 'corrupt_input.raw.txt'
            corrupt.write_bytes((fixtures / manifest['fixtures'][0]['fixture']).read_bytes() + b'1\t1\n')
            result['negative_controls'].append(reject(lambda: load_fixture(corrupt, manifest['fixtures'][0]), 'corrupt_input'))

            wrapper = repo / 'bin/multiqc_fingerprint.py'
            spec = importlib.util.spec_from_file_location('production_fingerprint_wrapper', wrapper)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            original_data = {'SAMPLE': {0.0: 0.25, 0.5: 0.75}}
            frozen = copy.deepcopy(original_data)
            normalized = module.normalize_numeric_keys(original_data)
            require(original_data == frozen and list(original_data['SAMPLE']) == [0.0, 0.5], 'Wrapper mutated the parser mapping')
            require(normalized == {'SAMPLE': {'0.0': 0.25, '0.5': 0.75}}, 'Wrapper changed numeric payload')
            try:
                module.normalize_numeric_keys({'SAMPLE': {0.5: 1, '0.5': 2}})
            except ValueError:
                result['negative_controls'].append({'control': 'numeric_key_string_collision', 'rejected': True})
            else:
                raise ContractError('Wrapper accepted a numeric-key/string-key collision')
            result['configured_formats'] = []
            for data_format in [None, 'yaml', 'json']:
                case = data_format or 'actual_assets_no_override'
                old_output = run(case + '_original', records, False, data_format)
                new_output = run(case + '_wrapped', records, True, data_format)
                key = validate_curves(old_output, expected)
                validate_curves(new_output, expected)
                old_files = list((old_output / 'multiqc_data').glob(key + '.*'))
                new_files = list((new_output / 'multiqc_data').glob(key + '.*'))
                require(len(old_files) == len(new_files) == 1, 'Ambiguous configured-format export')
                old_file, new_file = old_files[0], new_files[0]
                require(old_file.suffix == new_file.suffix, 'Wrapper changes the configured export format')
                if old_file.suffix == '.txt':
                    validate_table(new_file, expected)
                else:
                    require(old_file.read_bytes() == new_file.read_bytes(), 'Wrapper changes JSON/YAML export bytes')
                result['configured_formats'].append({'requested': data_format,
                    'effective_extension': new_file.suffix, 'original_sha256': digest(old_file),
                    'wrapped_sha256': digest(new_file), 'numeric_curves_preserved': True})
        result['inputs_after'] = {str(path): digest(path) for path in pinned}
        require(result['inputs_before'] == result['inputs_after'], 'A pinned source or fixture changed during execution')
        result['status'] = 'PASS'
        result['inputs'] = {str(p): digest(p) for p in [args.labels_json, config, fixtures / 'MANIFEST.json']}
        result['limits'] = 'Small report/parser integration only; no BAM processing or claim about missing historical CI raw files.'
    except Exception as error:
        result.update(status='FAIL', error=type(error).__name__ + ': ' + str(error))
    (args.output / 'RESULT.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': result['status'], 'result': str(args.output / 'RESULT.json'),
                      'negative_controls': len(result['negative_controls']), 'error': result.get('error')}))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())

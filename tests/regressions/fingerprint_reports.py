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
from unittest.mock import patch

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


def validate_input_order(output, inputs):
    report = json.loads((output / 'multiqc_data/multiqc_data.json').read_text())
    sources = [sections['plotFingerprint'] for sections in report['report_data_sources'].values()
               if 'plotFingerprint' in sections]
    require(len(sources) == 1, 'Missing or ambiguous fingerprint parser sources')
    observed = [str(Path(path).resolve()) for path in sources[0].values()]
    requested = [str(path.resolve()) for path in inputs]
    require(len(set(observed)) == len(observed), 'Repeated fingerprint parser source')
    require(observed == requested, 'Actual fingerprint parser input order differs from requested order')
    return observed


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
    plots = list((output / 'multiqc_data').glob('mqc_deeptools_fingerprint_plot*.txt'))
    require(len(plots) == 1, 'Missing or ambiguous fingerprint numeric plot export')
    return {'samples': len(expected), 'numeric_cells': cells, 'table_sha256': digest(table),
            'table': str(table), 'plot_sha256': digest(plots[0]), 'curve_sha256': hashlib.sha256(
                json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def reject(operation, label):
    try:
        operation()
    except ContractError as error:
        return {'control': label, 'rejected': True, 'reason': str(error)}
    raise ContractError('Negative control was accepted: ' + label)


def control_identity(label):
    require(label.startswith('control::') and label.endswith('.mLb.clN'),
            'Missing generated comparison-control namespace')
    return label[:-len('.mLb.clN')]


def validate_cleaning_scope(module):
    from multiqc import config
    from multiqc.modules.base_module import BaseMultiqcModule
    from multiqc.utils import util_functions
    original = BaseMultiqcModule.clean_s_name
    original_writer = util_functions.write_data_file
    options = ['fn_clean_sample_names', 'use_filename_as_sample_name', 'prepend_dirs', 'prepend_dirs_sep', 'prepend_dirs_depth',
               'sample_names_replace', 'sample_names_replace_exact', 'sample_names_replace_regex',
               'sample_names_replace_complete']
    frozen = {key: getattr(config, key) for key in options}
    instance = object.__new__(BaseMultiqcModule)
    instance.anchor = 'deeptools'
    label = 'control::CASE.mLb.clN_REP1::CONTROL_REP1.mLb.clN'
    context = {'sp_key': 'deeptools/plotFingerprintOutRawCounts', 'root': 'synthetic/dir'}
    observed = {}

    def exercise():
        config.fn_clean_sample_names = True
        clean = instance.clean_s_name
        require(clean(label, context) == control_identity(label), 'Generated control metadata was truncated')
        for value, metadata in [(label, {'sp_key': 'another/module'}),
                                ('mLb_A_REP1.mLb.clN', context),
                                ('control_CASE.mLb.clN', context),
                                ('control::CASE.mLb.clN_REP1', context)]:
            require(clean(value, metadata) == original(instance, value, metadata),
                    'Unrelated or nongenerated name cleaning changed')
        config.fn_clean_sample_names = False
        require(clean(label, context) == original(instance, label, context) == label,
                'Explicit full-name choice changed')
        config.fn_clean_sample_names = True
        filename_context = {**context, 'fn': 'raw_input.fastq.gz'}
        for choice in [True, [context['sp_key']]]:
            config.use_filename_as_sample_name = choice
            expected = original(instance, label, filename_context)
            require(expected != filename_context['fn'], 'Original filename-cleanup control is not active')
            require(clean(label, filename_context) == expected, 'Filename override cleanup changed')
        config.use_filename_as_sample_name = True
        require(clean(label, filename_context, filename='override.fastq.gz')
                == original(instance, label, filename_context, filename='override.fastq.gz'),
                'Explicit filename override cleanup changed')
        require(clean(label, context) == control_identity(label),
                'Filename preference without an effective filename bypassed identity protection')
        config.use_filename_as_sample_name = ['another/module']
        require(clean(label, filename_context) == control_identity(label),
                'Nonmatching filename preference bypassed identity protection')
        config.use_filename_as_sample_name = [context['sp_key']]
        require(clean(label, filename_context, seach_pattern_key='another/module') == control_identity(label),
                'Explicit nonmatching search key bypassed identity protection')
        config.use_filename_as_sample_name = False
        config.sample_names_replace = {control_identity(label): 'RENAMED_CONTROL'}
        config.sample_names_replace_exact = True
        config.sample_names_replace_regex = False
        config.sample_names_replace_complete = True
        require(clean(label, context) == 'RENAMED_CONTROL', 'Custom sample-name replacement was bypassed')
        config.sample_names_replace = {}
        config.prepend_dirs = True
        config.prepend_dirs_depth = 0
        config.prepend_dirs_sep = '|'
        require(clean(label, context) == 'synthetic|dir|' + control_identity(label),
                'Directory-prefix choice changed')
        try:
            clean(label, {**context, 'root': object()})
        except AttributeError:
            pass
        else:
            raise ContractError('Exceptional original cleaner control did not execute')
        require(config.fn_clean_sample_names is True, 'Cleaner option was not restored after an exception')
        observed.update(unrelated_names_unchanged=True, full_names_preserved=True,
                        filename_override_choices_preserved=True,
                        custom_renaming_preserved=True, directory_prefix_preserved=True,
                        cleaning_option_restored_after_exception=True)

    try:
        with patch('multiqc.__main__.run_multiqc', exercise):
            module.main()
    finally:
        BaseMultiqcModule.clean_s_name = original
        util_functions.write_data_file = original_writer
        for key, value in frozen.items():
            setattr(config, key, value)
    return observed


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
        cleaning = labels['cleaning_case']
        require(cleaning['treatment'] == 'CASE.mLb.clN_REP1' and cleaning['control'] == 'CONTROL_REP1'
                and len(cleaning['labels']) == 2, 'Missing cleaning-sensitive producer witness')
        empty_cleaning = labels['empty_cleaning_cases']
        require([(row['treatment'], row['control']) for row in empty_cleaning]
                == [('mLb_A_REP1', 'CONTROL_A_REP1'), ('mLb_B_REP1', 'CONTROL_B_REP1')]
                and all(len(row['labels']) == 2 for row in empty_cleaning),
                'Missing distinct-control empty-cleaning producer witnesses')
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
                    'CASE.mLb.clN,d_1.fastq.gz,d_2.fastq.gz,1,CONTROL,1\n'
                    'mLb_A,e_1.fastq.gz,e_2.fastq.gz,1,CONTROL_A,1\n'
                    'mLb_B,f_1.fastq.gz,f_2.fastq.gz,1,CONTROL_B,1\n'
                    'CONTROL_A,g_1.fastq.gz,g_2.fastq.gz,1,,\n'
                    'CONTROL_B,h_1.fastq.gz,h_2.fastq.gz,1,,\n'
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
                with destination.open() as stream:
                    validated = list(csv.DictReader(stream))
                require(any(row['sample'] == cleaning['treatment'] + '_T1'
                            and row['control'] == cleaning['control'] for row in validated),
                        'Cleaning-sensitive metadata does not follow the real samplesheet validator')
                require(all(any(row['sample'] == case['treatment'] + '_T1'
                                and row['control'] == case['control'] for row in validated)
                            for case in empty_cleaning),
                        'Distinct-control metadata does not follow the real samplesheet validator')
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
            result['executions'][-1]['actual_parser_input_order'] = validate_input_order(output, inputs)
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
            require(result['forward']['plot_sha256'] == result['reverse']['plot_sha256'], 'Reversed file order changes numeric plot-export bytes')
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

            def cleaning_input(name, pair, entry=None):
                require(len(pair) == 2 and all(not any(c in s for c in "\t\n'") for s in pair),
                        'Invalid cleaning-sensitive labels')
                entry = entry or manifest['fixtures'][0]
                lines = load_fixture(fixtures / entry['fixture'], entry)
                lines[1] = "'" + pair[0] + "'\t'" + pair[1] + "'\n"
                target = args.output / (name + '.plotFingerprint.raw.txt')
                target.write_text(''.join(lines))
                return target

            role_expected = {}
            role_oracles = []
            for role, index, neutral in [('treatment', 0, 'ORACLE_OTHER_CONTROL'),
                                          ('control', 1, 'ORACLE_OTHER_TREATMENT')]:
                pair = list(cleaning['labels'])
                pair[1 - index] = neutral
                source = cleaning_input('cleaning_original_' + role, pair)
                oracle = run('cleaning_original_' + role, [source], False)
                _, curves, _ = read_curves(oracle)
                require(len(curves) == 2 and neutral in curves, 'Original per-role oracle lost a curve')
                selected = {name: points for name, points in curves.items() if name != neutral}
                require(len(selected) == 1, 'Original per-role oracle is ambiguous')
                if role == 'control':
                    selected = {control_identity(cleaning['labels'][1]): next(iter(selected.values()))}
                require(not set(role_expected) & set(selected),
                        'MultiQC cleaning merges the treatment and comparison-control identities')
                role_expected.update(selected)
                role_oracles.append({'role': role, 'output': str(oracle), 'cleaned_names': list(selected)})
            require(len(role_expected) == 2, 'Cleaning-sensitive witness needs both original role curves')
            source = cleaning_input('cleaning_proposal', cleaning['labels'])
            positive = run('cleaning_proposal', [source], True)
            result['cleaning_sensitive_id'] = {
                'supported_sample': 'CASE.mLb.clN', 'metadata': cleaning, 'original_per_role_oracles': role_oracles,
                'validated': validate_report(positive, role_expected),
                'scope': 'Prevents new within-comparison loss; inherited aliases between different cleaned treatment IDs are outside this witness.'}
            previous = [cleaning['labels'][0],
                        cleaning['treatment'] + '::control::' + cleaning['control'] + '.mLb.clN']
            source = cleaning_input('cleaning_previous_prefix', previous)
            mutant = run('cleaning_previous_prefix', [source], False)
            _, mutant_curves, _ = read_curves(mutant)
            require(len(mutant_curves) == 1, 'Previous treatment-prefix cleaner did not reproduce curve loss')
            result['negative_controls'].append({**reject(
                lambda: validate_curves(mutant, role_expected), 'treatment_prefix_cleaning_collision'),
                'observed_curves': len(mutant_curves), 'required_curves': 2})

            empty_expected, empty_inputs, aliases = {}, [], []
            for index, case in enumerate(empty_cleaning):
                source = cleaning_input('empty_cleaning_' + str(index), case['labels'], manifest['fixtures'][index])
                empty_inputs.append(source)
                oracle = run('empty_cleaning_original_' + str(index), [source], False)
                _, curves, _ = read_curves(oracle)
                treatment = case['labels'][0]
                require(len(curves) == 2 and treatment in curves,
                        'Original empty-name fallback did not retain its treatment curve')
                control = next(name for name in curves if name != treatment)
                aliases.append(control)
                empty_expected[treatment] = curves[treatment]
                empty_expected[control_identity(case['labels'][1])] = curves[control]
            require(len(empty_expected) == 4 and len(set(aliases)) == 1,
                    'Distinct-control witness does not exercise original control-name collision')
            positive = run('empty_cleaning_forward', empty_inputs, True)
            reverse = run('empty_cleaning_reverse', list(reversed(empty_inputs)), True)
            forward_values = validate_report(positive, empty_expected)
            reverse_values = validate_report(reverse, empty_expected)
            require(forward_values['table_sha256'] == reverse_values['table_sha256'],
                    'Distinct-control numeric table changes with input order')
            require(forward_values['plot_sha256'] == reverse_values['plot_sha256'],
                    'Distinct-control numeric plot changes with input order')
            result['empty_cleaning_distinct_controls'] = {
                'metadata': empty_cleaning, 'forward': forward_values, 'reverse': reverse_values}
            mutant = run('empty_cleaning_unprotected', empty_inputs, False)
            _, mutant_curves, _ = read_curves(mutant)
            require(len(mutant_curves) == 3, 'Unprotected cleaner did not reproduce distinct-control curve loss')
            result['negative_controls'].append({**reject(
                lambda: validate_curves(mutant, empty_expected), 'unprotected_generated_control_cleanup'),
                'observed_curves': len(mutant_curves), 'required_curves': 4})

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
            result['name_cleaning_scope'] = validate_cleaning_scope(module)
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

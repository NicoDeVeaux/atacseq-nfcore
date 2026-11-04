import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capture_controls_evidence as collector


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        self.home = self.base / 'home'
        self.home.mkdir()
        (self.workspace / 'main.nf').write_text('// fixture\n')
        (self.workspace / 'test.tap').write_text('not ok 1 -profile test_controls\n')
        for name in collector.SOURCE_FILES:
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture source\n')
        self.case, self.fingerprint, self.multiqc = self.make_case(self.workspace / '~' / 'tests', 'a' * 32)

    def make_case(self, root, case_id, profiles='test,test_controls,docker', params=None, stub=False):
        case = root / case_id
        (case / 'meta').mkdir(parents=True)
        fingerprint = case / 'work/12' / ('abcdef' + '0' * 24)
        multiqc = case / 'work/34' / ('123abc' + '0' * 24)
        fingerprint.mkdir(parents=True)
        (multiqc / 'deeptools').mkdir(parents=True)
        (multiqc / 'multiqc_data').mkdir()
        for directory in (fingerprint, multiqc):
            (directory / '.command.sh').write_text('#!/bin/bash\ntrue\n')
            (directory / '.command.run').write_text('DO_NOT_ARCHIVE_LAUNCHER\n')
            (directory / '.command.env').write_text('DO_NOT_ARCHIVE_ENVIRONMENT\n')
        (multiqc / '.command.sh').write_text('#!/bin/bash\nmultiqc_fingerprint.py -f .\n')
        raw = fingerprint / 'sample__vs__control.plotFingerprint.raw.txt'
        raw.write_text('# sample\tcontrol\n1\t2\n')
        (multiqc / 'deeptools' / raw.name).symlink_to(raw)
        (multiqc / 'multiqc_config.yml').symlink_to(self.workspace / 'assets/multiqc_config.yml')
        (multiqc / collector.VERSIONS).write_text('Workflow:\n  Nextflow: 25.10.4\nMultiQC:\n  multiqc: 1.13\n')
        for name in collector.EXPORTS:
            # A broken blank export is valuable evidence too, not discarded by capture.
            (multiqc / 'multiqc_data' / name).write_text('Sample\t0.5\nsample\n')
        (case / 'meta/params.json').write_text(json.dumps(params or {}) + '\n')
        (case / 'meta/nextflow.log').write_text(
            f'DEBUG nextflow.cli.Launcher - $> nextflow run {self.workspace}/main.nf -profile {profiles} '
            f'-params-file {case}/meta/params.json' + (' -stub' if stub else '') + '\n'
            'INFO nextflow.cli.CmdRun - N E X T F L O W  ~  version 25.10.4\n')
        (case / 'meta/trace.csv').write_text(
            'task_id\thash\tname\tstatus\texit\tcontainer\n'
            '1\t12/abcdef\tNFCORE_ATACSEQ:ATACSEQ:MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT (comparison)\tCOMPLETED\t0\tquay.io/biocontainers/deeptools:3.5.6\n'
            '2\t34/123abc\tNFCORE_ATACSEQ:ATACSEQ:MULTIQC\tFAILED\t1\tbiocontainers/multiqc:1.13\n')
        return case, fingerprint, multiqc

    def collect(self, **kwargs):
        output = self.base / 'artifact'
        result = collector.collect(self.workspace, '~', output, 'a' * 40, home=self.home, **kwargs)
        self.assertEqual(json.loads((output / 'manifest.json').read_text()), result)
        return result, output

    def test_literal_tilde_failed_case_captures_exact_allowlist_and_hashes(self):
        result, output = self.collect()
        self.assertTrue(result['complete'])
        self.assertEqual(result['cases'][0]['nextflow_version'], '25.10.4')
        self.assertEqual(result['cases'][0]['tasks'][1]['status'], 'FAILED')
        self.assertEqual(result['cases'][0]['root_kind'], 'literal')
        self.assertTrue(any(f['was_symlink'] and f['role'] == 'MULTIQC/staged_raw' for f in result['files']))
        for row in result['files']:
            data = (output / row['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
            self.assertNotIn(b'DO_NOT_ARCHIVE', data)
        self.assertEqual(sum('/exports/' in f['path'] for f in result['files']), 2)

    def test_expanded_home_root_is_also_discovered(self):
        self.make_case(self.home / 'tests', 'b' * 32)
        result, _ = self.collect()
        self.assertEqual({c['root_kind'] for c in result['cases']}, {'literal', 'expanded_home'})

    def test_nf_test_failure_and_capture_completeness_are_separate(self):
        result, _ = self.collect(nf_test_outcome='failure')
        self.assertTrue(result['capture_complete'])
        self.assertEqual(result['nf_test_step_outcome'], 'failure')
        self.assertEqual(result['controls_tap']['statuses'], [{'number': 1, 'status': 'failed'}])

    def test_external_raw_symlink_is_rejected_before_read(self):
        raw = next(self.fingerprint.glob('*.raw.txt'))
        raw.unlink()
        outside = self.base / raw.name
        outside.write_text('OUTSIDE_SENTINEL\n')
        raw.symlink_to(outside)
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path.resolve(), outside.resolve(), 'An outside target was opened')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            result, _ = self.collect()
        self.assertFalse(result['complete'])
        self.assertIn('outside_allowed_boundary', [i['reason'] for i in result['issues']])

    def test_same_prefix_sibling_path_is_outside_boundary(self):
        directory = self.base / 'workspace-other'
        directory.mkdir()
        target = directory / 'multiqc_config.yml'
        target.write_text('OUTSIDE_SENTINEL\n')
        source = self.workspace / 'assets/multiqc_config.yml'
        source.unlink(); source.symlink_to(target)
        result, _ = self.collect()
        self.assertTrue(any(i['role'] == 'source/assets/multiqc_config.yml' and i['reason'] == 'outside_allowed_boundary' for i in result['issues']))

    def test_missing_export_keeps_other_evidence_and_marks_partial(self):
        (self.multiqc / 'multiqc_data' / collector.EXPORTS[0]).unlink()
        result, _ = self.collect()
        self.assertFalse(result['complete'])
        self.assertEqual(sum('/exports/' in f['path'] for f in result['files']), 1)
        self.assertTrue(any(f['role'].endswith('/command') for f in result['files']))

    def test_prefixed_data_directory_and_published_versions_are_supported(self):
        (self.multiqc / 'multiqc_data').rename(self.multiqc / 'controls_multiqc_data')
        versions = self.multiqc / collector.VERSIONS
        published = self.case / 'output/pipeline_info' / collector.VERSIONS
        published.parent.mkdir(parents=True)
        versions.rename(published)
        versions.symlink_to(published)
        result, _ = self.collect()
        self.assertTrue(result['complete'])
        self.assertEqual(sum('/exports/' in f['path'] for f in result['files']), 2)
        self.assertTrue(any(f['role'] == 'MULTIQC/observed_tool_versions' and f['was_symlink'] for f in result['files']))

    def test_supported_leading_underscore_preserves_both_raw_copies(self):
        raw = next(self.fingerprint.glob('*.plotFingerprint.raw.txt'))
        staged = self.multiqc / 'deeptools' / raw.name
        staged.unlink()
        renamed = raw.with_name('_CASE_REP1.mLb.clN.plotFingerprint.raw.txt')
        raw.rename(renamed)
        (staged.parent / renamed.name).symlink_to(renamed)
        result, _ = self.collect()
        self.assertTrue(result['capture_complete'])
        copies = [f for f in result['files'] if f['role'] in {
            'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT/raw', 'MULTIQC/staged_raw'}]
        self.assertEqual(len(copies), 2)
        self.assertTrue(all(Path(f['path']).name == renamed.name for f in copies))

    def test_ambiguous_short_task_hash_never_uses_first_match(self):
        (self.case / 'work/12' / ('abcdef' + '1' * 24)).mkdir()
        result, _ = self.collect()
        self.assertFalse(result['complete'])
        self.assertFalse(any(f['role'] == 'MERGED_LIBRARY_DEEPTOOLS_PLOTFINGERPRINT/raw' for f in result['files']))

    def test_capability_is_omitted_without_persisting_its_value(self):
        (self.multiqc / '.command.sh').write_text('curl https://example.invalid/wt/SYNTHETIC_CAPABILITY\n')
        result, output = self.collect()
        self.assertIn('credential_or_capability_text_omitted', [i['reason'] for i in result['issues']])
        self.assertFalse(any(b'SYNTHETIC_CAPABILITY' in p.read_bytes() for p in output.rglob('*') if p.is_file()))

    def test_stale_cases_are_not_archived(self):
        log = self.case / 'meta/nextflow.log'
        result, _ = self.collect(since=log.stat().st_mtime_ns + 1)
        self.assertFalse(result['cases'])
        self.assertFalse(result['files'])
        self.assertIn('selected_controls_case_not_identified', [i['reason'] for i in result['issues']])

    def test_other_profiles_are_not_archived(self):
        log = self.case / 'meta/nextflow.log'
        log.write_text(log.read_text().replace('test,test_controls,docker', 'test,docker'))
        (self.workspace / 'test.tap').write_text('ok 1 -profile test\n')
        result, _ = self.collect()
        self.assertEqual(result['status'], 'not_applicable')
        self.assertFalse(result['cases'])
        self.assertFalse(result['files'])

    def test_environment_and_launch_wrapper_are_never_opened(self):
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotIn(path.name, {'.command.env', '.command.run'})
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            result, _ = self.collect()
        self.assertTrue(result['complete'])

    def test_capability_in_container_metadata_is_not_persisted(self):
        trace = self.case / 'meta/trace.csv'
        trace.write_text(trace.read_text().replace('biocontainers/multiqc:1.13', 'https://example.invalid/ephemeral/SYNTHETIC_CAPABILITY'))
        result, output = self.collect()
        self.assertIsNone(result['cases'][0]['tasks'][1]['container_from_trace'])
        self.assertFalse(any(b'SYNTHETIC_CAPABILITY' in p.read_bytes() for p in output.rglob('*') if p.is_file()))

    def test_opt_in_identifies_all_seven_cases_from_launch_and_selectors(self):
        selectors = {'default': {}, 'controls': {}, 'bowtie2': {'aligner': 'bowtie2'},
                     'chromap': {'aligner': 'chromap', 'seq_center': 'Broad Institute'},
                     'star': {'aligner': 'star'}, 'skip_trimming': {'skip_trimming': True},
                     'skip_consensus_peaks': {'skip_consensus_peaks': True}}
        for index, (name, params) in enumerate(selectors.items(), 1):
            if name == 'controls':
                continue
            self.make_case(self.workspace / '~' / 'tests', format(index, '032x'), 'test,docker', params)
            source = self.workspace / collector.TEST_FILES[name]
            source.write_text('// actual test source ' + name + '\n')
        (self.workspace / 'test.tap').write_text(''.join(
            f'ok {index} {collector.TAP_NAMES[name]}\n' for index, name in enumerate(selectors, 1)))
        result, output = self.collect(include_non_controls=True)
        self.assertTrue(result['capture_complete'], result['issues'])
        self.assertEqual({case['test_case'] for case in result['cases']}, set(selectors))
        for case in result['cases']:
            self.assertFalse(case['stub_run'])
            self.assertEqual(case['selection_params']['aligner'], selectors[case['test_case']].get('aligner', 'bwa'))
            self.assertIsNotNone(case['params_metadata_sha256'])
            role = 'source/' + collector.TEST_FILES[case['test_case']]
            self.assertTrue(any(row['role'] == role for row in result['files']))
        self.assertFalse(any(p.name in {'.command.env', '.command.run'} for p in output.rglob('*')))

    def test_selected_file_cross_checks_metadata_and_tap(self):
        log = self.case / 'meta/nextflow.log'
        log.write_text(log.read_text().replace('test,test_controls,docker', 'test,docker'))
        (self.case / 'meta/params.json').write_text('{"aligner":"star"}\n')
        (self.workspace / 'tests/star.nf.test').write_text('// star source\n')
        (self.workspace / 'test.tap').write_text(
            'not ok 1 Test pipeline with STAR aligner: star\n'
            'ok 2 Test pipeline with STAR aligner: star with stub\n')
        result, _ = self.collect(include_non_controls=True, test_file='star.nf.test')
        self.assertTrue(result['capture_complete'], result['issues'])
        self.assertEqual(result['requested']['test_file'], 'tests/star.nf.test')
        self.assertEqual(result['expected_test_case'], 'star')
        self.assertEqual(result['profile_tap']['star']['matched_tests'], 1)
        self.assertEqual(result['profile_tap']['star']['statuses'], [{'number': 1, 'status': 'failed'}])

    def test_expected_real_test_requires_exactly_one_tap_binding(self):
        (self.workspace / 'test.tap').write_text('ok 1 Test pipeline: -profile test_controls with stub\n')
        result, _ = self.collect(include_non_controls=True, test_file='tests/controls.nf.test')
        self.assertFalse(result['capture_complete'])
        self.assertIn('expected_real_test_tap_count', [issue['reason'] for issue in result['issues']])

    def test_duplicate_real_tap_binding_is_not_credited(self):
        (self.workspace / 'test.tap').write_text(
            'ok 1 Test pipeline: -profile test_controls\nok 2 Test pipeline: -profile test_controls\n')
        result, _ = self.collect(include_non_controls=True, test_file='tests/controls.nf.test')
        self.assertFalse(result['capture_complete'])
        self.assertIn('expected_real_test_tap_count', [issue['reason'] for issue in result['issues']])

    def test_requested_profile_mismatch_does_not_capture_payload(self):
        result, _ = self.collect(include_non_controls=True, test_file='tests/chromap.nf.test')
        self.assertFalse(result['capture_complete'])
        self.assertFalse(result['cases'])
        self.assertFalse(result['files'])
        self.assertIn('requested_test_case_mismatch', [i['reason'] for i in result['issues']])

    def test_stub_with_complete_looking_outputs_is_excluded_without_payload_read(self):
        log = self.case / 'meta/nextflow.log'
        log.write_text(log.read_text().replace('-profile test,test_controls,docker', '-stub-run -profile test,test_controls,docker'))
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertFalse(path.is_relative_to(self.case / 'work'), 'A stub payload was opened')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            result, _ = self.collect(include_non_controls=True, test_file='tests/controls.nf.test')
        self.assertFalse(result['capture_complete'])
        self.assertFalse(result['cases'])
        self.assertFalse(result['files'])
        self.assertTrue(result['excluded_cases'][0]['stub_run'])

    def test_real_and_stub_in_one_test_file_only_credit_real_case(self):
        self.make_case(self.workspace / '~' / 'tests', 'b' * 32, stub=True)
        result, _ = self.collect(include_non_controls=True, test_file='tests/controls.nf.test')
        self.assertTrue(result['capture_complete'], result['issues'])
        self.assertEqual(len(result['cases']), 1)
        self.assertEqual(len(result['excluded_cases']), 1)

    def test_parameter_file_must_be_the_file_consumed_by_launcher(self):
        log = self.case / 'meta/nextflow.log'
        log.write_text(log.read_text().replace(str(self.case / 'meta/params.json'), str(self.workspace / 'other.json')))
        result, _ = self.collect(include_non_controls=True, test_file='tests/controls.nf.test')
        self.assertFalse(result['capture_complete'])
        self.assertFalse(result['cases'])

    def test_unknown_parameter_combination_is_not_misidentified_as_default(self):
        log = self.case / 'meta/nextflow.log'
        log.write_text(log.read_text().replace('test,test_controls,docker', 'test,docker'))
        (self.case / 'meta/params.json').write_text('{"aligner":"star","skip_trimming":true}\n')
        (self.workspace / 'test.tap').write_text('ok 1 star\n')
        result, _ = self.collect(include_non_controls=True, test_file='tests/star.nf.test')
        self.assertFalse(result['capture_complete'])
        self.assertFalse(result['cases'])

    def test_only_resolved_selectors_are_persisted_from_params(self):
        params = {'outdir': 'DO_NOT_PERSIST_PRIVATE_OUTPUT', 'aligner': 'chromap', 'seq_center': 'DO_NOT_PERSIST_FREE_TEXT'}
        log = self.case / 'meta/nextflow.log'
        log.write_text(log.read_text().replace('test,test_controls,docker', 'test,docker'))
        (self.case / 'meta/params.json').write_text(json.dumps(params))
        (self.workspace / 'tests/chromap.nf.test').write_text('// chromap source\n')
        (self.workspace / 'test.tap').write_text('ok 1 chromap\n')
        result, output = self.collect(include_non_controls=True, test_file='tests/chromap.nf.test')
        self.assertTrue(result['capture_complete'], result['issues'])
        self.assertFalse(any(b'DO_NOT_PERSIST' in p.read_bytes() for p in output.rglob('*') if p.is_file()))

    def test_staged_explicit_config_is_captured_with_shell_continuations(self):
        extra = self.workspace / 'assets/extra.yaml'
        extra.write_text('use_filename_as_sample_name: false\n')
        (self.multiqc / extra.name).symlink_to(extra)
        (self.multiqc / '.command.sh').write_text('multiqc_fingerprint.py \\\n -f \\\n --config extra.yaml \\\n .\n')
        result, _ = self.collect()
        self.assertTrue(result['capture_complete'], result['issues'])
        self.assertEqual(result['cases'][0]['tasks'][1]['config_files'], ['multiqc_config.yml', 'extra.yaml'])
        self.assertEqual(sum(row['role'] == 'MULTIQC/config' for row in result['files']), 2)

    def test_nextflow_eval_postamble_c_is_not_a_multiqc_config(self):
        (self.multiqc / '.command.sh').write_text(
            '#!/usr/bin/env bash -C -e -u -o pipefail\n'
            'multiqc_fingerprint.py \\\n    -f \\\n     \\\n     \\\n    .\n\n'
            '# capture process environment\nset +u\nset +e\ncd "$NXF_TASK_WORKDIR"\n\n'
            'nxf_eval_cmd() {\n    {\n'
            '        IFS=$\'\\n\' read -r -d \'\' "${1}";\n'
            '        IFS=$\'\\n\' read -r -d \'\' "${2}";\n'
            '        (IFS=$\'\\n\' read -r -d \'\' _ERRNO_; return ${_ERRNO_});\n'
            '    } < <((printf \'\\0%s\\0%d\\0\' "${?}" "${?}" 1>&2) 2>&1)\n}\n\n'
            'echo \'\' > .command.env\n#\n'
            'nxf_eval_cmd STDOUT STDERR bash -c "multiqc --version | sed -e \\"s/multiqc, version //g\\""\n'
            'status=$?\nif [ $status -eq 0 ]; then\n'
            '  echo nxf_out_eval_2="$STDOUT" >> .command.env\n'
            '  echo /nxf_out_eval_2/=exit:0 >> .command.env\nfi\n')
        result, _ = self.collect()
        self.assertTrue(result['capture_complete'], result['issues'])
        self.assertEqual(result['cases'][0]['tasks'][1]['config_files'], ['multiqc_config.yml'])

    def test_genuine_multiqc_c_option_still_rejects_unsupported_config(self):
        (self.multiqc / '.command.sh').write_text('multiqc_fingerprint.py -f -c ../outside.yaml .\n')
        result, _ = self.collect()
        self.assertFalse(result['capture_complete'])
        self.assertIn('unsupported_config_argument', [issue['reason'] for issue in result['issues']])

    def test_multiple_multiqc_invocations_are_not_silently_merged(self):
        (self.multiqc / '.command.sh').write_text('multiqc_fingerprint.py -f .\nmultiqc_fingerprint.py -f .\n')
        result, _ = self.collect()
        self.assertFalse(result['capture_complete'])
        self.assertIn('unrecognized_config_arguments', [issue['reason'] for issue in result['issues']])

    def test_unknown_test_file_is_rejected(self):
        with self.assertRaises(ValueError):
            self.collect(include_non_controls=True, test_file='../other.nf.test')


if __name__ == '__main__':
    unittest.main()

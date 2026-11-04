import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import qualify_fingerprint_reports as q


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def selection(self):
        return {'expected_test_case': 'default', 'requested': {'test_file': 'tests/default.nf.test', 'nextflow': '25.10.4'},
                'cases': [{'stub_run': False, 'test_case': 'default', 'nextflow_version': '25.10.4', 'source_test_file': 'tests/default.nf.test'}],
                'profile_tap': {'default': {'matched_tests': 1, 'statuses': [{'status': 'failed'}]}}}

    def test_snapshot_failure_is_separate_from_semantic_case_selection(self):
        q.check_selection(self.selection())

    def test_missing_real_tap_rejects(self):
        data = self.selection(); data['profile_tap']['default']['matched_tests'] = 0
        with self.assertRaises(q.EvidenceError): q.check_selection(data)

    def test_wrong_engine_rejects(self):
        data = self.selection(); data['cases'][0]['nextflow_version'] = '26.10.5'
        with self.assertRaises(q.EvidenceError): q.check_selection(data)

    def test_wrong_matrix_case_rejects(self):
        data = self.selection(); data['cases'][0]['test_case'] = 'controls'
        with self.assertRaises(q.EvidenceError): q.check_selection(data)

    def test_stub_does_not_substitute_for_real_case(self):
        data = self.selection(); data['cases'][0]['stub_run'] = True
        with self.assertRaises(q.EvidenceError): q.check_selection(data)

    def test_capture_hash_and_path_boundary(self):
        file = self.root / 'raw.txt'; file.write_text('1\n')
        record = dict(path='raw.txt', **q.hashes(file))
        self.assertEqual(q.captured(self.root, record), file)
        file.write_text('2\n')
        with self.assertRaises(q.EvidenceError): q.captured(self.root, record)
        record['path'] = '../outside'
        with self.assertRaises(q.EvidenceError): q.captured(self.root, record)

    def test_symlink_rejects_even_with_correct_hash(self):
        file = self.root / 'raw.txt'; file.write_text('1\n')
        (self.root / 'alias').symlink_to(file)
        with self.assertRaises(q.EvidenceError): q.captured(self.root, dict(path='alias', **q.hashes(file)))

    def test_only_actual_invocation_is_parsed(self):
        command = "cat <<'EOF'\nunbalanced ' ignored\nEOF\nmultiqc_fingerprint.py \\\n --data-format tsv -f .\n"
        self.assertEqual(q.context(command)['arguments'], ['--data-format', 'tsv', '-f'])
        with self.assertRaises(q.UnknownContext): q.context('multiqc_fingerprint.py --mystery x .')

    def test_nextflow_eval_postamble_does_not_become_cli_configuration(self):
        command = ('#!/usr/bin/env bash -C -e -u -o pipefail\n'
                   'multiqc_fingerprint.py \\\n    -f \\\n    .\n'
                   'nxf_eval_cmd() {\n    IFS=$\'\\n\' read -r -d \'\' "${1}";\n}\n'
                   'nxf_eval_cmd STDOUT STDERR bash -c "multiqc --version | sed -e \\"s/multiqc, version //g\\""\n')
        self.assertEqual(q.context(command), {'arguments': ['-f'], 'config_names': [], 'naming_options': {}})

    def test_duplicate_invocation_rejects(self):
        with self.assertRaises(q.EvidenceError): q.context('multiqc_fingerprint.py .\nmultiqc_fingerprint.py .')

    def test_raw_rejects_blank_duplicate_and_nonfinite(self):
        for text in ["#plotFingerprint --outRawCounts\n'A'\t'A'\n1\t2\n", "#plotFingerprint --outRawCounts\n'A'\n0\n", "#plotFingerprint --outRawCounts\n'A'\nnan\n"]:
            with self.assertRaises(q.EvidenceError): q.raw_columns(text)

    def test_complete_profile_and_missing_producer_control(self):
        raw = []; originals = []
        for role in sorted(q.DESIGNS['default']):
            path = self.root / (role + '.mLb.clN.plotFingerprint.raw.txt')
            path.write_text("#plotFingerprint --outRawCounts\n'" + role + ".mLb.clN'\n1\n2\n")
            raw.append({'path': str(path), 'name': path.name}); originals.append((path.name, 'a', 1))
        q.check_design('default', originals, raw)
        # Matching reduced exports must not turn an incomplete scientific design green.
        with self.assertRaises(q.EvidenceError): q.check_design('default', originals[:-1], raw[:-1])

    def test_wrong_control_role_rejects(self):
        raw = []; originals = []
        for role in sorted(q.DESIGNS['controls']):
            path = self.root / (role + '.mLb.clN.plotFingerprint.raw.txt')
            control = 'control::' + role + '::SPT5_INPUT_REP' + role[-1] + '.mLb.clN'
            path.write_text("#plotFingerprint --outRawCounts\n'" + role + ".mLb.clN'\t'" + control + "'\n1\t2\n")
            raw.append({'path': str(path), 'name': path.name}); originals.append((path.name, 'a', 1))
        q.check_design('controls', originals, raw)
        path.write_text(path.read_text().replace('SPT5_INPUT_REP2', 'SPT5_INPUT_REP1'))
        with self.assertRaises(q.EvidenceError): q.check_design('controls', originals, raw)

    def test_plot_numeric_values_and_unique_roles(self):
        path = self.root / 'plot.txt'; path.write_text('\t0.5\t1.0\nA\t0.25\t1.0\n')
        expected = {'A': {'0.5': 0.25, '1.0': 1.0}}
        q.validate_plot_table(path, expected)
        for text in ['\t0.5\t1.0\nA\t0.3\t1.0\n', '\t0.5\t0.5\nA\t0.25\t1.0\n', '\t0.5\t1.0\nA\t0.25\tnan\n']:
            path.write_text(text)
            with self.assertRaises(q.EvidenceError): q.validate_plot_table(path, expected)

    def test_existing_empty_output_root_writes_explicit_failure(self):
        evidence = self.root / 'evidence'; evidence.mkdir(); (evidence / 'manifest.json').write_text(json.dumps({'capture_complete': False}))
        output = self.root / 'output'; output.mkdir()
        process = subprocess.run([sys.executable, str(Path(q.__file__)), '--evidence', str(evidence), '--output', str(output)], capture_output=True)
        self.assertEqual(process.returncode, 1)
        result = json.loads((output / 'RESULT.json').read_text())
        self.assertEqual(result['status'], 'FAIL'); self.assertIn('Global capture is incomplete', result['reason'])

    def test_every_no_control_profile_has_unique_original_plot_constraint(self):
        for name in q.CASES - {'controls'}:
            self.assertRegex(q.original_plot_md5(name), r'^[0-9a-f]{32}$')


if __name__ == '__main__':
    unittest.main()

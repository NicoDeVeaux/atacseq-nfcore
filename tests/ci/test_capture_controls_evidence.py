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

    def make_case(self, root, case_id):
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
        raw = fingerprint / 'sample__vs__control.plotFingerprint.raw.txt'
        raw.write_text('# sample\tcontrol\n1\t2\n')
        (multiqc / 'deeptools' / raw.name).symlink_to(raw)
        (multiqc / 'multiqc_config.yml').symlink_to(self.workspace / 'assets/multiqc_config.yml')
        (multiqc / collector.VERSIONS).write_text('Workflow:\n  Nextflow: 25.10.4\nMultiQC:\n  multiqc: 1.13\n')
        for name in collector.EXPORTS:
            # A broken blank export is valuable evidence too, not discarded by capture.
            (multiqc / 'multiqc_data' / name).write_text('Sample\t0.5\nsample\n')
        (case / 'meta/nextflow.log').write_text(
            f'DEBUG nextflow.cli.Launcher - $> nextflow run {self.workspace}/main.nf -profile test,test_controls,docker\n'
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


if __name__ == '__main__':
    unittest.main()

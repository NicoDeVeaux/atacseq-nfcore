import copy
import json
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('provenance_probe', Path(__file__).with_name('provenance.py'))
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)


class ProvenanceContracts(unittest.TestCase):
    def setUp(self):
        self.manifest = {'name': 'nf-core/atacseq', 'version': '2.2.0dev'}
        self.runtime = {'nextflow': '25.10.4', 'commit_id': None}
        self.valid = {'Workflow': {'nf-core/atacseq': 'v2.2.0dev', 'Nextflow': '25.10.4'}, 'FIXTURE_TOOL': {'fixture_tool': '1.2.3'}}

    def test_complete_provenance_and_fixture_tool(self):
        self.assertEqual(probe.scientific_checks(self.valid, self.manifest, self.runtime)['status'], 'PASS')
        for field in ['Workflow', 'FIXTURE_TOOL']:
            broken = copy.deepcopy(self.valid); del broken[field]
            self.assertEqual(probe.scientific_checks(broken, self.manifest, self.runtime)['status'], 'FAIL')

    def test_wrong_workflow_engine_and_tool_versions_reject(self):
        for section, key, value in [('Workflow', 'Nextflow', '26.04.4'), ('Workflow', 'nf-core/atacseq', 'v0.0.0'), ('FIXTURE_TOOL', 'fixture_tool', '9.9.9')]:
            broken = copy.deepcopy(self.valid); broken[section][key] = value
            self.assertEqual(probe.scientific_checks(broken, self.manifest, self.runtime)['status'], 'FAIL')

    def test_safe_yaml_rejects_duplicate_keys_and_python_constructors(self):
        self.assertEqual(probe.load_yaml('a: 1\n'), {'a': 1})
        for text in ['Workflow:\n  Nextflow: 1\n  Nextflow: 2\n', 'a: 1\na: 2\n', '!!python/object/apply:builtins.str [x]\n']:
            with self.assertRaises(probe.ScientificFailure): probe.load_yaml(text)

    def test_empty_or_nonschema_Workflow_is_scientific_failure(self):
        for document in [None, [], {}, {'Workflow': None}, {'Workflow': 'wrong'}, {'Workflow': {}}]:
            self.assertEqual(probe.scientific_checks(document, self.manifest, self.runtime)['status'], 'FAIL')

    def test_successful_Nextflow_corrupt_product_fails_but_dependency_error_is_ERROR(self):
        repo = Path(__file__).resolve().parents[2]
        source_manifest = probe.literal_manifest((repo / 'nextflow.config').read_text())
        def trial(yaml_text, engine_exit=0):
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / 'output'
                args = SimpleNamespace(repo=repo, output=output, source_ref='unit-fixture', nextflow='nextflow-unit-fixture', nextflow_version='25.10.4')
                def execute(argv, **kwargs):
                    if argv[0] == 'git':
                        data = (('a' * 40 + '\n').encode() if 'rev-parse' in argv else (repo / argv[-1].split(':', 1)[1]).read_bytes())
                        return subprocess.CompletedProcess(argv, 0, data, b'')
                    if engine_exit:
                        return subprocess.CompletedProcess(argv, engine_exit, b'', b'controlled setup failure')
                    (output / 'runtime.json').write_text(json.dumps({'nextflow': '25.10.4', 'manifest_name': source_manifest['name'], 'manifest_version': source_manifest['version'], 'commit_id': None}))
                    path = output / 'published/pipeline_info/nf_core_atacseq_software_mqc_versions.yml'
                    path.parent.mkdir(parents=True)
                    if yaml_text is not None: path.write_text(yaml_text)
                    return subprocess.CompletedProcess(argv, 0, b'', b'')
                with patch.object(probe.subprocess, 'run', side_effect=execute):
                    result = probe.run(args)
                return result
        for text in ['', 'Workflow: [', 'Workflow: {}\nWorkflow: {}\n', 'Workflow: null\n', None]:
            result = trial(text)
            self.assertEqual(result['status'], 'FAIL', result)
            self.assertEqual(result['commands'][-1]['exit'], 0)
        result = trial('', engine_exit=1)
        self.assertEqual(result['status'], 'ERROR')
        self.assertEqual(result['commands'][-1]['exit'], 1)

    def test_literal_manifest_refuses_dynamic_and_ambiguous_values(self):
        self.assertEqual(probe.literal_manifest("manifest {\n name = 'nf-core/atacseq'\n version = '2.2.0dev'\n}\n"), self.manifest)
        for text in ["manifest {\n name = 'nf-core/atacseq'\n version = params.version\n}\n", "manifest {\n name = 'x'\n version = '1'\n version = '2'\n}\n"]:
            with self.assertRaises(probe.InfrastructureError): probe.literal_manifest(text)

    def test_extraction_keeps_actual_expression_and_functions_exact(self):
        expression = "    def ch_collated_versions = channel.topic('versions')\n        .map { it }\n        .collectFile(name: 'x')"
        source = expression + '\n\n    // next section\nother()\n'
        self.assertEqual(probe.extract_collation(source), expression)
        helper = 'def workflowVersionToYAML() {\n    return "${workflow.nextflow.version}"\n}\n'
        self.assertEqual(probe.extract_function(helper, 'workflowVersionToYAML'), helper)
        with self.assertRaises(probe.InfrastructureError): probe.extract_collation(source + source)


if __name__ == '__main__': unittest.main()

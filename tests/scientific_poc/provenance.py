#!/usr/bin/env python3
"""Exercise the actual versions collector and workflow helper on a tiny channel."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
SOURCE_FILES = ('workflows/atacseq.nf', 'subworkflows/nf-core/utils_nfcore_pipeline/main.nf', 'nextflow.config')
FIXTURE = "channel.of(['POC:FIXTURE_TOOL', 'fixture_tool', '1.2.3'], ['POC:FIXTURE_TOOL', 'fixture_tool', '1.2.3'])"


class InfrastructureError(RuntimeError):
    pass


class ScientificFailure(ValueError):
    pass


def digest(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def extract_function(text, name):
    matches = list(re.finditer(r'(?ms)^def ' + re.escape(name) + r'\(\) \{\n.*?^\}\n', text))
    if len(matches) != 1:
        raise InfrastructureError('Unsupported or ambiguous actual helper: ' + name)
    return matches[0].group()


def extract_collation(text):
    start = list(re.finditer(r"(?m)^    def ch_collated_versions = channel.topic\('versions'\)\s*$", text))
    if len(start) != 1:
        raise InfrastructureError('Unsupported or ambiguous versions collector')
    end = text.find('\n\n    //', start[0].end())
    if end == -1:
        raise InfrastructureError('Missing collector boundary')
    expression = text[start[0].start():end].rstrip()
    if not expression.endswith(')') or expression.count('.collectFile(') != 1:
        raise InfrastructureError('Unsupported collector expression')
    return expression


def literal_manifest(text):
    blocks = re.findall(r'(?ms)^manifest\s*\{\n(.*?)^\}', text)
    if len(blocks) != 1:
        raise InfrastructureError('Expected one literal manifest block')
    result = {}
    for field in ('name', 'version'):
        lines = re.findall(r'(?m)^\s*' + field + r'\s*=\s*(.*?)\s*$', blocks[0])
        if len(lines) != 1:
            raise InfrastructureError('Missing or ambiguous manifest ' + field)
        match = re.fullmatch(r"(['\"])([A-Za-z0-9_./+-]+)\1", lines[0])
        if match is None:
            raise InfrastructureError('Nonliteral manifest ' + field)
        result[field] = match[2]
    return result


def load_yaml(text):
    import yaml
    class UniqueSafeLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError('Duplicate YAML mapping key')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result
    UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        return yaml.load(text, Loader=UniqueSafeLoader)
    except (yaml.YAMLError, ValueError, TypeError) as error:
        raise ScientificFailure('Malformed actual versions YAML: ' + type(error).__name__) from error


def scientific_checks(document, manifest, runtime):
    version = manifest['version']
    expected_version = version if version.startswith('v') else 'v' + version
    commit = runtime.get('commit_id')
    if commit:
        if not re.fullmatch(r'[0-9a-f]{7,40}', commit):
            raise InfrastructureError('Unexpected actual runtime commit identity')
        expected_version += '-g' + commit[:7]
    expected = {'Workflow': {manifest['name']: expected_version, 'Nextflow': runtime['nextflow']},
                'FIXTURE_TOOL': {'fixture_tool': '1.2.3'}}
    observed = document if isinstance(document, dict) else {}
    workflow = observed.get('Workflow') if isinstance(observed.get('Workflow'), dict) else {}
    checks = {
        'workflow_block_present': isinstance(observed.get('Workflow'), dict),
        'actual_pipeline_version_exact': workflow.get(manifest['name']) == expected_version,
        'actual_nextflow_version_exact': workflow.get('Nextflow') == runtime['nextflow'],
        'fixture_tool_retained_exactly': observed.get('FIXTURE_TOOL') == expected['FIXTURE_TOOL'],
        'complete_top_level_identity_set': set(observed) == set(expected),
    }
    return {'status': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks,
            'expected': expected, 'observed': document}


def run(args):
    output = args.output
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError('Output must be a new or empty regular directory')
    output.mkdir(parents=True, exist_ok=True); output = output.resolve()
    result = {'schema': 'atacseq-scientific-provenance-poc/v1', 'status': 'ERROR', 'commands': [],
              'scope': 'Tiny synthetic versions channel, actual extracted production collector and helpers, executed by real Nextflow. No process tools, scientific cohort or full pipeline execution.',
              'snapshots_read_or_updated': False, 'probe_source': digest(Path(__file__).read_bytes())}
    def command(argv, label, env=None):
        try:
            proc = subprocess.run(argv, cwd=output, env=env, capture_output=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InfrastructureError(label + ': ' + type(error).__name__) from error
        for stream in ('stdout', 'stderr'):
            data = getattr(proc, stream); (output / (label + '.' + stream)).write_bytes(data)
        result['commands'].append({'label': label, 'argv': list(map(str, argv)), 'exit': proc.returncode,
                                   'stdout': digest(proc.stdout), 'stderr': digest(proc.stderr)})
        if proc.returncode:
            raise InfrastructureError(label + ' failed; retained logs identify tool/setup errors')
        return proc.stdout
    try:
        import yaml
        result['yaml_version'] = yaml.__version__
        repo = args.repo.resolve(); commit = command(['git', '-C', str(repo), 'rev-parse', args.source_ref or 'HEAD'], 'git-identity').decode().strip()
        result['source_commit'] = commit; sources = {}; result['source_files'] = []
        for relative in SOURCE_FILES:
            tracked = command(['git', '-C', str(repo), 'show', commit + ':' + relative], 'source-' + Path(relative).name)
            data = tracked if args.source_ref else (repo / relative).read_bytes()
            target = output / 'source' / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
            sources[relative] = data.decode()
            result['source_files'].append({'path': relative, **digest(data), 'matches_recorded_git_commit': data == tracked})
        expression = extract_collation(sources[SOURCE_FILES[0]])
        helper = ''.join(extract_function(sources[SOURCE_FILES[1]], name) + '\n' for name in ('getWorkflowVersion', 'workflowVersionToYAML'))
        manifest = literal_manifest(sources['nextflow.config'])
        (output / 'actual-collector.nf.fragment').write_text(expression + '\n')
        (output / 'actual_helpers.nf').write_text(helper)
        result['extracted'] = {'collector': digest(expression.encode()), 'helper_functions': digest(helper.encode()),
                               'only_collector_replacement': "channel.topic('versions') -> " + FIXTURE}
        harness = "include { workflowVersionToYAML } from './actual_helpers'\nworkflow {\n    def outdir = params.outdir\n" + expression.replace("channel.topic('versions')", FIXTURE, 1) + '''
    ch_collated_versions.subscribe { produced ->
        file(params.runtime_receipt).text = groovy.json.JsonOutput.toJson([
            nextflow: workflow.nextflow.version.toString(),
            manifest_name: workflow.manifest.name,
            manifest_version: workflow.manifest.version,
            commit_id: workflow.commitId,
        ]) + '\\n'
    }
}
'''
        (output / 'probe.nf').write_text(harness)
        config = 'manifest.name = ' + json.dumps(manifest['name']) + '\nmanifest.version = ' + json.dumps(manifest['version']) + '\n'
        config += 'params.outdir = ' + json.dumps(str(output / 'published')) + '\nparams.runtime_receipt = ' + json.dumps(str(output / 'runtime.json')) + '\n'
        (output / 'probe.config').write_text(config)
        env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true')
        if args.nextflow_version: env['NXF_VER'] = args.nextflow_version
        command([args.nextflow, '-C', str(output / 'probe.config'), 'run', str(output / 'probe.nf'),
                 '-work-dir', str(output / 'work'), '-ansi-log', 'false'], 'nextflow-collector', env=env)
        runtime = json.loads((output / 'runtime.json').read_text())
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', runtime.get('nextflow', '')):
            raise InfrastructureError('Missing actual Nextflow version')
        if args.nextflow_version and runtime['nextflow'] != args.nextflow_version:
            raise InfrastructureError('Executed Nextflow differs from requested version')
        if (runtime['manifest_name'], runtime['manifest_version']) != (manifest['name'], manifest['version']):
            raise InfrastructureError('Actual runtime manifest differs from parsed source')
        observed = output / 'published/pipeline_info/nf_core_atacseq_software_mqc_versions.yml'
        result['runtime'] = runtime
        try:
            actual = observed.read_bytes()
        except FileNotFoundError as error:
            raise ScientificFailure('Missing versions YAML after successful Nextflow execution') from error
        result['output_yaml'] = {'path': str(observed.relative_to(output)), **digest(actual)}
        try:
            text = actual.decode('utf-8')
        except UnicodeError as error:
            raise ScientificFailure('Non-text versions YAML after successful Nextflow execution') from error
        document = load_yaml(text)
        result.update(scientific=scientific_checks(document, manifest, runtime),
                      inputs={'fixture_channel': digest(FIXTURE.encode()), 'harness': digest(harness.encode()), 'config': digest(config.encode())})
        result['status'] = result['scientific']['status']
        result['frozen_source_files_unchanged'] = all(digest((output / 'source' / r['path']).read_bytes())['sha256'] == r['sha256'] for r in result['source_files'])
        if not result['frozen_source_files_unchanged']: raise InfrastructureError('Frozen source drift')
    except ScientificFailure as error:
        result.update(status='FAIL', failure_class='scientific_contract', reason=str(error))
    except Exception as error:
        result.update(status='ERROR', reason=type(error).__name__ + ': ' + str(error))
    result['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (output / 'RESULT.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path); parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-ref'); parser.add_argument('--nextflow', default='nextflow'); parser.add_argument('--nextflow-version')
    args = parser.parse_args(); result = run(args)
    print(json.dumps({'status': result['status'], 'result': str(args.output / 'RESULT.json')}))
    return {'PASS': 0, 'FAIL': 1, 'ERROR': 2}[result['status']]


if __name__ == '__main__': sys.exit(main())

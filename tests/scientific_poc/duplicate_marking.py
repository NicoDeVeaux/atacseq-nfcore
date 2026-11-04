#!/usr/bin/env python3
"""Check actual Picard duplicate membership using production Chromap read-group selectors."""
import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PICARD = 'community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6'
SAMTOOLS = 'community.wave.seqera.io/library/htslib_samtools:1.24--d697cfb9dce007cd'
SOURCE_FILES = ('nextflow.config', 'conf/modules.config', 'modules/nf-core/picard/addorreplacereadgroups/main.nf',
    'modules/nf-core/picard/markduplicates/main.nf', 'subworkflows/nf-core/fastq_align_chromap/main.nf',
    'subworkflows/nf-core/bam_markduplicates_picard/main.nf', 'workflows/atacseq.nf')
RUNS = ('LIB_A_REP1_T1', 'LIB_A_REP1_T2', 'LIB_B_REP1_T1')
NAMES = {
    'a_primary': 'A_KEEP:1:FC:1:1101:100:100',
    'a_duplicate': 'A_DUP:1:FC:1:1101:101:100',
    'a_unique1': 'A_UNIQUE1:1:FC:1:1101:300:100',
    'a_unique2': 'A_UNIQUE2:1:FC:1:1101:500:100',
    'b_independent': 'B_KEEP:1:FC:1:1101:200:200',
}
READ_RUN = {NAMES[name]: RUNS[run] for name, run in
            [('a_primary', 0), ('a_unique1', 0), ('a_duplicate', 1), ('a_unique2', 1), ('b_independent', 2)]}
READ_START = {NAMES[name]: position for name, position in
              [('a_primary', 100), ('a_unique1', 300), ('a_duplicate', 100), ('a_unique2', 500), ('b_independent', 100)]}


class InfrastructureError(RuntimeError):
    pass


class ScientificFailure(RuntimeError):
    pass


def digest(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def pairs(name, start, quality):
    name = NAMES[name]
    return [
        '\t'.join(map(str, [name, 99, 'chrPoc', start, 60, '20M', '=', start + 50, 70, 'A' * 20, quality * 20])),
        '\t'.join(map(str, [name, 147, 'chrPoc', start + 50, 60, '20M', '=', start, -70, 'T' * 20, quality * 20])),
    ]


def make_inputs(folder):
    layout = {RUNS[0]: pairs('a_primary', 100, 'I') + pairs('a_unique1', 300, 'I'),
              RUNS[1]: pairs('a_duplicate', 100, '!') + pairs('a_unique2', 500, 'I'),
              RUNS[2]: pairs('b_independent', 100, '5')}
    for run, rows in layout.items():
        rows.sort(key=lambda row: (int(row.split('\t')[3]), row.split('\t')[0]))
        (folder / (run + '.sam')).write_text('@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chrPoc\tLN:2000\n' + '\n'.join(rows) + '\n')


def parse_options(args):
    tokens = shlex.split(args)
    result = {}
    for index in range(0, len(tokens), 2):
        if index + 1 >= len(tokens) or not tokens[index].startswith('--') or tokens[index] in result:
            raise InfrastructureError('Unexpected production Picard argument structure')
        result[tokens[index]] = tokens[index + 1]
    return result


def parse_sam(text):
    records, groups = [], {}
    for line in text.splitlines():
        if line.startswith('@RG\t'):
            try:
                fields = dict(field.split(':', 1) for field in line.split('\t')[1:])
                identifier = fields['ID']
            except (ValueError, KeyError) as error:
                raise ScientificFailure('Malformed produced read-group header') from error
            if identifier in groups:
                raise ScientificFailure('Duplicate read-group ID in observed BAM header')
            groups[identifier] = fields
        elif line and not line.startswith('@'):
            fields = line.split('\t')
            if len(fields) < 11:
                raise ScientificFailure('Malformed produced SAM alignment')
            try:
                flag, position = int(fields[1]), int(fields[3])
            except ValueError as error:
                raise ScientificFailure('Malformed produced SAM flag or coordinate') from error
            tags = {x.split(':', 2)[0]: x.split(':', 2)[2] for x in fields[11:] if x.count(':') >= 2}
            records.append({'name': fields[0], 'mate': 1 if flag & 64 else 2 if flag & 128 else None,
                'flag': flag, 'duplicate': bool(flag & 1024), 'rg': tags.get('RG'),
                'reference': fields[2], 'position': position, 'cigar': fields[5]})
    return groups, records


def scientific_checks(groups, records, expected_groups):
    expected_names = set(NAMES.values())
    expected_duplicate = {NAMES['a_duplicate']}
    by_name = {name: [r for r in records if r['name'] == name] for name in expected_names}
    observed_names = {r['name'] for r in records}
    observed_duplicate = {r['name'] for r in records if r['duplicate']}
    flags_consistent = all({r['mate'] for r in rows} == {1, 2} and len(rows) == 2
                           and len({r['duplicate'] for r in rows}) == 1 for rows in by_name.values())
    comparisons = {
        'all_input_pairs_present_exactly_once': observed_names == expected_names and len(records) == 10 and flags_consistent,
        'only_lower_quality_technical_resequence_pair_marked_duplicate': observed_duplicate == expected_duplicate,
        'independent_biological_library_same_coordinates_retained': all(not r['duplicate'] for r in by_name[NAMES['b_independent']]) and len(by_name[NAMES['b_independent']]) == 2,
        'retained_pair_membership_exact': {r['name'] for r in records if not r['duplicate']} == expected_names - expected_duplicate,
        'read_group_ids_exact': set(groups) == set(expected_groups),
        'read_group_tags_match_actual_selector_metadata': all(groups.get(k, {}).get('LB') == v['--RGLB'] and groups.get(k, {}).get('SM') == v['--RGSM'] for k, v in expected_groups.items()),
        'every_alignment_has_a_declared_read_group': all(r['rg'] in groups for r in records),
        'every_alignment_keeps_its_actual_run_read_group': all(r['rg'] == READ_RUN.get(r['name']) for r in records),
        'alignment_coordinates_cigar_and_nonduplicate_flags_unchanged': all(
            r['reference'] == 'chrPoc' and r['cigar'] == '20M'
            and r['position'] == READ_START.get(r['name'], -999) + (50 if r['mate'] == 2 else 0)
            and r['flag'] & ~1024 == (99 if r['mate'] == 1 else 147) for r in records),
        'technical_runs_share_library_and_keep_distinct_ids': len(set(expected_groups)) == 3 and expected_groups[RUNS[0]]['--RGLB'] == expected_groups[RUNS[1]]['--RGLB'],
        'biological_library_is_distinct': expected_groups[RUNS[0]]['--RGLB'] != expected_groups[RUNS[2]]['--RGLB'],
    }
    return {'status': 'PASS' if all(comparisons.values()) else 'FAIL', 'checks': comparisons,
        'expected_duplicate_pairs': sorted(expected_duplicate), 'observed_duplicate_pairs': sorted(observed_duplicate),
        'expected_retained_pairs': sorted(expected_names - expected_duplicate),
        'observed_retained_pairs': sorted({r['name'] for r in records if not r['duplicate']}),
        'duplicate_alignment_records': sum(r['duplicate'] for r in records), 'alignment_records': len(records),
        'observed_read_groups': groups, 'observed_alignment_membership': records}


def run(args):
    output = args.output.resolve()
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError('Output must be a new or empty regular directory')
    output.mkdir(parents=True, exist_ok=True)
    result = {'schema': 'atacseq-scientific-duplicate-marking-poc/v1', 'status': 'ERROR',
        'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'commands': [],
        'mutant': args.mutant, 'snapshots_read_or_updated': False,
        'scope': 'Five synthetic paired fragments, actual production selector evaluation and actual Picard marking. No alignment, peak calling, cloud execution or scientific cohort rerun.'}

    def command(argv, label, cwd=output, env=None, allowed_exits=(0,)):
        stdout, stderr = output / (label + '.stdout'), output / (label + '.stderr')
        try:
            process = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InfrastructureError(label + ': ' + type(error).__name__) from error
        stdout.write_bytes(process.stdout); stderr.write_bytes(process.stderr)
        result['commands'].append({'label': label, 'argv': list(map(str, argv)), 'exit': process.returncode,
            'stdout': {'path': stdout.name, **digest(stdout)}, 'stderr': {'path': stderr.name, **digest(stderr)}})
        if process.returncode not in allowed_exits:
            raise InfrastructureError(label + ' failed; retained stderr identifies setup/tool errors')
        return process.stdout.decode()

    try:
        repo = args.repo.resolve()
        source_commit = command(['git', '-C', str(repo), 'rev-parse', args.source_ref or 'HEAD'], 'source-commit').strip()
        result['source_commit'] = source_commit
        source_root = output / 'source'
        sources = []
        for relative in SOURCE_FILES:
            target = source_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if args.source_ref:
                target.write_bytes(subprocess.check_output(['git', '-C', str(repo), 'show', source_commit + ':' + relative]))
            else:
                shutil.copyfile(repo / relative, target)
            tracked = subprocess.check_output(['git', '-C', str(repo), 'show', source_commit + ':' + relative])
            sources.append({'path': relative, **digest(target), 'matches_recorded_git_commit': target.read_bytes() == tracked})
        result['source_files'] = sources
        guard_root = output / 'guard'
        guard_root.mkdir()
        result['guard_files'] = []
        for original in (Path(__file__), HERE / 'library_labels.nf'):
            frozen = guard_root / original.name
            shutil.copyfile(original, frozen)
            result['guard_files'].append({'path': 'guard/' + original.name, **digest(frozen)})
        config = output / 'probe.config'
        defaults = re.findall(r'(?ms)^params\s*\{.*?^\}', (source_root / 'nextflow.config').read_text())
        if len(defaults) != 1:
            raise InfrastructureError('Cannot isolate the production parameter defaults')
        for module_path in ('modules/nf-core/picard/addorreplacereadgroups/main.nf', 'modules/nf-core/picard/markduplicates/main.nf'):
            declared = {image for image in re.findall(r"'([^']*picard:[^']+)'", (source_root / module_path).read_text()) if not image.startswith('https://')}
            if declared != {args.picard_image}:
                raise InfrastructureError('Picard probe image differs from a production module declaration')
        config.write_text(defaults[0] + "\nparams.outdir = " + json.dumps(str(output / 'probe-published')) + "\n"
            "params.publish_dir_mode = 'copy'\nparams.aligner = 'chromap'\nparams.seq_center = 'PoC Center'\n"
            "params.save_align_intermeds = false\nparams.shift_reads = false\n"
            "includeConfig " + json.dumps(str(source_root / 'conf/modules.config')) + "\n"
            "process.executor = 'local'\nprocess.cpus = 1\nprocess.memory = '512 MB'\n"
            "docker.enabled = false\nsingularity.enabled = false\nconda.enabled = false\n")
        probe = output / 'selectors.json'
        command([args.nextflow, '-C', str(config), 'run', str(guard_root / 'library_labels.nf'),
            '--modules_config', str(source_root / 'conf/modules.config'), '--output_json', str(probe),
            '-work-dir', str(output / 'probe-work')], 'nextflow-production-selectors')
        labels = json.loads(probe.read_text())
        if labels['modules_config_sha256'] != digest(source_root / 'conf/modules.config')['sha256']:
            raise InfrastructureError('Selector/config source binding mismatch')
        result['effective_production_metadata'] = labels
        result['selector_receipt'] = {'path': probe.name, **digest(probe)}
        groups, marker = {}, None
        for row in labels['rows']:
            if row['kind'] == 'read_groups':
                group = parse_options(row['args'])
                if not row['process'].endswith(':FASTQ_ALIGN_CHROMAP:PICARD_ADDORREPLACEREADGROUPS') or group.get('--RGID') != row['meta']['id']:
                    raise InfrastructureError('Production read-group selector did not bind expected metadata')
                groups[row['meta']['id']] = group
            elif row['kind'] == 'mark_duplicates':
                marker = shlex.split(row['args'])
        if set(groups) != set(RUNS) or not marker:
            raise InfrastructureError('Incomplete actual selector evaluation')
        result['unmutated_read_groups'] = json.loads(json.dumps(groups))
        if args.mutant == 'merge-biological-libraries':
            groups[RUNS[2]]['--RGLB'] = groups[RUNS[0]]['--RGLB']
        result['effective_read_groups'] = groups
        docker = ['docker', '--context', args.docker_context] if args.docker_context else ['docker']
        images = {}
        for name, image in [('picard', args.picard_image), ('samtools', args.samtools_image)]:
            template = '{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}'
            observation = json.loads(command(docker + ['image', 'inspect', '--format', template, image], name + '-cached-image'))
            refs = observation.get('RepoDigests') or []
            if not refs:
                raise InfrastructureError('Cached image lacks an immutable digest')
            images[name] = {'requested': image, 'image_id': observation['Id'], 'repo_digests': refs,
                            'executed': refs[0], 'os': observation.get('Os'), 'architecture': observation.get('Architecture')}
        result['containers'] = images
        def tool(name, argv, label, allowed_exits=(0,)):
            return command(docker + ['run', '--rm', '--pull', 'never', '--network', 'none',
                '--platform', 'linux/' + images[name]['architecture'],
                '--hostname', 'scientific-poc', '--add-host', 'scientific-poc:127.0.0.1', '--cpus', '1', '--memory', '2g',
                '--mount', 'type=bind,source=' + str(output) + ',target=/evidence', '-w', '/evidence',
                '--entrypoint', argv[0], images[name]['executed']] + argv[1:], label, allowed_exits=allowed_exits)
        tool('picard', ['picard', 'MarkDuplicates', '--version'], 'picard-version', allowed_exits=(0, 1))
        if 'Version:3.4.0' not in (output / 'picard-version.stdout').read_text() + (output / 'picard-version.stderr').read_text():
            raise InfrastructureError('Actual Picard version is not 3.4.0')
        tool('samtools', ['samtools', '--version'], 'samtools-version')
        make_inputs(output)
        result['input_sams'] = [{'path': run + '.sam', **digest(output / (run + '.sam'))} for run in RUNS]
        for index, run_id in enumerate(RUNS):
            rg = [token for item in groups[run_id].items() for token in item]
            tool('picard', ['picard', '-Xmx512M', 'AddOrReplaceReadGroups'] + rg +
                 ['--INPUT', run_id + '.sam', '--OUTPUT', run_id + '.rg.bam'], 'add-read-groups-' + str(index + 1))
            if not (output / (run_id + '.rg.bam')).is_file() or (output / (run_id + '.rg.bam')).stat().st_size == 0:
                raise ScientificFailure('Successful read-group tool did not produce a nonempty BAM')
        tool('samtools', ['samtools', 'merge', '-f', 'combined.bam'] + [run_id + '.rg.bam' for run_id in RUNS], 'merge-coordinate-bams')
        tool('samtools', ['samtools', 'sort', '-o', 'coordinate.bam', 'combined.bam'], 'sort-coordinate-bam')
        (output / 'tmp').mkdir()
        tool('picard', ['picard', '-Xmx512M', 'MarkDuplicates'] + marker +
             ['--INPUT', 'coordinate.bam', '--OUTPUT', 'marked.bam', '--METRICS_FILE', 'marked.metrics.txt'], 'mark-duplicates')
        for name in ('marked.bam', 'marked.metrics.txt'):
            if not (output / name).is_file() or (output / name).stat().st_size == 0:
                raise ScientificFailure('Successful Picard marking did not produce nonempty ' + name)
        try:
            sam = tool('samtools', ['samtools', 'view', '-h', 'marked.bam'], 'marked-sam')
        except InfrastructureError as error:
            if result['commands'][-1]['label'] == 'marked-sam' and result['commands'][-1]['exit'] in (1, 2):
                raise ScientificFailure('Successful Picard marking produced a BAM that Samtools cannot decode') from error
            raise
        header, records = parse_sam(sam)
        result['scientific'] = scientific_checks(header, records, groups)
        result['status'] = result['scientific']['status']
        result['outputs'] = [{'path': p.name, **digest(p)} for p in sorted(output.glob('*.bam'))]
        result['outputs'].append({'path': 'marked.metrics.txt', **digest(output / 'marked.metrics.txt')})
        result['source_files_unchanged_after_execution'] = all(digest(source_root / r['path'])['sha256'] == r['sha256'] for r in sources)
        if not result['source_files_unchanged_after_execution']:
            raise InfrastructureError('Frozen source bytes changed during execution')
    except ScientificFailure as error:
        result.update(status='FAIL', scientific={'status': 'FAIL', 'reason': str(error)}, error_type=type(error).__name__)
    except Exception as error:
        result.update(status='ERROR', error_type=type(error).__name__, reason=str(error))
    result['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    write_json(output / 'RESULT.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-ref', help='Read immutable production files from this local Git object, without changing checkout HEAD')
    parser.add_argument('--docker-context', default='colima')
    parser.add_argument('--nextflow', default='nextflow')
    parser.add_argument('--picard-image', default=PICARD)
    parser.add_argument('--samtools-image', default=SAMTOOLS)
    parser.add_argument('--mutant', choices=['none', 'merge-biological-libraries'], default='none')
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({'status': result['status'], 'result': str(args.output / 'RESULT.json')}))
    return {'PASS': 0, 'FAIL': 1, 'ERROR': 2}[result['status']]


if __name__ == '__main__':
    sys.exit(main())

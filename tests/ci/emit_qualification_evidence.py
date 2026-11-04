#!/usr/bin/env python3
"""Emit a bounded, integrity-checked evidence capsule into the job log."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import textwrap

from capture_controls_evidence import UNSAFE


def capsule(evidence, result, tap):
    manifest = json.loads((evidence / 'manifest.json').read_text())
    members = {'capture/manifest.json': (evidence / 'manifest.json').read_bytes()}
    for record in manifest.get('files', []):
        relative = Path(record['path'])
        source = evidence / relative
        if relative.is_absolute() or '..' in relative.parts or source.is_symlink():
            raise ValueError('Unsafe evidence path')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('Evidence hash mismatch')
        members['capture/' + relative.as_posix()] = data
    if result.is_file():
        members['qualification/RESULT.json'] = result.read_bytes()
    if tap.is_file():
        members['test.tap'] = tap.read_bytes()
    if sum(map(len, members.values())) > 20 * 1024 * 1024:
        raise ValueError('Evidence exceeds uncompressed log budget')
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as output:
        for name, data in sorted(members.items()):
            if UNSAFE.search(data.decode('utf-8')):
                raise ValueError('Unsafe evidence text')
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            output.addfile(info, io.BytesIO(data))
    data = archive.getvalue()
    if len(data) > 1024 * 1024:
        raise ValueError('Evidence exceeds compressed log budget')
    return data, len(members)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--tap', required=True, type=Path)
    args = parser.parse_args()
    data, files = capsule(args.evidence, args.result, args.tap)
    print(json.dumps({'schema': 'atacseq-free-qualification-capsule/v1',
                      'bytes': len(data), 'files': files,
                      'sha256': hashlib.sha256(data).hexdigest()}))
    print('BEGIN_ATACSEQ_QUALIFICATION_TGZ_BASE64')
    print('\n'.join(textwrap.wrap(base64.b64encode(data).decode(), 120)))
    print('END_ATACSEQ_QUALIFICATION_TGZ_BASE64')


if __name__ == '__main__':
    main()

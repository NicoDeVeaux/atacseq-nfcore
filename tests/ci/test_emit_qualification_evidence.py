import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('emitter', Path(__file__).with_name('emit_qualification_evidence.py'))
emitter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(emitter)


class CapsuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.source = self.evidence / 'raw.txt'
        self.source.write_text('# public fixture\n1\t2\n')
        self.result = self.root / 'RESULT.json'
        self.result.write_text('{"status": "FAIL"}\n')
        self.tap = self.root / 'test.tap'
        self.tap.write_text('not ok 1 public fixture\n')
        self.manifest()

    def manifest(self):
        (self.evidence / 'manifest.json').write_text(json.dumps({'files': [{
            'path': 'raw.txt', 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}]}))

    def test_capsule_preserves_failed_evidence_without_unlisted_files(self):
        (self.evidence / 'environment.txt').write_text('not captured\n')
        data, count = emitter.capsule(self.evidence, self.result, self.tap)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.getnames()), {
                'capture/manifest.json', 'capture/raw.txt', 'qualification/RESULT.json', 'test.tap'})
            self.assertEqual(archive.extractfile('test.tap').read(), self.tap.read_bytes())
        self.assertEqual(count, 4)

    def test_tampered_capture_is_rejected(self):
        self.source.write_text('different values\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            emitter.capsule(self.evidence, self.result, self.tap)

    def test_capability_text_is_rejected_even_with_matching_hash(self):
        self.source.write_text('https://example.org/file?token=example_capability\n')
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'Unsafe evidence text'):
            emitter.capsule(self.evidence, self.result, self.tap)

    def test_symlink_parent_cannot_extend_capture(self):
        (self.evidence / 'outside').symlink_to(self.root, target_is_directory=True)
        data = self.result.read_bytes()
        (self.evidence / 'manifest.json').write_text(json.dumps({'files': [{
            'path': 'outside/RESULT.json', 'sha256': hashlib.sha256(data).hexdigest()}]}))
        with self.assertRaisesRegex(ValueError, 'Unsafe evidence path'):
            emitter.capsule(self.evidence, self.result, self.tap)

    def test_symlink_cannot_extend_capture(self):
        self.source.unlink()
        self.source.symlink_to(self.tap)
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'Unsafe evidence path'):
            emitter.capsule(self.evidence, self.result, self.tap)


if __name__ == '__main__':
    unittest.main()

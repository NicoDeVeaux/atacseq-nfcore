import gzip
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('trimming', Path(__file__).with_name('trimming.py'))
trimming = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trimming)


class TrimmingContracts(unittest.TestCase):
    def test_independent_wrong_end_witnesses(self):
        for case, wrong in [('r2_5prime', trimming.R2[:-7]), ('r2_3prime', trimming.R2[9:])]:
            with self.subTest(case=case):
                expected = trimming.records(case, trimming.CASES[case]['expected2'], 2)
                self.assertTrue(trimming.compare_records(expected, expected)['passed'])
                observed = trimming.records(case, wrong, 2)
                self.assertFalse(trimming.compare_records(expected, observed)['passed'])
                self.assertEqual(len(expected[0][1]), len(observed[0][1]))

    def test_poly_g_or_c_loss_is_not_a_valid_default(self):
        for mate, shortened in [(1, trimming.R1), (2, trimming.R2)]:
            expected = trimming.records('poly_g', trimming.CASES['poly_g'][f'expected{mate}'], mate)
            self.assertFalse(trimming.compare_records(expected, trimming.records('poly_g', shortened, mate))['passed'])

    def test_membership_quality_and_duplicate_corruption_fail(self):
        expected = trimming.records('fixture', trimming.R1, 1)
        corruptions = [expected[:-1], [], expected + expected[:1],
                       [(expected[0][0], expected[0][1], 'J' * len(expected[0][1]))] + expected[1:],
                       [('different/1', expected[0][1], expected[0][2])] + expected[1:]]
        for observed in corruptions:
            with self.subTest(observed_records=len(observed)):
                self.assertFalse(trimming.compare_records(expected, observed)['passed'])
        self.assertTrue(trimming.compare_records(expected, list(reversed(expected)))['passed'])

    def test_fastq_roundtrip_and_malformed_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'reads.fastq.gz'
            expected = trimming.records('fixture', trimming.R1, 1)
            trimming.write_fastq(path, expected)
            self.assertEqual(trimming.read_fastq(path), expected)
            path.write_bytes(gzip.compress(b'@read\nACTG\n+\nIII\n', mtime=0))
            with self.assertRaises(ValueError):
                trimming.read_fastq(path)

    def test_empty_malformed_and_truncated_gzip_are_scientific_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'reads.fastq.gz'
            expected = trimming.records('fixture', trimming.R1, 1)
            trimming.write_fastq(path, expected)
            self.assertTrue(trimming.assess_fastq(expected, path)['passed'])
            good = path.read_bytes()
            for content in (gzip.compress(b'', mtime=0), gzip.compress(b'@read\nACTG\n+\nIII\n', mtime=0), good[:-5]):
                with self.subTest(compressed_bytes=len(content)):
                    path.write_bytes(content)
                    self.assertFalse(trimming.assess_fastq(expected, path)['passed'])


if __name__ == '__main__':
    unittest.main()

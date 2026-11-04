import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('fingerprint_reports', Path(__file__).parents[1] / 'regressions/fingerprint_reports.py')
reports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reports)


class ParserOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.inputs = [self.root / name for name in ('a.raw.txt', 'b.raw.txt')]
        for source in self.inputs:
            source.write_text('fixture\n')
        self.output = self.root / 'output'
        (self.output / 'multiqc_data').mkdir(parents=True)
        self.save_sources(self.inputs)

    def save_sources(self, inputs):
        sources = {str(index): str(path) for index, path in enumerate(inputs)}
        report = {'report_data_sources': {'deepTools': {'plotFingerprint': sources}}}
        (self.output / 'multiqc_data/multiqc_data.json').write_text(json.dumps(report))

    def test_observed_parser_order_is_recorded(self):
        self.assertEqual(reports.validate_input_order(self.output, self.inputs), [str(path) for path in self.inputs])

    def test_unchanged_read_order_cannot_count_as_a_reverse_run(self):
        with self.assertRaises(reports.ContractError):
            reports.validate_input_order(self.output, list(reversed(self.inputs)))

    def test_missing_or_repeated_source_cannot_validate_an_order(self):
        for sources in (self.inputs[:1], [self.inputs[0], self.inputs[0]]):
            with self.subTest(sources=sources):
                self.save_sources(sources)
                with self.assertRaises(reports.ContractError):
                    reports.validate_input_order(self.output, self.inputs)


if __name__ == '__main__':
    unittest.main()

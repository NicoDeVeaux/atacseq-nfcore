import copy
import json
import tempfile
import unittest
from pathlib import Path

from change_report import EvidenceError, compare_counts, compare_snapshot_case, load_json

HEADER = "Geneid\tChr\tStart\tEnd\tStrand\tLength\t/run/A.bam\t/run/B.bam\n"
ROWS = ["g1\tchr1\t1\t10\t+\t10\t10\t20\n", "g2\tchr1\t11\t20\t+\t10\t30\t40\n"]

class CountReportControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.left = self.root / "left.tsv"
        self.right = self.root / "right.tsv"
        self.left.write_text(HEADER+"".join(ROWS))
        self.right.write_text(self.left.read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def compare(self):
        return compare_counts(self.left,self.right)

    def test_identical(self):
        result=self.compare()
        self.assertEqual(result["kind"],"BYTE_IDENTICAL")
        self.assertEqual(result["comparable_cells"],4)
        self.assertEqual(result["scientific_acceptance"],"UNKNOWN")

    def test_row_and_sample_reordering_preserves_pairs(self):
        self.right.write_text("Geneid\tChr\tStart\tEnd\tStrand\tLength\t/new/B.bam\t/new/A.bam\n"+"g2\tchr1\t11\t20\t+\t10\t40\t30\n"+"g1\tchr1\t1\t10\t+\t10\t20\t10\n")
        result=self.compare()
        self.assertEqual(result["changed_count_cells"],0)
        self.assertEqual(result["kind"],"ORDER_ONLY_WITH_POSSIBLE_SERIALIZATION_CHANGES")

    def test_swapped_sample_values_are_scientific_change(self):
        self.right.write_text(HEADER+"g1\tchr1\t1\t10\t+\t10\t20\t10\n"+ROWS[1])
        result=self.compare()
        self.assertEqual(result["changed_count_cells"],2)
        self.assertEqual(result["shared_cells_total_delta"],"0")
        self.assertEqual(result["affected_features"],1)
        self.assertEqual(result["kind"],"SCIENTIFIC_CONTENT_CHANGED")

    def test_changed_count_is_not_hidden_by_same_row_totals(self):
        self.right.write_text(HEADER+ROWS[0].replace("\t10\t20\n","\t9\t21\n")+ROWS[1])
        self.assertEqual(self.compare()["changed_count_cells"],2)

    def test_missing_feature(self):
        self.right.write_text(HEADER+ROWS[0])
        result=self.compare()
        self.assertEqual(result["kind"],"IDENTITY_CHANGED")
        self.assertEqual(result["missing_features"],["g2"])
        self.assertFalse(result["identity_preserved"])

    def test_extra_feature(self):
        self.right.write_text(HEADER+"".join(ROWS)+"g3\tchr1\t21\t30\t+\t10\t0\t0\n")
        self.assertEqual(self.compare()["extra_features"],["g3"])

    def test_missing_sample(self):
        self.right.write_text("Geneid\tChr\tStart\tEnd\tStrand\tLength\tA.bam\n"+"g1\tchr1\t1\t10\t+\t10\t10\n")
        self.assertEqual(self.compare()["missing_samples"],["B.bam"])

    def test_duplicate_features_are_rejected(self):
        self.right.write_text(HEADER+ROWS[0]+ROWS[0])
        with self.assertRaises(EvidenceError):self.compare()

    def test_duplicate_normalized_sample_names_are_rejected(self):
        self.right.write_text(self.right.read_text().replace("/run/B.bam","/different/A.bam"))
        with self.assertRaises(EvidenceError):self.compare()

    def test_nonfinite_negative_and_ragged_counts_are_rejected(self):
        for value in ["NaN","Infinity","-1","text"]:
            with self.subTest(value=value):
                self.right.write_text(HEADER+ROWS[0].replace("\t10\t20\n",f"\t{value}\t20\n"))
                with self.assertRaises(EvidenceError):self.compare()
        self.right.write_text(HEADER+"g1\tchr1\t1\n")
        with self.assertRaises(EvidenceError):self.compare()

    def test_feature_metadata_changes_are_not_ignored(self):
        self.right.write_text(HEADER+ROWS[0].replace("\t1\t10\t","\t2\t10\t")+ROWS[1])
        result=self.compare()
        self.assertEqual(result["changed_count_cells"],0)
        self.assertEqual(result["changed_metadata_fields"],1)
        self.assertEqual(result["kind"],"SCIENTIFIC_CONTENT_CHANGED")

    def test_renumbered_peaks_are_matched_by_declared_coordinates(self):
        self.right.write_text(HEADER+ROWS[0].replace("g1\t","g2\t")+ROWS[1].replace("g2\t","g1\t"))
        result=compare_counts(self.left,self.right,key_mode="interval")
        self.assertEqual(result["changed_count_cells"],0)
        self.assertEqual(result["renamed_feature_ids"],2)
        self.assertEqual(result["kind"],"FEATURE_LABELS_CHANGED")
        self.assertEqual(result["renamed_feature_examples"][0]["before_feature_id"],"g1")
        self.assertEqual(result["renamed_feature_examples"][0]["after_feature_id"],"g2")
        self.assertEqual(result["sample_header_aliases"]["before"]["A.bam"],"/run/A.bam")

    def test_interval_membership_changes_are_explicit(self):
        self.right.write_text(HEADER+ROWS[0].replace("\t1\t10\t","\t2\t10\t")+ROWS[1])
        result=compare_counts(self.left,self.right,key_mode="interval")
        self.assertEqual(result["kind"],"IDENTITY_CHANGED")
        self.assertEqual(len(result["missing_features"]),1)
        self.assertEqual(len(result["extra_features"]),1)

    def test_ambiguous_coordinate_identity_is_rejected(self):
        self.right.write_text(HEADER+ROWS[0]+ROWS[0].replace("g1\t","g3\t"))
        with self.assertRaises(EvidenceError):compare_counts(self.left,self.right,key_mode="interval")

    def test_numeric_serialization_is_exact(self):
        self.right.write_text(self.right.read_text().replace("\t10\t20\n","\t10.0\t20.000\n"))
        self.assertEqual(self.compare()["kind"],"SERIALIZATION_OR_PATH_ONLY")
        self.right.write_text(HEADER+ROWS[0].replace("\t10\t20\n","\t10.00000000000000000001\t20\n")+ROWS[1])
        self.assertEqual(self.compare()["changed_count_cells"],1)

class SnapshotReportControls(unittest.TestCase):
    def fixture(self):
        return {"content":[2,{"TOOL":{"tool":"1.0"}},["counts.tsv"],["counts.tsv:md5,"+"a"*32]],"meta":{"nf-test":"0.9.4"}}

    def test_restored_workflow_is_provenance(self):
        before=self.fixture();after=copy.deepcopy(before)
        after["content"][1]["Workflow"]={"nf-core/atacseq":"v2.2.0dev"}
        result=compare_snapshot_case(before,after)
        self.assertTrue(result["only_provenance_expectation_changed"])
        self.assertEqual(result["hash_changes"],[])

    def test_new_hash_alone_is_unexplained(self):
        before=self.fixture();after=copy.deepcopy(before)
        after["content"][3][0]="counts.tsv:md5,"+"b"*32
        result=compare_snapshot_case(before,after)
        self.assertEqual(result["hash_changes"][0]["kind"],"OPAQUE_HASH_CHANGED")
        self.assertEqual(result["scientific_acceptance"],"UNKNOWN")

    def test_repeated_filename_change_is_ambiguous(self):
        before=self.fixture();after=copy.deepcopy(before)
        before["content"][3].append("counts.tsv:md5,"+"c"*32)
        result=compare_snapshot_case(before,after)
        self.assertEqual(result["hash_changes"][0]["kind"],"AMBIGUOUS_REPEATED_FILENAME")

    def test_swapped_duplicate_basenames_are_not_silently_canonicalized(self):
        before=self.fixture()
        before["content"][3].append("counts.tsv:md5,"+"b"*32)
        after=copy.deepcopy(before)
        after["content"][3].reverse()
        result=compare_snapshot_case(before,after)
        self.assertEqual(result["hash_changes"][0]["kind"],"AMBIGUOUS_REPEATED_FILENAME")
        self.assertEqual(result["hash_changes"][0]["expectations_removed"],0)

    def test_refreshed_snapshot_does_not_approve_science(self):
        refreshed=self.fixture()
        result=compare_snapshot_case(refreshed,copy.deepcopy(refreshed))
        self.assertTrue(result["content_identical"])
        self.assertEqual(result["scientific_acceptance"],"UNKNOWN")

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(EvidenceError):load_json('{"sample":1,"sample":2}')

if __name__=="__main__":unittest.main()

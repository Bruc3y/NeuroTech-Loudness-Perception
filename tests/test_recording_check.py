import copy
import csv
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_pilot_recording import inspect


class RecordingCheckTests(unittest.TestCase):
    def test_matching_pulses_and_failures(self):
        # Synthetic fixture only. It is never saved in a participant/run directory.
        run = {"run_id": "SYNTHETIC_TEST", "status": "completed", "expected_nonzero_codes": [1, 4, 10, 11]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.csv"
            rows = [[round((i + 1) / 300, 4), 5, 0, i % 256] for i in range(900)]
            for start, code in zip((60, 210, 360, 510), run["expected_nonzero_codes"]):
                for i in range(start, start + 15):
                    rows[i][2] = code

            def write():
                with path.open("w", newline="") as stream:
                    stream.write("Synthetic fixture; not acquired EEG\n")
                    writer = csv.writer(stream)
                    writer.writerow(["Time", "F3", "Trigger", "ADC_SEQ"])
                    writer.writerows(rows)

            write()
            report = inspect(path, run)
            self.assertTrue(report["marker_delivery_check_passed"])
            self.assertEqual(report["sample_rate_hz"], 300)
            self.assertFalse(report["physical_acoustic_onsets_measured"])
            wrong = copy.deepcopy(run)
            wrong["expected_nonzero_codes"][1] = 5
            self.assertFalse(inspect(path, wrong)["marker_delivery_check_passed"])
            rows[600][3] = (rows[600][3] + 5) % 256
            write()
            self.assertFalse(inspect(path, run)["marker_delivery_check_passed"])

    def test_streamer_free_text_comments_do_not_hide_missing_eeg(self):
        run = {"run_id": "SYNTHETIC_TEST", "status": "completed", "expected_nonzero_codes": [12]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.csv"
            rows = [[round((i + 1) / 300, 4), 1.0, 12 if 30 <= i < 46 else 0,
                     "baseline note" if i == 20 else ""] for i in range(90)]

            def write():
                with path.open("w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["Time", "F3", "Trigger", "Comments"])
                    writer.writerows(rows)

            write()
            self.assertTrue(inspect(path, run, 300)["marker_delivery_check_passed"])
            rows[10][1] = ""
            write()
            with self.assertRaises(ValueError):
                inspect(path, run, 300)


if __name__ == "__main__":
    unittest.main()

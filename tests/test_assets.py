import sys
from pathlib import Path
import unittest
import wave
from collections import Counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from station_common import ROOT, inventory, make_schedule


class AssetTests(unittest.TestCase):
    def test_wav_formats_and_duration(self):
        for path in inventory()["audio"].values():
            with wave.open(str(ROOT / "web" / path.lstrip("/"))) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()),
                                 (1, 3, 48000, 48000))

    def test_balanced_design_uses_images_without_replacement(self):
        for count in (6, 72):
            schedule = make_schedule(count, 42)
            self.assertEqual(len(schedule), count)
            self.assertEqual(len({row["image_id"] for row in schedule}), count)
            cells = Counter((row["valence"], row["level_label"]) for row in schedule)
            self.assertEqual(len(cells), 6)
            self.assertEqual(set(cells.values()), {count // 6})
            self.assertEqual({row["tone_code"] for row in schedule}, set(range(4, 10)))

    def test_full_schedule_constraints_across_seeds(self):
        assets = inventory()
        all_ids = {image["image_id"] for image in assets["images"]}
        with patch("station_common.inventory", return_value=assets):
            for seed in range(200):
                rows = make_schedule(72, seed)
                self.assertEqual({row["image_id"] for row in rows}, all_ids)
                self.assertEqual(len(rows), 72)
                self.assertEqual(set(Counter((r["valence"], r["level_label"]) for r in rows).values()), {12})
                levels = [row["level_label"] for row in rows]
                self.assertTrue(all(len(set(levels[i:i+3])) > 1 for i in range(70)))
            self.assertEqual(make_schedule(72, 42), make_schedule(72, 42))

    def test_jittered_timing_is_bounded_reproducible_and_varied(self):
        from station_common import SOA_RANGE_S, ITI_RANGE_S, trial_timing
        rows = make_schedule(72, 7)
        soas = [row["soa_s"] for row in rows]
        self.assertTrue(all(SOA_RANGE_S[0] <= v <= SOA_RANGE_S[1] for v in soas))
        self.assertTrue(all(ITI_RANGE_S[0] <= row["iti_s"] <= ITI_RANGE_S[1] for row in rows))
        self.assertGreater(len(set(soas)), 60)
        self.assertGreater(max(soas) - min(soas), 0.8)
        self.assertEqual(trial_timing(72, 7), trial_timing(72, 7))
        self.assertNotEqual(trial_timing(72, 7), trial_timing(72, 8))


if __name__ == "__main__":
    unittest.main()

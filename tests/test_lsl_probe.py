import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lsl_probe import select_stream


def outlet(name, source):
    return SimpleNamespace(name=lambda: name, source_id=lambda: source)


class LSLSelectionTests(unittest.TestCase):
    def test_requires_a_unique_exact_outlet(self):
        a, b = outlet("EEG", "headset-a"), outlet("EEG", "headset-b")
        for streams in ([], [a, b]):
            with self.assertRaises(ValueError):
                select_stream(streams, "EEG")
        self.assertIs(select_stream([a, b], "EEG", "headset-b"), b)
        self.assertIs(select_stream([a, outlet("other", "x")], "EEG"), a)

import tempfile
import unittest
from pathlib import Path

from pipeline import config, state


class TestState(unittest.TestCase):
    def setUp(self):
        self._old_json = config.EPISODES_JSON
        self.tmp = tempfile.mkdtemp()
        config.EPISODES_JSON = Path(self.tmp) / "episodes.json"

    def tearDown(self):
        config.EPISODES_JSON = self._old_json

    def _sample(self):
        return [{"no": 1, "status": "pending", "retries": 0,
                 "qa": {"flags": []}},
                {"no": 2, "status": "pending", "retries": 0,
                 "qa": {"flags": []}}]

    def test_roundtrip(self):
        eps = self._sample()
        state.save(eps)
        self.assertEqual(state.load(), eps)

    def test_load_missing_raises(self):
        with self.assertRaises(state.StateError):
            state.load()

    def test_illegal_transition(self):
        ep = {"no": 1, "status": "pending", "retries": 0}
        with self.assertRaises(state.StateError):
            state.set_status(ep, "done")  # pending 不能直达 done
        with self.assertRaises(state.StateError):
            state.set_status(ep, "suspect")

    def test_legal_transitions(self):
        ep = {"no": 1, "status": "pending", "retries": 0}
        for path in ("converting", "transcribing", "extracting", "done"):
            state.set_status(ep, path)
        self.assertEqual(ep["status"], "done")
        with self.assertRaises(state.StateError):
            state.set_status(ep, "pending")  # done 是终态

    def test_failed_rearm(self):
        ep = {"no": 1, "status": "failed", "retries": 2}
        state.set_status(ep, "pending")
        self.assertEqual(ep["status"], "pending")

    def test_selection(self):
        self.assertEqual(state.parse_selection("1-3,5", 10), [1, 2, 3, 5])
        self.assertEqual(state.parse_selection("all", 3), [1, 2, 3])
        self.assertEqual(state.parse_selection("67", 67), [67])
        with self.assertRaises(state.StateError):
            state.parse_selection("5-1", 10)
        with self.assertRaises(state.StateError):
            state.parse_selection("abc", 10)

    def test_by_no(self):
        eps = self._sample()
        self.assertEqual(state.by_no(eps, 2)["no"], 2)
        with self.assertRaises(state.StateError):
            state.by_no(eps, 99)


if __name__ == "__main__":
    unittest.main()

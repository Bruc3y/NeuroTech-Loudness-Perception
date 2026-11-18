import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

spec = importlib.util.spec_from_file_location("station", Path(__file__).resolve().parents[1] / "server.py")
station = importlib.util.module_from_spec(spec)
spec.loader.exec_module(station)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        station.DATA = Path(self.directory.name)
        self.server = station.ThreadingHTTPServer(("127.0.0.1", 0), station.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def call(self, route, body=None):
        request = urllib.request.Request(self.url + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            value = response.read()
            return response.status, json.loads(value) if "application/json" in response.headers.get("Content-Type", "") else value

    def test_unique_session_and_restricted_participant_path(self):
        status, first = self.call("/api/sessions", {"participant": "DEMO01"})
        self.assertEqual(status, 201)
        _, second = self.call("/api/sessions", {"participant": "DEMO01"})
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.call("/api/sessions", {"participant": "../escape"})[0], 400)
        self.assertEqual(self.call("/api/sessions", {"participant": None})[0], 400)
        self.assertEqual(self.call("/api/sessions", ["DEMO"])[0], 400)
        self.assertEqual(self.call("/data/anything")[0], 404)

    def test_checkpoint_conflict_and_zip_match(self):
        _, session = self.call("/api/sessions", {"participant": "DEMO02"})
        route = "/api/sessions/" + session["id"]
        state = copy.deepcopy(session["state"])
        state["phase"] = "screening"
        state["screening"] = {"difficulty": "No"}
        status, saved = self.call(route, {"revision": 0, "state": state})
        self.assertEqual(status, 200)
        self.assertEqual(self.call(route, {"revision": 0, "state": state})[0], 409)
        self.assertEqual(self.call(route)[1]["state"]["screening"], state["screening"])
        status, body = self.call(route + "/export")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            manifest = next(name for name in archive.namelist() if name.endswith("manifest.json"))
            checkpoint = next(name for name in archive.namelist() if name.endswith("/session.json"))
            self.assertFalse(json.loads(archive.read(manifest))["eeg_recorded"])
            self.assertEqual(json.loads(archive.read(checkpoint))["revision"], saved["revision"])

    def test_finished_sessions_are_preserved(self):
        _, session = self.call("/api/sessions", {"participant": "DEMO03"})
        route = "/api/sessions/" + session["id"]
        state = session["state"]
        state["phase"] = "aborted"
        self.assertEqual(self.call(route, {"revision": 0, "state": state})[0], 200)
        state["phase"] = "setup"
        self.assertEqual(self.call(route, {"revision": 1, "state": state})[0], 409)
        self.assertEqual(self.call(route)[1]["state"]["phase"], "aborted")

    def test_assets_with_spaces_and_data_boundary(self):
        from urllib.parse import quote
        status, stimuli = self.call("/api/stimuli")
        self.assertEqual(status, 200)
        self.assertEqual(len(stimuli["images"]), 72)
        for image in stimuli["images"]:
            status, body = self.call(quote(image["path"]))
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(b"\xff\xd8"))
        for audio in stimuli["audio"].values():
            status, body = self.call(audio)
            self.assertEqual(status, 200)
            self.assertTrue(body.startswith(b"RIFF"))
        for route in ("/assets/../server.py", "/assets/%2e%2e/server.py", "/assets/audio/verification.json", "/data/session.json"):
            self.assertEqual(self.call(route)[0], 404)
        _, session = self.call("/api/sessions", {"participant": "ASSETTEST"})
        self.assertEqual(session["protocol"]["tone_seconds"], 1.0)
        self.assertFalse(session["protocol"]["acoustic_calibration_verified"])
        self.assertEqual(session["protocol"]["main_trials"], 72)
        rows = session["state"]["schedule"]
        self.assertEqual(len({row["image_id"] for row in rows}), 72)
        levels = [row["level"] for row in rows]
        self.assertTrue(all(len(set(levels[i:i+3])) > 1 for i in range(70)))
        self.assertEqual(self.call('/api/sessions/' + session['id'])[1]['state']['schedule'], rows)

    def test_full_72_trial_export_and_saved_order(self):
        _, session = self.call('/api/sessions', {'participant': 'FULL72'})
        state = session['state']
        rows = state['schedule']
        state['phase'] = 'complete'
        state['ratings'] = [dict(row, attempt_id='qa-' + str(i), rating=4, rt_ms=500)
                            for i, row in enumerate(rows)]
        # Include the complete trial metadata in event logs, as the browser does.
        state['events'] = [{'utc': '2026-11-14T00:00:00Z', 'browser_ms': i,
                            'event': label, 'detail': json.dumps(row)}
                           for i, row in enumerate(rows)
                           for label in ('tone_scheduled', 'rating_saved')]
        route = '/api/sessions/' + session['id']
        self.assertEqual(self.call(route, {'revision': 0, 'state': state})[0], 200)
        self.assertEqual(self.call(route)[1]['state']['schedule'], rows)
        status, body = self.call(route + '/export')
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            import csv
            path = next(n for n in archive.namelist() if n.endswith('behavior/trials.csv'))
            exported = list(csv.DictReader(io.StringIO(archive.read(path).decode('utf-8-sig'))))
            self.assertEqual(len(exported), 72)
            self.assertEqual([r['image_id'] for r in exported], [r['image_id'] for r in rows])

    def test_loudness_ranking_saved_and_exported(self):
        _, session = self.call('/api/sessions', {'participant': 'RANK01'})
        self.assertEqual(session['protocol']['loudness_ranking']['levels_db'], [70, 75, 80, 85, 90])
        assignment = session['state']['ranking']['assignment']
        self.assertEqual(sorted(assignment), list('ABCDE'))
        self.assertEqual(sorted(assignment.values()), [70, 75, 80, 85, 90])
        seed = session['protocol']['schedule_seed']
        self.assertEqual(station.ranking_assignment(seed), assignment)
        mappings = {tuple(station.ranking_assignment(s).values()) for s in range(50)}
        self.assertGreater(len(mappings), 10)
        levels = dict(sorted(assignment.items()))
        route = '/api/sessions/' + session['id']
        state = copy.deepcopy(session['state'])
        state['phase'] = 'ranking'
        self.assertEqual(self.call(route, {'revision': 0, 'state': state})[0], 200)
        bad = dict(state, ranking=['A'])
        self.assertEqual(self.call(route, {'revision': 1, 'state': bad})[0], 400)
        legacy = {key: value for key, value in state.items() if key != 'ranking'}
        self.assertEqual(self.call(route, {'revision': 1, 'state': legacy})[0], 200)
        given = {'A': 1, 'B': 3, 'C': 2, 'D': 4, 'E': 5}
        results = [{'label': label, 'level_db': db, 'gain': round(0.1 * 10 ** ((db - 70) / 20), 6),
                    'audio_path': '/assets/audio/tone_target_70dbSPL_1s.wav', 'expected_rank': (db - 65) // 5,
                    'rank': given[label], 'correct': given[label] == (db - 65) // 5, 'plays': 1}
                   for label, db in levels.items()]
        state['phase'] = 'review'
        state['ranking'] = {'ranks': given, 'plays': {k: 1 for k in given}, 'results': results,
                            'exact_matches': 3, 'spearman_rho': 0.9}
        self.assertEqual(self.call(route, {'revision': 2, 'state': state})[0], 200)
        status, body = self.call(route + '/export')
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            import csv
            path = next(n for n in archive.namelist() if n.endswith('screening/loudness_ranking.csv'))
            rows = list(csv.DictReader(io.StringIO(archive.read(path).decode('utf-8-sig'))))
        self.assertEqual([r['label'] for r in rows], list('ABCDE'))
        self.assertEqual([r['rank'] for r in rows], ['1', '3', '2', '4', '5'])
        self.assertEqual([int(r['level_db']) for r in rows], [levels[label] for label in 'ABCDE'])


FAKE_PSYCHOPY = """
import json, os, sys, time
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
time.sleep(0.6)
folder = Path(os.environ["FAKE_RECORDINGS"]) / (args["--study-id"] + "_run")
folder.mkdir(parents=True)
(folder / "trials.csv").write_text("trial,rating\\n1,4\\n2,5\\n")
(folder / "run.json").write_text(json.dumps({"run_id": folder.name, "status": "completed",
    "web_session_id": args["--web-session-id"], "created_utc": "2026-11-17T00:00:00+00:00",
    "schedule": [{}] * 6, "fullscreen": True, "timing_warnings": [],
    "audio_device_used": {"name": "Test Speaker"}}))
print("fake task done", sys.argv[1:])
"""


class LauncherTests(SessionTests):
    def setUp(self):
        super().setUp()
        import os, sys
        self.recordings = Path(self.directory.name) / "recordings"
        self.recordings.mkdir()
        script = Path(self.directory.name) / "fake_psychopy.py"
        script.write_text(FAKE_PSYCHOPY)
        env = dict(os.environ, FAKE_RECORDINGS=str(self.recordings))
        self.saved = (station.RECORDINGS, station.TASK_SCRIPT, station.psychopy_command)
        station.RECORDINGS, station.TASK_SCRIPT = self.recordings, script
        station.psychopy_command = lambda: ([sys.executable], env)
        station.TASK.clear()

    def tearDown(self):
        if station.TASK:
            station.TASK["process"].wait(10)
        station.RECORDINGS, station.TASK_SCRIPT, station.psychopy_command = self.saved
        station.TASK.clear()
        super().tearDown()

    def wait_for_exit(self, route):
        import time
        for _ in range(100):
            status = self.call(route)[1]
            if status["launcher"]["state"] == "exited":
                return status
            time.sleep(0.1)
        self.fail("fake PsychoPy did not exit")

    def test_launch_links_run_and_blocks_second_launch(self):
        _, session = self.call("/api/sessions", {"participant": "EEG01"})
        _, other = self.call("/api/sessions", {"participant": "EEG02"})
        route = "/api/sessions/" + session["id"] + "/task"
        status, before = self.call(route)
        self.assertEqual((status, before["runs"], before["launcher"]), (200, [], None))
        status, launched = self.call(route, {})
        self.assertEqual(status, 202)
        self.assertEqual(launched["launcher"]["state"], "running")
        self.assertEqual(self.call(route, {})[0], 409)
        self.assertTrue(self.call("/api/sessions/" + other["id"] + "/task")[1]["other_session_running"])
        done = self.wait_for_exit(route)
        self.assertEqual(done["launcher"]["exit_code"], 0)
        self.assertEqual(len(done["runs"]), 1)
        run = done["runs"][0]
        self.assertEqual((run["status"], run["trials_done"], run["trials_total"], run["audio_device"]),
                         ("completed", 2, 6, "Test Speaker"))
        log = Path(done["launcher"]["log"]).read_text()
        self.assertIn("--web-session-id", log)
        self.assertIn(session["id"], log)
        # Runs belonging to another session are not reported here.
        self.assertEqual(self.call("/api/sessions/" + other["id"] + "/task")[1]["runs"], [])

    def test_launch_refused_without_psychopy_or_after_session_end(self):
        _, session = self.call("/api/sessions", {"participant": "EEG03"})
        route = "/api/sessions/" + session["id"]
        station.psychopy_command = lambda: None
        self.assertEqual(self.call(route + "/task", {})[0], 404)
        self.assertFalse(self.call(route + "/task")[1]["psychopy_available"])
        state = session["state"]
        state["phase"] = "aborted"
        self.assertEqual(self.call(route, {"revision": 0, "state": state})[0], 200)
        self.assertEqual(self.call(route + "/task", {})[0], 409)
        self.assertEqual(self.call("/api/sessions/not-a-session/task", {})[0], 400)


if __name__ == "__main__":
    unittest.main()

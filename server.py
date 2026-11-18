"""Local-only NeuroNerds prototype. Requires Python 3.8 or newer."""
import argparse
import csv
import io
import json
import os
import random
import re
import subprocess
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
from station_common import inventory, browser_schedule, SOA_RANGE_S

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
DATA = ROOT / "data"
STIMULI = inventory()
ASSET_ROUTES = {"/assets/manifest.json"} | {item["path"] for item in STIMULI["images"]} | set(STIMULI["audio"].values())
PHASES = {"setup", "screening", "discrimination", "ranking", "review", "psychopy", "main", "complete", "aborted"}
RECORDINGS = ROOT / "recordings"
TASK_SCRIPT = ROOT / "psychopy_pilot.py"
TASK = {}  # The one native PsychoPy process this server launched most recently.


def psychopy_command():
    """Return (argv prefix, environment) for PsychoPy's own Python, or None.

    Set PSYCHOPY_PYTHON to override. The macOS app bundle's interpreter needs
    PYTHONHOME pointing at its Resources folder to find its libraries.
    """
    env = dict(os.environ)
    override = os.environ.get("PSYCHOPY_PYTHON")
    if override:
        return ([override, "-u"], env) if Path(override).is_file() else None
    if sys.platform == "darwin":
        app = Path("/Applications/PsychoPy.app/Contents")
        if (app / "MacOS/python").is_file():
            env["PYTHONHOME"] = str(app / "Resources")
            return [str(app / "MacOS/python"), "-u"], env
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
            candidate = Path(base) / "PsychoPy" / "python.exe"
            if base and candidate.is_file():
                return [str(candidate), "-u"], env
    return None


def task_status(session_id):
    launcher = None
    if TASK.get("session_id") == session_id:
        code = TASK["process"].poll()
        launcher = {"state": "running" if code is None else "exited", "exit_code": code,
                    "started_at": TASK["started_at"], "log": TASK["log"]}
    runs = []
    for run_file in RECORDINGS.glob("*/run.json"):
        try:
            run = json.loads(run_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue  # The task may be mid-write; the next poll will read it.
        if not isinstance(run, dict) or run.get("web_session_id") != session_id:
            continue
        try:
            with (run_file.parent / "trials.csv").open(newline="", encoding="utf-8") as stream:
                done = sum(1 for _ in csv.DictReader(stream))
        except OSError:
            done = 0
        runs.append({"run_id": run.get("run_id"), "status": run.get("status"),
                     "created_utc": run.get("created_utc"), "trials_done": done,
                     "trials_total": len(run.get("schedule", [])),
                     "audio_device": (run.get("audio_device_used") or run.get("audio_device_requested") or {}).get("name"),
                     "fullscreen": run.get("fullscreen"), "timing_warnings": len(run.get("timing_warnings", [])),
                     "eeg_recording_verified": run.get("eeg_recording_verified", False),
                     "error": run.get("error"), "folder": str(run_file.parent)})
    runs.sort(key=lambda item: item["created_utc"] or "")
    other = TASK.get("session_id") not in (None, session_id) and TASK["process"].poll() is None
    return {"launcher": launcher, "runs": runs, "psychopy_available": psychopy_command() is not None,
            "other_session_running": other}


def launch_task(path, session):
    if session["state"]["phase"] in {"complete", "aborted"}:
        raise PermissionError("This session has ended. Start a new session to run the task.")
    if TASK and TASK["process"].poll() is None:
        raise PermissionError("A PsychoPy task is already running. Finish or close it first.")
    command = psychopy_command()
    if command is None:
        raise FileNotFoundError("PsychoPy was not found. Install it in Applications or set PSYCHOPY_PYTHON.")
    argv, env = command
    log_dir = path.parent / "psychopy"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / ("launch_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".log")
    # Fixed script and validated arguments only; nothing from the request body is executed.
    with log_path.open("wb") as log:
        process = subprocess.Popen(argv + [str(TASK_SCRIPT), "--study-id", session["participant"],
                                           "--web-session-id", session["id"]],
                                   cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
    TASK.clear()
    TASK.update(process=process, session_id=session["id"], started_at=now(), log=str(log_path))
    return task_status(session["id"])


RANKING_LETTERS = ("A", "B", "C", "D", "E")
RANKING_LEVELS_DB = (70, 75, 80, 85, 90)


def ranking_assignment(seed):
    """Shuffle which level each letter plays, reproducibly from the session seed."""
    levels = list(RANKING_LEVELS_DB)
    random.Random(str(seed) + "-ranking").shuffle(levels)
    return dict(zip(RANKING_LETTERS, levels))


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def locate(session_id):
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{12}", session_id):
        raise ValueError("Invalid session identifier")
    matches = list(DATA.glob("sub-*/ses-" + session_id + "/session.json"))
    if len(matches) != 1:
        raise FileNotFoundError("Session not found")
    return matches[0]


def csv_bytes(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return output.getvalue().encode("utf-8-sig")


def export_files(session):
    metadata = {key: value for key, value in session.items() if key != "state"}
    state = session["state"]
    metadata.update({"status": state["phase"], "simulation": True,
                     "eeg_recorded": False, "audio_calibrated": False})
    screening = [{"question": key, "response": value}
                 for key, value in state.get("screening", {}).items()]
    encode = lambda value: json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
    return {
        "manifest.json": encode(metadata),
        "session.json": encode(session),
        "screening/questionnaire.csv": csv_bytes(screening, ["question", "response"]),
        "screening/discrimination_trials.csv": csv_bytes(state.get("discrimination", []),
            ["trial", "attempt_id", "delta_db", "louder_interval", "response", "correct", "rt_ms"]),
        "screening/loudness_ranking.csv": csv_bytes((state.get("ranking") or {}).get("results", []),
            ["label", "level_db", "gain", "audio_path", "expected_rank", "rank", "correct", "plays"]),
        "screening/threshold.json": encode({"estimate_delta_db": state.get("threshold"),
            "method": "12-trial demo, weighted up-down staircase (start 6 dB; -0.5 dB per correct, +1.5 dB per error; 0.5-10 dB); last four tested deltas averaged",
            "validated": False, "eligibility": "not_assessed",
            "review": state.get("review", {})}),
        "behavior/trials.csv": csv_bytes(state.get("ratings", []),
            ["trial", "attempt_id", "image_id", "image_source", "valence", "level", "gain", "image_path", "image_sha256", "audio_path", "rating", "rt_ms"]),
        "events/events.csv": csv_bytes(state.get("events", []),
            ["utc", "browser_ms", "event", "detail"]),
        "protocol.json": encode(session["protocol"]),
        "eeg/README.txt": b"SIMULATION ONLY. No EEG was acquired. Browser event times are unvalidated.\n",
    }


def materialize(path, session):
    for name, content in export_files(session).items():
        if name == "session.json":
            continue  # The canonical checkpoint is already written atomically.
        target = path.parent / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(str(temporary), str(target))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def send_json(self, value, status=200):
        body = json.dumps(value, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") not in {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}:
            return self.send_json({"error": "Use the localhost address printed by the app."}, 403)
        route = urlparse(self.path).path
        try:
            with LOCK:
                if route == "/api/status":
                    return self.send_json({"mode": "demo", "data_directory": str(DATA),
                        "psychopy": "native_launcher", "psychopy_available": psychopy_command() is not None,
                        "eeg": "not_monitored", "version": "0.4.0"})
                if route == "/api/stimuli":
                    return self.send_json(STIMULI)
                if route == "/api/sessions":
                    sessions = []
                    for path in sorted(DATA.glob("sub-*/ses-*/session.json"), reverse=True):
                        try:
                            item = json.loads(path.read_text(encoding="utf-8"))
                            sessions.append({"id": item["id"], "participant": item["participant"],
                                "created_at": item["created_at"], "phase": item["state"]["phase"]})
                        except (ValueError, KeyError, OSError):
                            continue
                    sessions.sort(key=lambda item: item["created_at"], reverse=True)
                    return self.send_json(sessions[:30])
                if route.startswith("/api/sessions/"):
                    parts = route.strip("/").split("/")
                    path = locate(parts[2])
                    session = json.loads(path.read_text(encoding="utf-8"))
                    if len(parts) == 3:
                        return self.send_json(session)
                    if len(parts) == 4 and parts[3] == "task":
                        return self.send_json(task_status(session["id"]))
                    if len(parts) == 4 and parts[3] == "export":
                        materialize(path, session)
                        output = io.BytesIO()
                        prefix = path.parent.parent.name + "/" + path.parent.name + "/"
                        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                            for name, content in export_files(session).items():
                                archive.writestr(prefix + name, content)
                        body = output.getvalue()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header("Content-Disposition", 'attachment; filename="' + path.parent.name + '.zip"')
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    raise FileNotFoundError("Route not found")
            if route.startswith("/api/"):
                raise FileNotFoundError("Route not found")
            # Serve only the app assets; collected data is outside this directory.
            if unquote(route) not in {"/", "/index.html", "/app.js", "/style.css", "/favicon.svg"} | ASSET_ROUTES:
                raise FileNotFoundError("File not found")
            super().do_GET()
        except FileNotFoundError as error:
            self.send_json({"error": str(error)}, 404)
        except (ValueError, KeyError) as error:
            self.send_json({"error": str(error)}, 400)
        except OSError:
            self.send_json({"error": "Could not read or export this session. Check the data folder."}, 500)

    def do_POST(self):
        if self.headers.get("Host") not in {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}:
            return self.send_json({"error": "Use the localhost address printed by the app."}, 403)
        route = urlparse(self.path).path
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            return self.send_json({"error": "Requests must originate from the local app."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 524288:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request must be a JSON object")
            with LOCK:
                if route == "/api/sessions":
                    participant = body.get("participant", "")
                    if not isinstance(participant, str):
                        raise ValueError("Participant ID must be text")
                    participant = participant.strip()
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", participant):
                        raise ValueError("Use 1–32 letters, numbers, underscores or hyphens. Begin with a letter or number.")
                    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
                    seed = int(uuid.uuid4().hex[:8], 16)
                    session = {"id": session_id, "participant": participant, "created_at": now(),
                        "updated_at": now(), "revision": 0, "mode": "demo",
                        "protocol": {"version": "prototype-0.5-72-balanced-jittered-soa-ranking", "tone_hz": 1000, "tone_seconds": 1.0,
                            "ramp_seconds": 0.01, "discrimination_trials": 12, "main_trials": 72,
                            "discrimination_staircase": {"rule": "weighted_up_down", "start_db": 6, "step_down_db": 0.5,
                                "step_up_db": 1.5, "min_db": 0.5, "max_db": 10, "target_percent_correct": 75},
                            "schedule_seed": seed, "max_consecutive_same_level": 2,
                            "image_to_tone_soa_range_s": list(SOA_RANGE_S),
                            "screening_cutoff": None, "image_source": "supplied_finalized_image_set",
                            "audio_source": "supplied_70_90_extended_wav", "main_playback_gain": 0.1,
                            "level_labels": ["70", "90"], "acoustic_calibration_verified": False,
                            "loudness_ranking": {"letters": list(RANKING_LETTERS), "levels_db": list(RANKING_LEVELS_DB),
                                "assignment": "letter-to-level shuffled per session; saved in state.ranking.assignment",
                                "source_audio": STIMULI["audio"]["70"],
                                "gain_rule": "0.1 * 10^((level_db-70)/20)", "unique_ranks": True}},
                        "state": {"phase": "setup", "screening": {}, "checks": {},
                            "discrimination": [], "delta": 6, "threshold": None,
                            "review": {}, "ranking": {"assignment": ranking_assignment(seed), "ranks": {}, "plays": {}}, "schedule": browser_schedule(seed), "ratings": [], "events": [], "activeTask": None}}
                    path = DATA / ("sub-" + participant) / ("ses-" + session_id) / "session.json"
                    path.parent.mkdir(parents=True, exist_ok=False)
                    atomic_json(path, session)
                    materialize(path, session)
                    return self.send_json(session, 201)
                parts = route.strip("/").split("/")
                if route.startswith("/api/sessions/") and len(parts) == 4 and parts[3] == "task":
                    path = locate(parts[2])
                    session = json.loads(path.read_text(encoding="utf-8"))
                    return self.send_json(launch_task(path, session), 202)
                if route.startswith("/api/sessions/") and len(parts) == 3:
                    path = locate(route.rsplit("/", 1)[1])
                    session = json.loads(path.read_text(encoding="utf-8"))
                    if body.get("revision") != session["revision"]:
                        return self.send_json({"error": "This session changed in another tab. Reload to resume its latest checkpoint."}, 409)
                    state = body.get("state")
                    if not isinstance(state, dict) or state.get("phase") not in PHASES:
                        raise ValueError("Invalid session state")
                    for key in ("screening", "checks", "review"):
                        if not isinstance(state.get(key), dict):
                            raise ValueError("Invalid questionnaire or review state")
                    # Optional so that sessions created before the ranking step still save.
                    if "ranking" in state and not isinstance(state["ranking"], dict):
                        raise ValueError("Invalid loudness ranking state")
                    for key in ("discrimination", "ratings", "events", "schedule"):
                        if not isinstance(state.get(key), list) or len(state[key]) > 2000:
                            raise ValueError("Invalid session records")
                    if session["state"]["phase"] in {"complete", "aborted"}:
                        return self.send_json({"error": "This session has ended. Start a new session to repeat the test."}, 409)
                    session.update(state=state, updated_at=now(), revision=session["revision"] + 1)
                    atomic_json(path, session)
                    # The canonical checkpoint remains recoverable if a derived export fails.
                    try:
                        materialize(path, session)
                    except OSError:
                        session["export_warning"] = "Checkpoint saved. Retry ZIP export to regenerate derived files."
                    return self.send_json(session)
                raise FileNotFoundError("Route not found")
        except FileNotFoundError as error:
            self.send_json({"error": str(error)}, 404)
        except PermissionError as error:
            self.send_json({"error": str(error)}, 409)
        except (ValueError, TypeError, KeyError) as error:
            self.send_json({"error": str(error)}, 400)
        except OSError:
            self.send_json({"error": "Saving or launching failed. Check the data directory and PsychoPy install."}, 500)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NeuroNerds local research-flow prototype")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    args = parser.parse_args()
    DATA = args.data_dir.resolve()
    DATA.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("NeuroNerds prototype → http://127.0.0.1:" + str(args.port), flush=True)
    print("Session data → " + str(DATA), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

"""Local-only NeuroNerds prototype. Requires Python 3.8 or newer."""
import argparse
import csv
import io
import json
import os
import re
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
from station_common import inventory

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
DATA = ROOT / "data"
STIMULI = inventory()
ASSET_ROUTES = {"/assets/manifest.json"} | {item["path"] for item in STIMULI["images"]} | set(STIMULI["audio"].values())
PHASES = {"setup", "screening", "discrimination", "review", "main", "complete", "aborted"}


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
        "screening/threshold.json": encode({"estimate_delta_db": state.get("threshold"),
            "method": "12-trial demo, two-down one-up staircase; last four tested deltas averaged",
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
                        "psychopy": "separate_native_pilot", "eeg": "not_monitored", "version": "0.2.0"})
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
                    session = {"id": session_id, "participant": participant, "created_at": now(),
                        "updated_at": now(), "revision": 0, "mode": "demo",
                        "protocol": {"version": "prototype-0.2-supplied-assets", "tone_hz": 1000, "tone_seconds": 1.0,
                            "ramp_seconds": 0.01, "discrimination_trials": 12, "main_trials": 6,
                            "screening_cutoff": None, "image_source": "supplied_finalized_image_set",
                            "audio_source": "supplied_70_90_extended_wav", "main_playback_gain": 0.1,
                            "level_labels": ["70", "90"], "acoustic_calibration_verified": False},
                        "state": {"phase": "setup", "screening": {}, "checks": {},
                            "discrimination": [], "delta": 6, "streak": 0, "threshold": None,
                            "review": {}, "schedule": [], "ratings": [], "events": [], "activeTask": None}}
                    path = DATA / ("sub-" + participant) / ("ses-" + session_id) / "session.json"
                    path.parent.mkdir(parents=True, exist_ok=False)
                    atomic_json(path, session)
                    materialize(path, session)
                    return self.send_json(session, 201)
                if route.startswith("/api/sessions/") and len(route.strip("/").split("/")) == 3:
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
        except (ValueError, TypeError, KeyError) as error:
            self.send_json({"error": str(error)}, 400)
        except OSError:
            self.send_json({"error": "Saving failed. Check the data directory and available disk space."}, 500)


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

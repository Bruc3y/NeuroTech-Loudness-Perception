"""Run in installed PsychoPy Coder. Real MMBT-S writes; Windows owns EEG recording.

Equipment pilot only. File labels do not establish dB SPL. Default playback gain
is 0.1 (-20 dB); no physical stimulus timing or EEG quality is inferred here.
"""
from pathlib import Path
from datetime import datetime, timezone
import csv
import json
import re
import sys
import wave

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from station_common import make_schedule, inventory


def main():
    from psychopy import prefs
    prefs.hardware["audioLib"] = ["ptb"]
    from psychopy import core, event, gui, sound, visual
    import numpy as np
    import psychtoolbox as ptb
    import serial
    from serial.tools import list_ports

    print("Serial ports:", [(p.device, p.description) for p in list_ports.comports()])
    config = {"Study ID": "SELFTEST", "MMBT-S port": "/dev/cu.usbmodem101",
              "Trial count": ["6", "72"], "Playback gain (0.01 to 1)": "0.1",
              "MMBT-S in S mode and trigger cable connected": False,
              "Windows raw EEG recording will be started before SPACE": False,
              "Audio output at a low comfortable volume": False}
    if not gui.DlgFromDict(config, title="NeuroNerds: worn EEG equipment pilot").OK:
        return
    for name in list(config)[4:]:
        if not config[name]:
            raise ValueError("Complete each equipment check before running.")
    participant = config["Study ID"].strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", participant):
        raise ValueError("Study ID: use letters, numbers, underscores or hyphens.")
    port_name = config["MMBT-S port"].strip()
    if not (port_name.startswith("/dev/cu.") or re.fullmatch(r"COM[0-9]+", port_name)):
        raise ValueError("Enter the identified MMBT-S serial port.")
    gain = float(config["Playback gain (0.01 to 1)"])
    if not 0.01 <= gain <= 1.0:
        raise ValueError("Playback gain must be between 0.01 and 1.")
    run_id = participant + "_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder = ROOT / "recordings" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    seed = int(datetime.now().timestamp() * 1000000)
    schedule = make_schedule(int(config["Trial count"]), seed)
    expected = [12, 15] + [code for row in schedule for code in
                            (row["image_code"], row["tone_code"], 10, 11)] + [13]
    metadata = {"run_id": run_id, "purpose": "worn_equipment_pilot", "seed": seed,
                "created_utc": datetime.now(timezone.utc).isoformat(), "config": config,
                "playback_gain": gain, "status": "prepared", "eeg_recording_verified": False,
                "acoustic_calibration_verified": False, "physical_onsets_measured": False,
                "baseline_seconds": 30, "tone_seconds": 1, "image_lead_seconds": 1,
                "pulse_requested_seconds": 0.05, "schedule": schedule,
                "expected_nonzero_codes": expected,
                "codes": {"1/2/3": "image: pleasant/neutral/unpleasant",
                          "4/5": "pleasant tone: 70/90 file label",
                          "6/7": "neutral tone: 70/90 file label",
                          "8/9": "unpleasant tone: 70/90 file label",
                          "10": "rating prompt", "11": "rating response",
                          "12": "run start", "13": "run complete",
                          "14": "abort/error", "15": "eyes-open baseline start"},
                "audio_verification": json.loads((ROOT / "web/assets/audio/verification.json").read_text())}

    def save_metadata():
        (folder / "run.json").write_text(json.dumps(metadata, indent=2))

    save_metadata()
    port = win = None
    tones = {}
    active_trial = 0
    pulse_started = None
    events_file = (folder / "events.csv").open("x", newline="")
    trials_file = (folder / "trials.csv").open("x", newline="")
    events_writer = csv.DictWriter(events_file, ["trial", "event", "code", "utc", "psychopy_s",
                                                "ptb_s", "scheduled_audio_ptb_s", "value"])
    events_writer.writeheader()
    trial_fields = ["trial", "image_id", "valence", "level_label", "image_sha256",
                    "audio_file", "playback_gain", "rating", "response_time_s"]
    trial_writer = csv.DictWriter(trials_file, trial_fields)
    trial_writer.writeheader()

    def log(label, code="", scheduled="", value=""):
        events_writer.writerow({"trial": active_trial, "event": label, "code": code,
                                "utc": datetime.now(timezone.utc).isoformat(),
                                "psychopy_s": core.getTime(), "ptb_s": ptb.GetSecs(),
                                "scheduled_audio_ptb_s": scheduled, "value": value})
        events_file.flush()

    def reset():
        nonlocal pulse_started
        if port is not None:
            if port.write(bytes([0])) != 1:
                raise IOError("MMBT-S zero write failed")
            log("reset_write_returned", 0)
        pulse_started = None

    def mark(code, label, scheduled="", value=""):
        nonlocal pulse_started
        if pulse_started is not None:
            raise RuntimeError("A preceding trigger was not reset")
        log(label + "_write_requested", code, scheduled, value)
        if port.write(bytes([code])) != 1:
            raise IOError("MMBT-S write failed")
        pulse_started = core.getTime()
        log(label + "_write_returned", "", scheduled, value)

    def tick():
        if event.getKeys(keyList=["escape"]):
            raise KeyboardInterrupt
        if pulse_started is not None and core.getTime() - pulse_started >= 0.050:
            reset()

    def phase(draw, duration, code=None, label="", value=""):
        phase_clock = core.Clock()
        first = True
        while first or phase_clock.getTime() < duration:
            tick()
            draw()
            if first:
                win.callOnFlip(phase_clock.reset)
                if code is not None:
                    win.callOnFlip(mark, code, label, "", value)
            win.flip()
            first = False
        # Complete any final pulse before the next phase.
        while pulse_started is not None:
            tick()
            core.wait(0.001)

    try:
        # Decode the supplied 24-bit PCM and duplicate the mono signal to stereo.
        for level, asset in inventory()["audio"].items():
            with wave.open(str(ROOT / "web" / asset.lstrip("/"))) as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) != (1, 3, 48000, 48000):
                    raise ValueError("Expected one-second, mono, 24-bit, 48-kHz audio")
                a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.uint8).reshape(-1, 3).astype(np.int32)
                x = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
                waveform = ((x ^ 0x800000) - 0x800000).astype(float) / 8388608
            tones[level] = sound.Sound(np.column_stack((waveform, waveform)), sampleRate=48000,
                                       stereo=True, hamming=False, volume=gain)
        if "ptb" not in type(tones["70"]).__module__:
            raise RuntimeError("PTB sound backend is required for this pilot")
        metadata["sound_backend"] = type(tones["70"]).__module__
        port = serial.Serial(port_name, baudrate=9600, bytesize=8, parity="N", stopbits=1,
                             timeout=0, write_timeout=1, xonxoff=False, rtscts=False, dsrdtr=False)
        port.rts = False
        port.dtr = True
        core.wait(2.0)
        reset()
        win = visual.Window(size=(1100, 780), fullscr=False, units="height", color=-0.3)
        text = visual.TextStim(win, height=0.033, wrapWidth=1.3)
        fixation = visual.TextStim(win, text="+", height=0.07)
        patch = visual.Rect(win, pos=(0.64, 0.44), size=(0.055, 0.055), fillColor="white", lineColor="white")
        pictures = {}
        for row in schedule:
            path = ROOT / "web" / row["path"].lstrip("/")
            # Preserve aspect ratio inside a fixed display area.
            from PIL import Image
            with Image.open(path) as im:
                width, height = im.size
            ratio = width / height
            h = min(0.70, 1.16 / ratio)
            pictures[row["image_id"]] = visual.ImageStim(win, image=str(path), size=(h * ratio, h))

        text.text = (f"Run: {run_id}\n\nOn WINDOWS: start raw EEG + Trigger recording.\n"
                     "Use this run name; leave 5 seconds before SPACE.\n\n"
                     "First: 30 seconds looking quietly at +.\n"
                     f"Then: {len(schedule)} image + one-second tone trials.\n"
                     "Rate loudness with keys 1-7. ESC stops and saves.\n\n"
                     "SPACE begins. No sound plays before you begin.")
        text.draw()
        win.flip()
        print("READY. Windows recording name:", run_id, flush=True)
        print("Output folder:", folder, flush=True)
        if "escape" in event.waitKeys(keyList=["space", "escape"]):
            raise KeyboardInterrupt
        metadata["status"] = "running"
        save_metadata()
        phase(fixation.draw, 1.0, 12, "run_start")
        phase(fixation.draw, 30.0, 15, "eyes_open_baseline")
        for row in schedule:
            active_trial = row["trial"]
            phase(fixation.draw, 1.0)
            picture = pictures[row["image_id"]]
            # Schedule audio ahead of the expected flip, allowing PTB preparation.
            # The trigger remains a software request; external acoustic timing is unmeasured.
            phase(picture.draw, 0.85, row["image_code"], "image", row["image_id"])
            target = win.getFutureFlipTime(targetTime=0.15, clock="ptb")
            tones[row["level_label"]].play(when=target)
            log("audio_scheduled", "", target, row["audio_file"])
            while win.getFutureFlipTime(clock="ptb") < target - 0.0001:
                tick()
                picture.draw()
                win.flip()
            onset_clock = core.Clock()
            picture.draw()
            patch.draw()
            win.callOnFlip(mark, row["tone_code"], "tone", target, row["audio_file"])
            win.callOnFlip(onset_clock.reset)
            win.flip()
            # Retain the picture until the scheduled one-second buffer is over.
            while ptb.GetSecs() < target + 1.12:
                tick()
                picture.draw()
                win.flip()
            phase(fixation.draw, 0.25)
            text.text = "How loud was the tone?\n\n1 = very quiet                 7 = very loud\n\nPress a number from 1 to 7."
            event.clearEvents()
            response_clock = core.Clock()
            text.draw()
            win.callOnFlip(mark, 10, "rating_prompt")
            win.callOnFlip(response_clock.reset)
            win.flip()
            keys = []
            while not keys:
                tick()
                keys = event.getKeys(keyList=list("1234567"), timeStamped=response_clock)
                core.wait(0.001)
            key, rt = keys[0]
            # A very fast response must not truncate or overwrite the prompt pulse.
            while pulse_started is not None:
                tick()
                core.wait(0.001)
            core.wait(0.050)  # explicit low interval before the response pulse
            mark(11, "rating_response", value=key)
            trial_writer.writerow({"trial": active_trial, "image_id": row["image_id"],
                                   "valence": row["valence"], "level_label": row["level_label"],
                                   "image_sha256": row["sha256"], "audio_file": row["audio_file"],
                                   "playback_gain": gain, "rating": key, "response_time_s": rt})
            trials_file.flush()
            phase(fixation.draw, 1.0)
        active_trial = 0
        phase(fixation.draw, 1.0, 13, "run_complete")
        metadata["status"] = "completed"
        save_metadata()
        text.text = ("Pilot complete.\n\nOn WINDOWS: wait 5 seconds, then stop and save.\n"
                     "Keep the raw CSV + recording log with this run's Mac folder.\n\n"
                     "Saved EEG and markers still need verification.\nSPACE closes.")
        text.draw()
        win.flip()
        event.waitKeys(keyList=["space", "escape"])
    except BaseException as error:
        metadata["status"] = "aborted" if isinstance(error, KeyboardInterrupt) else "error"
        metadata["error"] = repr(error)
        if port is not None:
            try:
                reset()
                core.wait(0.050)
                mark(14, "abort")
                core.wait(0.050)
            except Exception:
                pass
        print("Pilot stopped:", repr(error), flush=True)
        if not isinstance(error, KeyboardInterrupt):
            raise
    finally:
        for tone in tones.values():
            tone.stop()
        if port is not None:
            try:
                reset()
            finally:
                port.close()
        if win is not None:
            win.close()
        events_file.close()
        trials_file.close()
        metadata["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save_metadata()
        print("Saved:", folder, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        (ROOT / "last_startup_error.txt").write_text(traceback.format_exc())
        raise

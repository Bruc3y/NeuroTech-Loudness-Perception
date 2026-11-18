"""Run in installed PsychoPy Coder. Real MMBT-S writes; Windows owns EEG recording.

Equipment pilot only. File labels do not establish dB SPL. Default playback gain
is 0.1 (-20 dB); no physical stimulus timing or EEG quality is inferred here.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import json
import hashlib
import re
import sys
import wave

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from station_common import make_schedule, inventory, SOA_RANGE_S, ITI_RANGE_S

ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}"
SESSION_PATTERN = r"[0-9]{8}T[0-9]{6}-[a-f0-9]{12}"


def parse_args(argv=None):
    """Optional prefill from the website launcher. Coder/Runner runs pass none."""
    parser = argparse.ArgumentParser(description="NeuroNerds native PsychoPy task")
    parser.add_argument("--study-id", default="SELFTEST")
    parser.add_argument("--web-session-id", default=None)
    args, _ = parser.parse_known_args(argv)
    if not re.fullmatch(ID_PATTERN, args.study_id):
        raise ValueError("--study-id: use letters, numbers, underscores or hyphens.")
    if args.web_session_id is not None and not re.fullmatch(SESSION_PATTERN, args.web_session_id):
        raise ValueError("--web-session-id is not a station session identifier.")
    return args


def output_devices():
    """PTB output devices as (dialog label, exact device name, details)."""
    import psychtoolbox.audio as ptb_audio
    devices = []
    for profile in ptb_audio.get_devices():
        if profile.get("NrOutputChannels", 0) > 0:
            name = profile["DeviceName"]
            label = f"{name}  [{int(profile['NrOutputChannels'])} ch, {int(profile.get('DefaultSampleRate', 0))} Hz]"
            devices.append((label, name, {"name": name, "index": int(profile["DeviceIndex"]),
                                          "channels": int(profile["NrOutputChannels"]),
                                          "default_sample_rate": profile.get("DefaultSampleRate"),
                                          "host_api": profile.get("HostAudioAPIName")}))
    if not devices:
        raise RuntimeError("No audio output devices were found.")
    return devices


def main():
    args = parse_args()
    from psychopy import prefs
    prefs.hardware["audioLib"] = ["ptb"]
    # sound is imported only after the output device is chosen and pinned below.
    from psychopy import core, event, gui, visual
    import numpy as np
    import psychtoolbox as ptb
    import serial
    from serial.tools import list_ports

    print("Serial ports:", [(p.device, p.description) for p in list_ports.comports()])
    devices = output_devices()
    print("Audio outputs:", [label for label, _, _ in devices])
    checks = ["MMBT-S in S mode and trigger cable connected",
              "Windows raw EEG recording will be started before SPACE",
              "Audio output at a low comfortable volume"]
    config = {"Study ID": args.study_id, "MMBT-S port": "/dev/cu.usbmodem101",
              "Trial count": ["6", "72"], "Playback gain (0.01 to 1)": "0.1",
              # PsychoPy's "default" audio setting means the FIRST device PTB lists,
              # not the macOS system output, so the device is always chosen here.
              "Audio output (wired; not Bluetooth)": [label for label, _, _ in devices],
              "Full screen (required for data collection)": True,
              **{name: False for name in checks},
              "Publish LSL task markers (requires pylsl)": False}
    if not gui.DlgFromDict(config, title="NeuroNerds: native EEG task", sortKeys=False).OK:
        return
    for name in checks:
        if not config[name]:
            raise ValueError("Complete each equipment check before running.")
    device_label = config["Audio output (wired; not Bluetooth)"]
    audio_device = next(info for label, _, info in devices if label == device_label)
    prefs.hardware["audioDevice"] = [audio_device["name"]]
    from psychopy import sound
    participant = config["Study ID"].strip()
    if not re.fullmatch(ID_PATTERN, participant):
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
                "web_session_id": args.web_session_id,
                "audio_device_requested": audio_device,
                "fullscreen": bool(config["Full screen (required for data collection)"]),
                "max_consecutive_same_level": 2,
                "lsl_markers_enabled": config["Publish LSL task markers (requires pylsl)"],
                "created_utc": datetime.now(timezone.utc).isoformat(), "config": config,
                "playback_gain": gain, "status": "prepared", "eeg_recording_verified": False,
                "acoustic_calibration_verified": False, "physical_onsets_measured": False,
                "baseline_seconds": 30, "tone_seconds": 1,
                "image_to_tone_soa_range_s": list(SOA_RANGE_S),
                "post_response_iti_range_s": list(ITI_RANGE_S),
                "pre_image_fixation_s": 1.0,
                "pulse_requested_seconds": 0.05, "schedule": schedule,
                "expected_nonzero_codes": expected,
                "codes": {"1/2/3": "image: pleasant/neutral/unpleasant",
                          "4/5": "pleasant tone: 70/90 file label",
                          "6/7": "neutral tone: 70/90 file label",
                          "8/9": "unpleasant tone: 70/90 file label",
                          "10": "rating prompt", "11": "rating response",
                          "12": "run start", "13": "run complete",
                          "14": "abort/error", "15": "eyes-open baseline start"},
                "timing_warnings": [], "software_tone_marker_offsets_ms": [],
                "pilot_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "audio_verification": json.loads((ROOT / "web/assets/audio/verification.json").read_text())}
    (folder / "pilot_script_used.py").write_bytes(Path(__file__).read_bytes())

    def save_metadata():
        (folder / "run.json").write_text(json.dumps(metadata, indent=2))

    save_metadata()
    port = win = None
    marker_outlet = None
    lsl_clock = None
    tones = {}
    active_trial = 0
    pulse_started = None
    events_file = (folder / "events.csv").open("x", newline="")
    trials_file = (folder / "trials.csv").open("x", newline="")
    events_writer = csv.DictWriter(events_file, ["trial", "event", "code", "utc", "psychopy_s",
                                                "ptb_s", "scheduled_audio_ptb_s", "value"])
    events_writer.writeheader()
    trial_fields = ["trial", "image_id", "valence", "level_label", "image_sha256",
                    "audio_file", "playback_gain", "soa_s", "iti_s", "rating", "response_time_s"]
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
        requested_lsl_s = lsl_clock() if lsl_clock else None
        if pulse_started is not None:
            raise RuntimeError("A preceding trigger was not reset")
        log(label + "_write_requested", code, scheduled, value)
        if port.write(bytes([code])) != 1:
            raise IOError("MMBT-S write failed")
        pulse_started = core.getTime()
        log(label + "_write_returned", "", scheduled, value)
        if marker_outlet is not None:
            marker_outlet.push_sample([json.dumps({"run_id": run_id, "trial": active_trial,
                "event": label, "code": code, "value": value,
                "meaning": "software_trigger_request", "physical_onset_measured": False})],
                timestamp=requested_lsl_s)

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
        if metadata["lsl_markers_enabled"]:
            from pylsl import StreamInfo, StreamOutlet, local_clock
            lsl_clock = local_clock
            info = StreamInfo("NeuroNerds-Task", "Markers", 1, 0, "string", run_id)
            info.desc().append_child_value("timing", "software trigger requests; not measured acoustic onsets")
            marker_outlet = StreamOutlet(info)
            metadata["lsl_marker_source_id"] = run_id
            save_metadata()
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
        speaker = tones["70"].speaker
        metadata["audio_device_used"] = {"name": speaker.name, "index": speaker.index,
                                         "sample_rate": getattr(speaker, "sampleRateHz", None),
                                         "latency_class": getattr(speaker, "latencyClass", None)}
        if speaker.name != audio_device["name"]:
            raise RuntimeError(f"PsychoPy opened '{speaker.name}', not the chosen '{audio_device['name']}'")
        # Prime both actual PTB tracks silently before the recording start screen.
        # The first play can otherwise spend substantial time initializing audio.
        for tone in tones.values():
            tone.setVolume(0)
            tone.play(when=ptb.GetSecs() + 0.25)
            core.wait(1.40)
            tone.stop()
            tone.setVolume(gain)
        metadata["silent_audio_warmup_completed"] = True
        port = serial.Serial(port_name, baudrate=9600, bytesize=8, parity="N", stopbits=1,
                             timeout=0, write_timeout=1, xonxoff=False, rtscts=False, dsrdtr=False)
        port.rts = False
        port.dtr = True
        core.wait(2.0)
        reset()
        win = visual.Window(size=(1100, 780), fullscr=metadata["fullscreen"], units="height",
                            color=-0.3, waitBlanking=True)
        win.mouseVisible = False
        frame_rate = win.getActualFrameRate(nIdentical=20, nMaxFrames=240, nWarmUpFrames=20)
        metadata["measured_frame_rate_hz"] = frame_rate
        if frame_rate is None:
            metadata["timing_warnings"].append({"trial": 0, "reason": "frame rate could not be measured stably"})
        save_metadata()
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
            # Prime texture drawing in the back buffer, without showing the image.
            pictures[row["image_id"]].draw()
            win.clearBuffer()

        text.text = (f"Run: {run_id}\n\nOn WINDOWS: start raw EEG + Trigger recording.\n"
                     "Use this run name; leave 5 seconds before SPACE.\n\n"
                     "First: 30 seconds looking quietly at +.\n"
                     f"Then: {len(schedule)} image + one-second tone trials.\n"
                     f"Audio: {audio_device['name']}\n"
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
            picture.draw()
            win.callOnFlip(mark, row["image_code"], "image", "", row["image_id"])
            win.flip()
            # Schedule immediately after image onset, a jittered 1-2 s ahead (row["soa_s"]),
            # so the tone cannot be anticipated from a fixed interval.
            # The trigger remains a software request; acoustic timing is unmeasured.
            target = win.getFutureFlipTime(targetTime=row["soa_s"], clock="ptb")
            tones[row["level_label"]].play(when=target)
            log("audio_scheduled", "", target, f'{row["audio_file"]}; soa_s={row["soa_s"]}')
            while win.getFutureFlipTime(clock="ptb") < target - 0.0001:
                tick()
                picture.draw()
                win.flip()
            onset_clock = core.Clock()
            picture.draw()
            patch.draw()
            def tone_marker():
                offset_ms = (ptb.GetSecs() - target) * 1000
                metadata["software_tone_marker_offsets_ms"].append({"trial": active_trial, "offset_ms": offset_ms})
                if abs(offset_ms) > 20:
                    metadata["timing_warnings"].append({"trial": active_trial,
                        "reason": "tone marker request differed from scheduled audio by over 20 ms", "offset_ms": offset_ms})
                mark(row["tone_code"], "tone", target, row["audio_file"])

            win.callOnFlip(tone_marker)
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
                                   "playback_gain": gain, "soa_s": row["soa_s"], "iti_s": row["iti_s"],
                                   "rating": key, "response_time_s": rt})
            trials_file.flush()
            phase(fixation.draw, row["iti_s"])
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

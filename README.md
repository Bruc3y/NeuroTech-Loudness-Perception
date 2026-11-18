# NeuroNerds loudness station

## November 17 update: launch PsychoPy from the website, timing changes

- **Launch from the website.** At the Researcher review step (or before the first browser trial), the **EEG session** panel has a **Launch PsychoPy task** button. The local server starts `psychopy_pilot.py` with PsychoPy's own Python (`/Applications/PsychoPy.app`; set `PSYCHOPY_PYTHON` to override), prefilled with the participant ID and linked to the web session. PsychoPy still shows its setup dialog and equipment checks, runs full screen, and sends the Trigger Hub markers. The panel shows progress; when the run completes, **Finish session with this run** links it to the web session. Launch output is logged in `data/sub-*/ses-*/psychopy/`. DSI-Streamer on Windows is still started/stopped by hand. Only one PsychoPy task runs at a time; the browser main task remains a no-trigger simulation.
- **Jittered timing.** Image-to-tone onset is drawn uniformly from 1.0–2.0 s per trial; post-response fixation from 0.5–1.5 s (plus the fixed 1 s pre-image fixation). Values are reproducible from the run seed without changing the image/level order for that seed.
- **Audio output is chosen explicitly.** PsychoPy's "default" audio setting uses the first device PTB lists (here "External Headphones"), not the macOS system output. The dialog now lists output devices; the run aborts if PsychoPy opens a different one. The chosen and opened device are saved in run.json.
- **Full screen** is on by default (windowed timing on macOS is less reliable); the mouse is hidden and the measured frame rate is saved.
- These changes have not yet been tested with the headset. Run a fresh six-trial pilot and check it with `check_pilot_recording.py` before collecting participants.

Prepared November 13, 2026. This working copy contains all 72 supplied images and the two supplied 70/90-labeled tones extended to one second. Original source files remain unchanged. The Master Doc was used as project context; its meeting action items were not treated as instructions. The user's explicit 70/90 levels, one-second duration and 72-image instructions govern this revision; the latest meeting notes also specify 24 images per category split 12/12.

## Start the website

The prepared website runs at http://127.0.0.1:8766 while its server is open. To restart, double-click start-mac.command; on Windows use start-windows.bat. Both use port 8766.

The home page offers a gallery with 24 pleasant, 24 neutral, and 24 unpleasant images. Each NEW browser session presents all 72 images exactly once, with 12 per category at each audio level. A saved random seed reproduces the order; neither level occurs three times consecutively. Existing sessions keep their previously saved schedules. Main trials play the actual extended WAVs at playback gain 0.1. The station-check and discrimination portions synthesize 1,000 Hz, one-second tones. After discrimination, a loudness-ranking step plays five tones labelled A–E at the 70, 75, 80, 85 and 90 dB labels. Which letter plays which level is shuffled for each session and reproducible from the session seed. It is saved in `state.ranking.assignment`, and levels are not shown to the participant. The participant ranks them 1 (quietest) to 5 (loudest), using each rank once, and the result appears at the researcher review. All five play the 70 dB WAV at gain 0.1 × 10^((level − 70)/20). The supplied 90 dB file is exactly that file at +20 dB, so the 70 and 90 levels match main-task playback. Each tone is scored against its true rank by level. Results are saved in the checkpoint and exported as `screening/loudness_ranking.csv`. Session checkpoints and ZIP exports include image identity, file hashes, audio path, relative level, rating, and timing requests. The browser records behavior; it does not operate PsychoPy, send hardware triggers, or acquire EEG. Its screening remains illustrative.

## LSL and the recommended architecture

See [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md) for the PsychoPy/Pygame comparison, Windows acquisition plus Mac task setup, LSL instructions, marker definitions, and remaining integration work. `lsl_probe.py` discovers and checks existing outlets; it does not open the headset or record an experiment.

The native pilot now offers an optional **Publish LSL task markers** checkbox. It requires `pylsl` and `liblsl` in the Python environment actually running PsychoPy. It publishes `NeuroNerds-Task` alongside its existing serial pulses. The browser has no EEG/LSL or task-launch bridge. The original source prototype is untouched.

## First worn EEG pilot

1. Keep the headset and Bluetooth antenna connected to Windows DSI-Streamer. Keep the MMBT-S USB connected to the Mac and its supported trigger cable connected to the DSI-7. The user confirmed S/Simple mode and the connected trigger route in this chat.
2. Follow the manufacturer's sensor/reference contact checks on the wearer. Use the working Windows recording configuration and save raw CSV with the Trigger channel. The previous off-head 15-pulse test already passed; it did not establish scalp signal quality.
3. In PsychoPy **Runner**, choose **File > Open** and select **Start_Pilot.psyrun**. Select psychopy_pilot.py and click Run. Runner's Open command opens a task list, not a Python script. Alternatively, switch to Coder, then open psychopy_pilot.py and Run.
4. Choose six trials, playback gain 0.1, and the identified MMBT-S port (/dev/cu.usbmodem101 in this setup). Confirm the checks. Turn down the intended audio output and use a comfortable volume. Avoid Bluetooth audio for the eventual timing validation.
5. Leave the pilot waiting at its SPACE screen. On Windows, start raw EEG recording with the displayed run name. Wait five seconds; then press SPACE on the Mac.
6. Look quietly at the fixation for the 30-second eyes-open baseline. The six trials cover all three image categories at both supplied file levels. Each image appears 1.0–2.0 s (randomly jittered per trial, saved in run.json and trials.csv) before its one-second tone. Press 1-7 for perceived loudness. ESC stops and saves a partial run.
7. After the completion screen, leave five seconds of EEG, then stop/save on Windows. Retain its raw CSV, acquisition log and optional filtered/EDF companions. Transfer copies into the matching Mac recording folder or provide their exact paths for review.
8. Verify the raw EEG Trigger channel against run.json and the Mac events.csv before concluding that markers arrived. Inspect baseline traces and signal contact separately. No pass is claimed merely because the Mac serial writes succeeded.

The Mac outputs are in recordings/<study-ID_timestamp>/: run.json (saved randomized schedule, settings, expected codes and status), events.csv (writes, resets, scheduling requests, UTC and separate clock readings), and trials.csv (image, tone, rating, response time). run.json deliberately leaves eeg_recording_verified false until an actual Windows file is examined. A one-second buffer and a sound-request marker do not establish measured physical sound onset.

## Marker map

| Code | Meaning |
|---|---|
| 0 | Reset / no event |
| 1, 2, 3 | Pleasant, neutral, unpleasant image request |
| 4, 5 | Pleasant: 70-label / 90-label tone |
| 6, 7 | Neutral: 70-label / 90-label tone |
| 8, 9 | Unpleasant: 70-label / 90-label tone |
| 10 | Rating prompt |
| 11 | Rating response; response value is in the Mac log |
| 12 | Run start |
| 13 | Run complete |
| 14 | Abort/error |
| 15 | Start of 30-second eyes-open baseline |

Each pulse requests at least 50 ms high, followed by zero. The box must be in S mode; P mode auto-resets after 8 ms. Serial settings are 9600 baud, 8 data bits, no parity, one stop bit, no flow control. All event codes fit the DSI-7's four trigger bits. A complete six-trial recording contains 27 nonzero pulses: 12, 15, then image/tone/10/11 for each of six randomized trials, then 13. Use run.json for the exact order.

The native 72-trial option uses every image exactly once, with 12 images per category assigned to each level and no three identical audio levels consecutively. It is an optional pilot design. The six-trial test and the illustrative browser screening are not a finalized participant protocol.

## Review a Windows export

Use the supplied check_pilot_recording.py with the raw CSV and that run's run.json. It reads metadata before the Time/Trigger header, checks marker order, pulse widths, resets, timestamp continuity and an ADC sequence counter when present, and writes eeg_verification.json into the Mac run folder. Unknown layouts fail explicitly. Confirm the sampling rate against the acquisition log; --sample-rate 300 is appropriate only when the log confirms 300 Hz. Numerical channel ranges and variability are included for review, without certifying scientific signal quality.

## Audio verification

Both WAVs are mono, 24-bit PCM at 48,000 Hz with exactly 48,000 samples (one second). The steady 1,000 Hz portion was extended by inserting 500 copies of a 48-sample middle cycle. The original first and last 250 ms, including both ramps, remain byte-for-byte identical. No silence, time stretching, gain normalization or pitch change was used. The plateau RMS level difference remains approximately 20.0000 dB, and peak values are unchanged. web/assets/audio/verification.json records the measurements and source/output SHA-256 values.

The 70/90 strings identify the supplied files. Actual dB SPL must be measured at the listening position with the final output chain. Default gain 0.1 applies a further 20 dB digital attenuation to both files for the equipment test. Physical trigger-to-sound latency needs external measurement before sound-locked ERP interpretation. PTB scheduling requests are logged separately from serial writes; neither is a measured acoustic onset.

## Supplied Builder file

source/test1.original.psyexp preserves the supplied file. It originally synthesized a tone, used Windows COM10 and contained one fixation/sound routine without an image trial loop. source/test1_mac_1s.psyexp is a separate minimal Mac port/one-second-file example. Use psychopy_pilot.py for the prepared image-and-tone collection workflow.

## Validation status

Prior verification, recorded before this revision: seven automated checks passed: session saving/export/conflicts, completed-session protection, all 72 image URLs and two audio URLs, blocked paths, audio format/duration, balanced schedules without reused images, and synthetic passing/failing trigger recordings. All three gallery categories were inspected in the browser; 24 images loaded in each. PsychoPy 2026.2.5's bundled Python imports the required NumPy, pyserial and Psychtoolbox libraries. The first worn six-trial pilot completed with six saved ratings and 27 matching Mac trigger requests. The supplied raw CSV has now passed saved-file review: 27/27 markers, no detected timestamp/ADC sequence gaps, no saturation, and encouraging baseline traces. See recordings/bruceSELFTEST_20261113_004658_013698/REVIEW_STATUS.md. Its first sound marker request was about 100 ms later than scheduled audio; this revision silently primes both PTB tracks and textures, schedules audio about a second ahead, and logs explicit software timing warnings above 20 ms. External acoustic timing remains unmeasured.

## Manufacturer references

- MMBT-S manual: https://wearablesensing.com/wp-content/uploads/2025/02/MMBT-S_instruction_manual_v2.2-compressed.pdf
- DSI-7 manual: https://wearablesensing.com/wp-content/uploads/2022/09/DSI-7-User-Manual_v0.5.pdf
- Wearable Sensing trigger range: https://wearablesensing.com/faq/
- PsychoPy PTB scheduling: https://psychopy.org/api/sound/playback.html

## November 13 update: 72-trial browser task

New sessions use the server-generated, saved 72-image schedule. It balances the six category/level combinations and limits audio runs to two, using the same scheduler as the native pilot. The browser finishes its checkpoint before displaying an image, waits for image decoding and rendering, then schedules the decoded one-second WAV with a fixed one-second lead. These are browser timing requests, not measured display or acoustic onsets. The image remains during the loudness rating and the next trial begins on a button press.

The questionnaire and 12-comparison staircase remain demonstrations, not the approved ASHA form or a validated eligibility test. The discrimination task intentionally generates adjustable comparison tones; the two supplied fixed WAVs are used for the main task.

Current revision verification: all 11 automated checks passed, including 200 schedule seeds, all stimulus URLs, complete 72-rating save/export with synthetic responses, and unambiguous LSL outlet selection. Browser verification completed all 12 illustrative screening comparisons, two saved main-task ratings, a third playback, pause/resume, refresh recovery and partial-session saving. No EEG or hardware session was run for this update.

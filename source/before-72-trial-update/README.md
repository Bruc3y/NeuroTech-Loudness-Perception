# NeuroNerds loudness station

Prepared November 13, 2026. This working copy contains all 72 supplied images and the two supplied 70/90-labeled tones extended to one second. Original source files remain unchanged. The Master Doc was used as project context; its meeting action items were not treated as instructions. The user's explicit 70/90 choice overrides the document's 70/80 proposal.

## Start the website

The prepared website runs at http://127.0.0.1:8766 while its server is open. To restart, double-click start-mac.command; on Windows use start-windows.bat. Both use port 8766.

The home page offers a gallery with 24 pleasant, 24 neutral, and 24 unpleasant images. The browser walkthrough samples two different images per category, one for each audio level. Main trials play the actual extended WAVs at playback gain 0.1. The station-check and discrimination portions synthesize 1,000 Hz, one-second tones. Session checkpoints and ZIP exports include image identity, file hashes, audio path, relative level, rating, and timing requests. The browser records behavior; it does not operate PsychoPy, send hardware triggers, or acquire EEG. Its screening remains illustrative.

## First worn EEG pilot

1. Keep the headset and Bluetooth antenna connected to Windows DSI-Streamer. Keep the MMBT-S USB connected to the Mac and its supported trigger cable connected to the DSI-7. The user confirmed S/Simple mode and the connected trigger route in this chat.
2. Follow the manufacturer's sensor/reference contact checks on the wearer. Use the working Windows recording configuration and save raw CSV with the Trigger channel. The previous off-head 15-pulse test already passed; it did not establish scalp signal quality.
3. In PsychoPy **Runner**, choose **File > Open** and select **Start_Pilot.psyrun**. Select psychopy_pilot.py and click Run. Runner's Open command opens a task list, not a Python script. Alternatively, switch to Coder, then open psychopy_pilot.py and Run.
4. Choose six trials, playback gain 0.1, and the identified MMBT-S port (/dev/cu.usbmodem101 in this setup). Confirm the checks. Turn down the intended audio output and use a comfortable volume. Avoid Bluetooth audio for the eventual timing validation.
5. Leave the pilot waiting at its SPACE screen. On Windows, start raw EEG recording with the displayed run name. Wait five seconds; then press SPACE on the Mac.
6. Look quietly at the fixation for the 30-second eyes-open baseline. The six trials cover all three image categories at both supplied file levels. Each image appears about one second before its one-second tone. Press 1-7 for perceived loudness. ESC stops and saves a partial run.
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

The 72-trial option uses every image exactly once, with 12 images per category assigned to each level. It is an optional pilot design. The six-trial test and the illustrative browser screening are not a finalized participant protocol.

## Review a Windows export

Use the supplied check_pilot_recording.py with the raw CSV and that run's run.json. It reads metadata before the Time/Trigger header, checks marker order, pulse widths, resets, timestamp continuity and an ADC sequence counter when present, and writes eeg_verification.json into the Mac run folder. Unknown layouts fail explicitly. Confirm the sampling rate against the acquisition log; --sample-rate 300 is appropriate only when the log confirms 300 Hz. Numerical channel ranges and variability are included for review, without certifying scientific signal quality.

## Audio verification

Both WAVs are mono, 24-bit PCM at 48,000 Hz with exactly 48,000 samples (one second). The steady 1,000 Hz portion was extended by inserting 500 copies of a 48-sample middle cycle. The original first and last 250 ms, including both ramps, remain byte-for-byte identical. No silence, time stretching, gain normalization or pitch change was used. The plateau RMS level difference remains approximately 20.0000 dB, and peak values are unchanged. web/assets/audio/verification.json records the measurements and source/output SHA-256 values.

The 70/90 strings identify the supplied files. Actual dB SPL must be measured at the listening position with the final output chain. Default gain 0.1 applies a further 20 dB digital attenuation to both files for the equipment test. Physical trigger-to-sound latency needs external measurement before sound-locked ERP interpretation. PTB scheduling requests are logged separately from serial writes; neither is a measured acoustic onset.

## Supplied Builder file

source/test1.original.psyexp preserves the supplied file. It originally synthesized a tone, used Windows COM10 and contained one fixation/sound routine without an image trial loop. source/test1_mac_1s.psyexp is a separate minimal Mac port/one-second-file example. Use psychopy_pilot.py for the prepared image-and-tone collection workflow.

## Validation status

Seven automated checks passed: session saving/export/conflicts, completed-session protection, all 72 image URLs and two audio URLs, blocked paths, audio format/duration, balanced schedules without reused images, and synthetic passing/failing trigger recordings. All three gallery categories were inspected in the browser; 24 images loaded in each. PsychoPy 2026.2.5's bundled Python imports the required NumPy, pyserial and Psychtoolbox libraries. The first worn six-trial pilot completed with six saved ratings and 27 matching Mac trigger requests. The supplied raw CSV has now passed saved-file review: 27/27 markers, no detected timestamp/ADC sequence gaps, no saturation, and encouraging baseline traces. See recordings/bruceSELFTEST_20261113_004658_013698/REVIEW_STATUS.md. Its first sound marker request was about 100 ms later than scheduled audio; this revision silently primes both PTB tracks and textures, schedules audio about a second ahead, and logs explicit software timing warnings above 20 ms. External acoustic timing remains unmeasured.

## Manufacturer references

- MMBT-S manual: https://wearablesensing.com/wp-content/uploads/2025/02/MMBT-S_instruction_manual_v2.2-compressed.pdf
- DSI-7 manual: https://wearablesensing.com/wp-content/uploads/2022/09/DSI-7-User-Manual_v0.5.pdf
- Wearable Sensing trigger range: https://wearablesensing.com/faq/
- PsychoPy PTB scheduling: https://psychopy.org/api/sound/playback.html

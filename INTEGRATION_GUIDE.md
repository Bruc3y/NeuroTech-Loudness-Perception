# NeuroNerds experiment and EEG integration

Prepared November 13, 2026. Recommendation: use native PsychoPy for the timed experiment, keep the website for participant check-in, questionnaires and session management, and use a DSI acquisition outlet plus LabRecorder for LSL recording.

## What the supplied project materials establish

The Master Docs PDF, pages 66–67, proposes pleasant/neutral/unpleasant OASIS images, hearing screening, alpha-band analysis and possible auditory ERPs. Page 71 updates the design to 24 images per category, with 12 assigned to each of the 70/90 conditions. Pages 72–74 document the working Windows EEG acquisition plus Mac PsychoPy/trigger arrangement. These are project context, not commands to carry out the meeting action items. The user's current request specifies all 72 unique images and one-second tones and governs this update.

Your main task is a 3 × 2 within-participant design:

| Image category | 70-labeled tone | 90-labeled tone | Total |
|---|---:|---:|---:|
| Pleasant | 12 | 12 | 24 |
| Neutral | 12 | 12 | 24 |
| Unpleasant | 12 | 12 | 24 |
| Total | 36 | 36 | 72 |

Each image is presented once per session, paired with one tone. The image-to-level assignment and presentation order are randomized per session. The entire 72-trial sequence has a maximum run of two identical audio levels, including across category changes. This does not impose blocks of 24; categories are mixed. The full order and random seed are saved before the main task. The six-trial native equipment check remains available separately.

For the current browser prototype, the image is rendered, the sound is scheduled one second later, and the WAV lasts one second. The image remains visible for the loudness rating. Response time and the participant's delay before starting the next trial remain variable. A fixed image-to-tone interval does not imply a fixed total trial duration. The native pilot retains its existing fixation, baseline and rating phases, with the same new audio-order constraint.

## What is implemented

- The existing `loudness-station` working copy now uses the full 72-image schedule in new browser sessions. The separate supplied `neuronerds-prototype` folder and existing participant recordings were left intact.
- All 72 working images were checked against the supplied source folders by file hash: 24 per category, exact copies.
- The existing prepared one-second WAVs were checked against the supplied 500-ms originals. Each is mono, 24-bit PCM, 48 kHz, with 48,000 output samples. The prepared files insert 500 steady 48-sample cycles of the 1-kHz tone at the midpoint. Both original halves, including the ramps, are retained exactly; this adds sustained sound rather than silence or a second onset. Source/output hashes and amplitude measurements are in `web/assets/audio/verification.json`.
- Main-task playback uses those WAV files. The station check and adjustable discrimination comparisons still generate tones, because a threshold procedure needs more level differences than just two fixed recordings. This is explicitly documented as a demonstration, not a finalized screening procedure.
- The browser checkpoints image identity/hash, tone path, level assignment, gain, ratings and event logs. Existing saved sessions retain their saved schedules rather than being rewritten.
- The native `psychopy_pilot.py` can optionally publish an LSL task-marker outlet alongside its serial hardware pulses. It does not acquire EEG or start LabRecorder.
- `lsl_probe.py` discovers existing LSL streams and checks for fresh samples without dropping channels or applying EEG filters. It does not create a DSI outlet or save an EEG recording.

Start the updated website with `start-mac.command` or `start-windows.bat` in this folder, then use the address they print (normally port 8766). Restart an already-running server to load this version, and create a new session for the 72-trial design. Test data from this review used a separate temporary directory on port 8770.

## PsychoPy versus Pygame

Use PsychoPy with the PTB audio backend. Your experiment depends on consistent stimulus timing, sound scheduling, event markers and response logs; PsychoPy provides experiment-oriented support for these. Its PTB backend supports audio scheduled ahead against the PTB clock. Actual latency and variability still need measurement on the final equipment.

Pygame can display images, play WAVs and communicate with serial/LSL libraries. It would require more custom work for stimulus scheduling, timing diagnostics, trial management and reproducible data output. Pygame's mixer buffer trades lower latency against possible audio dropouts. Neither native Python package runs inside an ordinary browser page by being embedded in HTML.

If every participant stimulus must appear inside the browser, the alternatives are a JavaScript task (such as PsychoJS or your current Web Audio implementation) and a local hardware bridge. A browser task is a different implementation from native PsychoPy. Its physical timing must be validated independently, especially if interpreting sound-locked N1/P2 responses.

Sources: [PsychoPy audio scheduling](https://psychopy.org/api/sound/playback.html), [Pygame mixer documentation](https://www.pygame.org/docs/ref/mixer.html).

## What LSL does

LSL transports time-stamped streams and supports clock synchronization between computers. `pylsl` is its Python interface; it is not a Bluetooth driver for the DSI-7. An outlet publishes samples, an inlet receives them, and a recorder writes them to disk. Starting an outlet does not by itself save data.

You need an acquisition program that opens the actual headset through Wearable Sensing's interface and publishes its samples. The supported starting point is Wearable Sensing's `dsi2lslGUI` or command-line `dsi2lsl`. Their current published build guide is Windows-specific. A custom Python-only publisher would additionally need the vendor DSI API, Python wrapper and correct native library; simply creating a `pylsl.StreamOutlet` cannot read a headset.

For the setup documented in your PDF, keep acquisition on the Windows laptop and PsychoPy plus the MMBT-S USB connection on the Mac. The computers can exchange LSL streams over the same lab network. The physical trigger cable still connects the MMBT-S route to the DSI-7; LSL does not replace that cable. One acquisition program should own the headset connection at a time.

```text
Mac: native PsychoPy ──USB──> MMBT-S ──supported trigger cable──> DSI-7
        │                                                       │
        │ task markers via pylsl                                │ headset data
        ▼                                                       ▼
     LSL marker outlet                             Windows: dsi2lsl acquisition
        │                                                       │
        └─────────────────────> LabRecorder <────────────────────┘
                                   │
                                   ▼
                            session EEG/markers.xdf

Website: questionnaire + session records
Future local controller: launch task, monitor acquisition/recording, link files
```

Sources: [Wearable Sensing LSL setup](https://support.wearablesensing.com/examples/lsl/gui.html), [vendor outlet and Windows build support](https://github.com/WearableSensing/App-WearableSensing), [DSI API prerequisites](https://support.wearablesensing.com/api/getting_started/index.html), [LabRecorder](https://github.com/labstreaminglayer/App-LabRecorder).

## First LSL test

1. On Windows, identify the headset's actual connection port. Close other software holding that port. Start `dsi2lslGUI`, enter that port, and start acquisition. Follow the vendor's contact/impedance checks. The headset port and the MMBT-S USB port are different devices.
2. In a Python environment with `pylsl` available, run `python lsl_probe.py`. It lists exact stream names, types, source IDs, channel counts and nominal rates. If the package is missing, install it in that environment with `python -m pip install pylsl`. macOS/Linux may additionally require the matching `liblsl` native library; follow the pylsl installation instructions rather than assuming pip alone supplies it.
3. Run `python lsl_probe.py --name "EXACT_NAME_FROM_DISCOVERY" --seconds 10`. If more than one outlet shares the name, add `--source-id "EXACT_SOURCE_ID"`. This checks fresh sample delivery and prints full channel metadata. Do not assume that seven EEG electrodes imply exactly seven stream channels; inspect auxiliary channels, trigger data, labels, units and sampling rate.
4. Send a short test sequence through the existing native hardware task. Inspect which outlet/channel carries the recorded Trigger signal. Some configurations may publish auxiliary/trigger information separately. Preserve and select those streams too. Finding an EEG stream alone does not prove that hardware events are present.
5. If using the new optional native marker output, install `pylsl`/`liblsl` in the Python environment used by PsychoPy, check **Publish LSL task markers**, and leave the task at its SPACE screen. Its `NeuroNerds-Task` outlet uses the run ID as its source ID. The checkbox defaults off so existing serial-only use remains possible.
6. In LabRecorder, select the headset EEG stream, every required auxiliary/trigger stream, and the task-marker stream. Start recording before pressing SPACE in PsychoPy. Use the same run/session identifier for the recording and behavioral files; retain a few seconds before and after the task.
7. Stop and finalize recording after the task. Reopen the XDF with a suitable reader such as `pyxdf`, verify stream/channel identity and units, check sample continuity and compare saved hardware marker counts/order with the task log. For a complete native 72-trial run, the current pilot expects 291 nonzero hardware pulses: run start, baseline, four per trial, then run complete. Aborts have a different ending. Use that run's saved expected sequence as the reference.

The diagnostic preserves raw outlet-clock timestamps and does not align streams. LabRecorder saves clock-offset information in XDF; XDF importers use this to synchronize streams. Do not compare `performance.now()`, `time.time()`, PsychoPy/PTB times and LSL timestamps as though they were one clock. LSL clock synchronization does not automatically remove headset buffering, display lag or speaker latency.

Sources: [pylsl installation](https://github.com/labstreaminglayer/pylsl), [LSL synchronization](https://labstreaminglayer.readthedocs.io/info/time_synchronization.html).

## Interpreting the two supplied example files

The downloaded `mi_task.py` is a motor-imagery presentation and serial-trigger example. Its own description says it removes live EEG/LSL processing. Its structure is useful; its left/right stimuli, port identification, 115,200-baud setting and 10-ms pulse are not your hardware protocol. The MMBT-S manual specifies 9,600 baud. Use the correct identified port and verified pulse/reset behavior.

The supplied `bci_orchestrator.py`, as retrieved for this review, imports `StreamLSL` from `mne_lsl.stream`. `_prepare_stream_signature` resolves an existing outlet by name/type/source ID, connects to it and selects channels. It does not open a DSI Bluetooth/serial connection or publish the headset data. Its channel picking for motor-imagery models is not a raw EEG-plus-trigger recorder. Copying those channel selections could discard your hardware trigger channel. The model configuration's 8–30 Hz settings are also not a justified preprocessing choice for an auditory ERP study.

Sources: [motor-imagery task](https://github.com/Neurotech-BCI/ErrP/blob/main/psychopy/mi_task.py), [orchestrator](https://github.com/Neurotech-BCI/ErrP/blob/main/bci/bci_orchestrator.py#L120).

## Hardware event codes and timing

The DSI-7 accepts four trigger bits, codes 0–15; reserve zero for reset. The existing native pilot uses 1–3 for image category, 4–9 for the six category/level sound conditions, 10 for rating prompt, 11 for response, 12 for run start, 13 for completion, 14 for abort/error, and 15 for baseline. Exact image, trial ID and rating remain in the richer task log and optional JSON LSL markers.

Keep the MMBT-S in the verified Simple/S mode with explicit resets; the current pilot requests 50-ms pulses. Pulse/P mode automatically resets at 8 ms. The mode and pulse route must be verified in acquired data. A successful USB serial write confirms only the write, not that the EEG recorder captured the pulse. Optional task LSL timestamps label software trigger requests, not measured acoustic onsets.

For sound-locked ERP analysis, measure sound onset relative to the trigger using the final output chain and suitable acoustic/electrical measurement. Use a photodiode if you need measured image onset. Keep measured times separate from scheduled/requested times. A one-second WAV specifies duration, not output latency.

Sources: [DSI-7 code range and trigger route](https://support.wearablesensing.com/examples/psychopy/hardware.html), [MMBT-S manual](https://wearablesensing.com/wp-content/uploads/2025/02/MMBT-S_instruction_manual_v2.2-compressed.pdf).

## What website integration still requires

An integrated local station is feasible, but this update does not implement the full controller. The browser currently neither launches PsychoPy nor acquires EEG, operates serial triggers, monitors recording or reads live LSL.

A local Python controller would own session IDs, launch the native PsychoPy process with a saved protocol/schedule, check the exact EEG/trigger outlets and recording readiness, and report progress/errors to the browser. It would halt trial advancement on acquisition or recorder failure, preserve partial files, verify final outputs, and link the XDF, trial CSV, questionnaire and protocol under one session. It should launch a fixed configured task rather than accepting arbitrary browser commands. Browser EEG plots can receive a reduced-rate display feed over a WebSocket while full-resolution data goes directly to the recorder. Keep networking and file operations outside the task's critical stimulus timing.

This lets the website be the single session dashboard while stimuli appear in a native PsychoPy window. If images must appear inside the webpage, choose the browser/JavaScript task route explicitly and validate its hardware bridge and timing instead. Native Pygame does not remove that distinction.

## Decisions needed before participant data collection

- Calibrate the final output chain at the listening position. WAV names and digital amplitude do not establish 70/90 dB SPL. The supplied files differ by about 20 dB digitally; the prototype's common gain of 0.1 applies another 20 dB attenuation to both. Keep output device, OS volume and speaker/headphone settings fixed after calibration.
- Replace the illustrative questionnaire and short staircase with the team's chosen instrument, threshold estimator and review criteria. The current 12 comparisons cannot establish a validated hearing/discrimination threshold or participant eligibility. For the final auditory threshold procedure, use the same calibrated output chain as the main task; a native PsychoPy version would keep the auditory timing and playback path consistent.
- Finalize image exposure, fixation, rating scale, breaks and screening timing. The current one-second image lead is a prototype timing choice, not proof of emotion induction.
- Distinguish picture valence from an individual's actual emotional state. Arousal values above a cutoff are not necessarily matched across categories; consider a manipulation check and review the category distributions before attributing an effect specifically to emotion.
- Counterbalance image-to-sound assignments across participants if needed to separate image-specific effects from level. Within a participant, compare categories at the same audio level, then examine the category-by-level interaction. Twelve trials per cell is the planned count before any EEG artifact rejection, not a guarantee of sufficient usable ERP data.

## Verification limits

This update checks asset identity, audio duration/edge preservation, schedule constraints over 200 seeds, server save/export behavior, and browser screening/playback/pause/resume behavior. The tests also exercise a complete 72-rating export with synthetic responses. No headset, MMBT-S, dsi2lsl or LabRecorder hardware session was run in this review. The optional LSL publisher and diagnostic require an actual outlet/recorder test. Earlier pilot results in the workspace are historical records and do not validate this revision's LSL path.

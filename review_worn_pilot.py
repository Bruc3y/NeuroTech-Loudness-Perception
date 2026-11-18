"""Reproduce the first worn-run review without changing source EEG files.

Uses the project's analysis environment (NumPy, SciPy, Matplotlib).
"""
from pathlib import Path
import csv
import hashlib
import json
import numpy as np
from scipy import signal
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from check_pilot_recording import read_streamer

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "recordings/bruceSELFTEST_20261113_004658_013698"
SOURCE = Path("/Users/brucenguyen/Library/CloudStorage/OneDrive-Personal/Documents/Grad School/USC/Fall 2026/NeuroTech/test1")
RAW = SOURCE / "bruce_midnight_test_legit_raw.csv"
FILTERED = SOURCE / "bruce_midnight_test_legit_filtered.csv"
MANUAL = "https://wearablesensing.com/wp-content/uploads/2022/09/DSI-7-User-Manual_v0.5.pdf"


def main():
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (RAW, FILTERED)}
    verification = json.loads((RUN / "eeg_verification.json").read_text())
    run = json.loads((RUN / "run.json").read_text())
    trials = list(csv.DictReader((RUN / "trials.csv").open()))
    mac = list(csv.DictReader((RUN / "events.csv").open()))
    header, rows, metadata = read_streamer(RAW)
    fh, fr, fm = read_streamer(FILTERED)
    names = ["LE", "F4", "C4", "P4", "P3", "C3", "F3"]
    column = lambda name: np.array([row[header.index(name)] for row in rows])
    times = column("Time")
    codes = column("Trigger").astype(int)
    x = np.column_stack([column(name) for name in names])
    fs = 300
    # A second, vectorized edge detector confirms the verifier's event indices.
    independent_edges = np.flatnonzero((codes != np.r_[0, codes[:-1]]) & (codes != 0))
    assert independent_edges.tolist() == [e["sample_index_zero_based"] for e in verification["events"]]
    expected_event_column = np.zeros(len(codes), dtype=int)
    expected_event_column[independent_edges] = codes[independent_edges]
    event_column_matches = np.array_equal(column("Event"), expected_event_column)
    companion_alignment = {name: np.array_equal(column(name), np.array([r[fh.index(name)] for r in fr]))
                           for name in ("Time", "Trigger", "Event", "ADC_Sequence", "ADC_Status", "Time_Offset")}
    events = verification["events"]
    baseline_start = next(e["csv_time_s"] for e in events if e["code"] == 15)
    baseline_mask = (times >= baseline_start + 1) & (times < baseline_start + 29)
    frequencies, psd = signal.welch(x[baseline_mask], fs=fs, nperseg=1200, noverlap=600, axis=0)

    def power(lo, hi):
        mask = (frequencies >= lo) & (frequencies <= hi)
        return np.trapezoid(psd[mask], frequencies[mask], axis=0)

    sos = signal.butter(4, [1, 40], btype="bandpass", fs=fs, output="sos")
    display = signal.sosfiltfilt(sos, x, axis=0)
    total_power = power(1, 140)
    metrics = {}
    for i, name in enumerate(names):
        metrics[name] = {"raw_min_uV": float(x[:, i].min()), "raw_max_uV": float(x[:, i].max()),
                         "raw_baseline_sd_uV": float(x[baseline_mask, i].std()),
                         "baseline_1_40Hz_rms_uV": float(display[baseline_mask, i].std()),
                         "power_58_62Hz_percent_of_1_140Hz": float(100 * power(58, 62)[i] / total_power[i]),
                         "power_108_112Hz_percent_of_1_140Hz": float(100 * power(108, 112)[i] / total_power[i]),
                         "power_128_132Hz_percent_of_1_140Hz": float(100 * power(128, 132)[i] / total_power[i]),
                         "samples_abs_at_least_9800uV": int((abs(x[:, i]) >= 9800).sum()),
                         "full_record_display_peak_to_peak_uV": float(np.ptp(display[:, i]))}

    alignment = []
    for trial in trials:
        index = int(trial["trial"])
        image, tone, prompt, response = events[2 + (index - 1) * 4:2 + index * 4]
        assert image["code"] == run["schedule"][index - 1]["image_code"]
        assert tone["code"] == run["schedule"][index - 1]["tone_code"]
        alignment.append({"trial": index, "image_id": trial["image_id"], "category": trial["valence"],
                          "level_file_label": trial["level_label"], "playback_gain": trial["playback_gain"],
                          "image_code": image["code"], "image_sample_zero_based": image["sample_index_zero_based"],
                          "image_csv_time_s": image["csv_time_s"], "tone_code": tone["code"],
                          "tone_sample_zero_based": tone["sample_index_zero_based"], "tone_csv_time_s": tone["csv_time_s"],
                          "image_to_tone_marker_s": tone["csv_time_s"] - image["csv_time_s"],
                          "rating": int(trial["rating"]), "response_time_s": float(trial["response_time_s"]),
                          "rating_response_sample_zero_based": response["sample_index_zero_based"],
                          "audio_file": trial["audio_file"]})
    with (RUN / "trials_aligned_to_eeg.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, list(alignment[0]))
        writer.writeheader()
        writer.writerows(alignment)
    with (RUN / "recorded_trigger_events.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, list(events[0]))
        writer.writeheader()
        writer.writerows(events)

    mac_requests = [e for e in mac if e["code"] and int(e["code"]) > 0]
    csv_onsets = np.array([e["csv_time_s"] for e in events])
    mac_onsets = np.array([float(e["ptb_s"]) for e in mac_requests])
    offsets = csv_onsets - mac_onsets
    interval_residual_ms = (offsets - np.median(offsets)) * 1000
    report = {"run_id": run["run_id"], "raw_source": str(RAW), "source_sha256": before,
              "sample_count": len(rows), "sample_rate_hz": fs, "duration_s": len(rows) / fs,
              "reference": "Pz (from acquisition metadata)", "active_recorded_channels": names,
              "all_active_channels_nonsaturated_by_9800uV_screen": not any(v["samples_abs_at_least_9800uV"] for v in metrics.values()),
              "pz_reference_all_zero": bool(np.all(column("Pz") == 0)),
              "independent_trigger_edge_check_matches": True, "event_column_matches_trigger_edges": bool(event_column_matches),
              "marker_delivery_check_passed": verification["marker_delivery_check_passed"],
              "raw_filtered_metadata_channel_alignment": {k: bool(v) for k, v in companion_alignment.items()},
              "adc_status_counts": {str(int(v)): int((column("ADC_Status") == v).sum()) for v in np.unique(column("ADC_Status"))},
              "baseline_review_interval_csv_seconds": [baseline_start + 1, baseline_start + 29],
              "baseline_review_samples": int(baseline_mask.sum()), "channel_metrics": metrics,
              "mac_vs_eeg_constant_offset_residual_ms": interval_residual_ms.tolist(),
              "clock_note": "Mac/Windows clock origins differ. Sequence alignment and interval agreement do not measure stimulus latency.",
              "visual_processing": "Fourth-order Butterworth 1-40 Hz SOS, applied forward/backward offline to the full recording. Plots only; sources unchanged.",
              "spectrum_processing": "Welch PSD of unfiltered baseline, Hann windows, 4 s windows, 50 percent overlap, constant detrend.",
              "signal_assessment": "Encouraging worn scalp-signal pilot with no saturation; artifacts/contact still require review and no impedance readings were supplied.",
              "impedance_drive_interpretation": "Strong 110/130 Hz peaks are consistent with the continuous impedance-monitoring drive signals documented in the DSI-7 manual; not evidence of brain rhythms at those frequencies.",
              "manufacturer_reference": MANUAL, "physical_sound_onsets_measured": False,
              "acoustic_calibration_verified": False, "study_effect_tested": False}
    (RUN / "signal_quality_review.json").write_text(json.dumps(report, indent=2))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "axes.labelcolor": "#334155"})
    colors = plt.get_cmap("tab10")(np.arange(len(names)))
    fig, axes = plt.subplots(2, 1, figsize=(13.5, 7.8), sharex=True,
                             gridspec_kw={"height_ratios": [3.4, 1]}, layout="constrained")
    fig.suptitle("Worn DSI-7 pilot: continuous signals and all 27 recorded markers", fontsize=16, fontweight="bold")
    offsets_y = np.arange(len(names))[::-1] * 90
    for i, name in enumerate(names):
        axes[0].plot(times, display[:, i] + offsets_y[i], lw=0.5, color=colors[i])
    axes[0].set_yticks(offsets_y, names)
    axes[0].set_ylabel("Channels (90 µV spacing; reference Pz)")
    axes[0].set_title("Offline 1–40 Hz display filter; original raw CSV preserved", fontsize=10, loc="left")
    axes[0].axvspan(baseline_start, baseline_start + 30, color="#d1fae5", alpha=0.6, zorder=-5)
    axes[0].text(baseline_start + 15, offsets_y[0] + 45, "30 s eyes-open baseline", ha="center", color="#047857")
    for row in alignment:
        axes[0].axvline(row["tone_csv_time_s"], color="#b45309", lw=0.8, alpha=0.7)
        axes[0].text(row["tone_csv_time_s"], -58, f'T{row["trial"]}', ha="center", color="#92400e", fontsize=9)
    axes[0].set_ylim(-75, offsets_y[0] + 75)
    axes[0].grid(axis="x", alpha=0.15)
    axes[1].step(times, codes, where="post", color="#1e3a5f", lw=0.9)
    for e in events:
        axes[1].text(e["csv_time_s"], e["code"] + 0.5, str(e["code"]), fontsize=7, ha="center")
    axes[1].set_yticks([0, 5, 10, 15])
    axes[1].set_ylim(-0.5, 18)
    axes[1].set_ylabel("Trigger code")
    axes[1].set_xlabel("EEG CSV time (seconds)")
    axes[1].set_xlim(0, times[-1])
    axes[1].grid(alpha=0.15)
    fig.savefig(RUN / "eeg_overview.png", dpi=170)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(13.5, 8), layout="constrained")
    fig.suptitle("Baseline inspection: scalp-range signals and expected device drive peaks", fontsize=15, fontweight="bold")
    window = (times >= baseline_start + 5) & (times < baseline_start + 15)
    for i, name in enumerate(names):
        axes[0].plot(times[window], display[window, i] + offsets_y[i], color=colors[i], lw=0.8)
        axes[1].semilogy(frequencies[1:], psd[1:, i], color=colors[i], lw=0.9, label=name)
    axes[0].set_yticks(offsets_y, names)
    axes[0].set_ylabel("Channels (90 µV spacing)")
    axes[0].set_xlabel("EEG CSV time (seconds)")
    axes[0].set_title("Fixed 10-second baseline window, offline 1–40 Hz display", fontsize=10, loc="left")
    axes[0].grid(alpha=0.15)
    axes[1].set_xlim(1, 140)
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel("Raw baseline PSD (µV²/Hz)")
    axes[1].set_title("Unfiltered baseline spectrum: 110/130 Hz match documented impedance drive frequencies", fontsize=10, loc="left")
    for frequency, label, color in [(60, "60 Hz mains", "#64748b"), (110, "110 Hz drive", "#b45309"), (130, "130 Hz drive", "#b45309")]:
        axes[1].axvline(frequency, ls="--", lw=0.7, color=color)
        axes[1].text(frequency - 1, 0.98, label, rotation=90, va="top", ha="right",
                     transform=axes[1].get_xaxis_transform(), color=color, fontsize=8)
    axes[1].legend(ncol=7, loc="lower left", fontsize=9)
    axes[1].grid(alpha=0.15)
    fig.savefig(RUN / "baseline_inspection.png", dpi=170)
    plt.close(fig)
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (RAW, FILTERED)}
    print(json.dumps({"marker_pass": report["marker_delivery_check_passed"],
                      "source_files_unchanged": True, "channels": metrics,
                      "mac_eeg_interval_residual_range_ms": [float(interval_residual_ms.min()), float(interval_residual_ms.max())]}, indent=2))


if __name__ == "__main__":
    main()

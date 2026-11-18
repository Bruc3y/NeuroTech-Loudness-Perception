"""Compare a DSI-Streamer raw CSV's Trigger channel with one native pilot run.

No filtering or source changes. Unknown CSV layouts fail explicitly. Event
delivery checks are distinct from scalp signal quality and acoustic validation.
"""
import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path


def read_streamer(path):
    metadata, rows, header = [], [], None
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for line, row in enumerate(csv.reader(stream), 1):
            if not any(cell.strip() for cell in row):
                continue
            clean = [cell.strip() for cell in row]
            if header is None:
                names = [cell.lower() for cell in clean]
                if "time" in names and any(name in ("trigger", "trg") for name in names):
                    header = clean
                else:
                    metadata.append(",".join(clean))
                continue
            if len(clean) != len(header):
                raise ValueError(f"Line {line}: wrong column count; inspect the CSV layout")
            try:
                # Streamer appends a free-text Comments column, often empty.
                # Keep it outside numerical validation; never replace missing EEG.
                numbers = [None if header[index].lower() == "comments" else float(cell)
                           for index, cell in enumerate(clean)]
            except ValueError:
                raise ValueError(f"Line {line}: nonnumeric sample; inspect the CSV layout")
            if not all(value is None or math.isfinite(value) for value in numbers):
                raise ValueError(f"Line {line}: non-finite sample")
            rows.append(numbers)
    if header is None or len(rows) < 2:
        raise ValueError("Expected a numeric CSV with Time and Trigger/TRG columns; inspect the export first")
    return header, rows, metadata


def inspect(path, run, sample_rate=None):
    header, rows, metadata = read_streamer(path)
    names = [name.lower() for name in header]
    times = [row[names.index("time")] for row in rows]
    trigger_i = next(i for i, name in enumerate(names) if name in ("trigger", "trg"))
    raw_codes = [row[trigger_i] for row in rows]
    if any(abs(value - round(value)) > 1e-6 or not 0 <= value <= 15 for value in raw_codes):
        raise ValueError("The trigger channel contains values outside integer range 0-15")
    codes = [round(value) for value in raw_codes]
    deltas = [b - a for a, b in zip(times, times[1:])]
    positive = [dt for dt in deltas if dt > 0]
    if not positive:
        raise ValueError("The recording has no increasing timestamps")
    measured_rate = (len(times) - 1) / (times[-1] - times[0]) if times[-1] > times[0] else None
    rate_source = "explicit_argument"
    if sample_rate is None:
        # Four-decimal timestamps make a median interval (0.0033) misleading at 300 Hz.
        # Infer a standard nominal rate only when mean and median are consistent.
        candidates = [128, 250, 256, 300, 500, 512, 1000]
        closest = min(candidates, key=lambda rate: abs(1 / statistics.median(positive) - rate))
        if measured_rate is None or abs(measured_rate / closest - 1) > 0.005:
            raise ValueError("Cannot confidently infer sample rate; pass --sample-rate from the acquisition log")
        sample_rate = closest
        rate_source = "inferred_from_CSV_time_span_verify_against_acquisition_log"
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive")
    edges = []
    start, previous = None, 0
    for i, code in enumerate(codes + [0]):
        if code != previous:
            if previous:
                edges.append({"sample_index_zero_based": start, "csv_time_s": times[start],
                              "code": previous, "sample_count": i - start,
                              "width_ms": 1000 * (i - start) / sample_rate,
                              "zero_before": start > 0 and codes[start - 1] == 0,
                              "recording_ended_high": i == len(codes)})
            start = i if code else None
            previous = code
    expected = run["expected_nonzero_codes"]
    found = [edge["code"] for edge in edges]
    gaps = sum(dt > 1.5 / sample_rate for dt in deltas)
    non_increasing = sum(dt <= 0 for dt in deltas)
    adc_i = next((i for i, name in enumerate(names) if "adc" in name and ("seq" in name or "sequence" in name)), None)
    sequence_errors = None
    if adc_i is not None:
        seq = [row[adc_i] for row in rows]
        if any(value != round(value) or not 0 <= value <= 255 for value in seq):
            raise ValueError("ADC sequence is not an unsigned 8-bit counter; inspect layout before interpreting")
        sequence_errors = sum((b - a) % 256 != 1 for a, b in zip(seq, seq[1:]))
    channels = {}
    # Common 10-20 electrode labels; never label ADC counters as EEG.
    electrode_names = {"f3", "f4", "c3", "c4", "p3", "p4", "pz", "fz", "cz", "o1", "o2",
                       "fp1", "fp2", "t3", "t4", "t5", "t6", "t7", "t8", "a1", "a2", "le"}
    for index, name in enumerate(names):
        if name in electrode_names:
            values = [row[index] for row in rows]
            channels[header[index]] = {"min_export_units": min(values), "max_export_units": max(values),
                                      "sd_export_units": statistics.pstdev(values),
                                      "fraction_abs_at_least_9800": sum(abs(v) >= 9800 for v in values) / len(values),
                                      "all_zero": all(v == 0 for v in values)}
    pulses_ok = bool(edges) and all(edge["zero_before"] and not edge["recording_ended_high"]
                                   and 40 <= edge["width_ms"] <= 120 for edge in edges)
    pulse_delivery_ok = bool(edges) and all(edge["zero_before"] and not edge["recording_ended_high"]
                                          and edge["width_ms"] > 15 for edge in edges)
    return {"source_csv": str(Path(path).resolve()), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "run_id": run["run_id"], "run_status": run["status"], "sample_count": len(rows),
            "sample_rate_hz": sample_rate, "sample_rate_source": rate_source,
            "time_span_s": times[-1] - times[0], "non_increasing_timestamps": non_increasing,
            "timestamp_gaps_over_1_5_samples": gaps, "adc_sequence_discontinuities": sequence_errors,
            "expected_codes": expected, "recorded_codes": found, "code_sequence_matches": found == expected,
            "requested_pulse_widths_approximately_present": pulses_ok, "events": edges,
            "pulse_minimum_requirement_and_resets_met": pulse_delivery_ok,
            "marker_delivery_check_passed": run["status"] == "completed" and found == expected and pulse_delivery_ok
                                             and gaps == 0 and non_increasing == 0 and sequence_errors in (None, 0),
            "eeg_channel_metrics": channels,
            "signal_quality_status": "requires_worn_trace_and_contact_review",
            "signal_quality_note": "9800-unit metric flags the range saturated in the prior bench export; confirm this file's units and reference in metadata. A reference channel may be zero normally.",
            "physical_acoustic_onsets_measured": False, "acoustic_calibration_verified": False,
            "csv_metadata": metadata}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_csv", type=Path)
    parser.add_argument("run_json", type=Path)
    parser.add_argument("--sample-rate", type=float)
    args = parser.parse_args()
    run = json.loads(args.run_json.read_text())
    report = inspect(args.raw_csv, run, args.sample_rate)
    output = args.run_json.parent / "eeg_verification.json"
    output.write_text(json.dumps(report, indent=2))
    print("Marker delivery check:", "PASS" if report["marker_delivery_check_passed"] else "NEEDS REVIEW")
    print("Recorded markers:", len(report["events"]), "; expected:", len(report["expected_codes"]))
    print("Worn EEG contact and physical audio timing still require review.")
    print("Report:", output)


if __name__ == "__main__":
    main()

"""Inspect an existing LSL outlet; this does not connect to headset Bluetooth.

Use LabRecorder for the actual EEG + marker recording. This diagnostic keeps
every streamed channel and applies no EEG filters or clock postprocessing.
"""
import argparse
import time
import xml.etree.ElementTree as ET


def select_stream(streams, name, source_id=None):
    matches = [s for s in streams if s.name() == name and
               (source_id is None or s.source_id() == source_id)]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one matching outlet; found {len(matches)}. "
                         "List outlets first and specify --name and, if needed, --source-id.")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="Exact outlet name printed by discovery")
    parser.add_argument("--source-id", help="Disambiguate outlets with the same name")
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    from pylsl import StreamInlet, resolve_streams

    streams = resolve_streams(wait_time=3.0)
    if not streams:
        raise RuntimeError("No LSL outlets found. Start dsi2lsl first; pylsl is a receiver, not a DSI driver.")
    for s in streams:
        print(f"name={s.name()!r} type={s.type()!r} source_id={s.source_id()!r} "
              f"channels={s.channel_count()} nominal_hz={s.nominal_srate()}")
    if not args.name:
        print("Discovery only. Rerun with --name to inspect an outlet. Nothing was recorded.")
        return
    inlet = StreamInlet(select_stream(streams, args.name, args.source_id), max_buflen=5,
                        processing_flags=0)
    try:
        inlet.open_stream(timeout=5)
        info = inlet.info(timeout=5)
        channels = ET.fromstring(info.as_xml()).findall("./desc/channels/channel")
        print("Channel labels/units:", [(c.findtext("label"), c.findtext("unit")) for c in channels])
        print("Check the complete metadata below for EEG, Trigger and auxiliary channels:")
        print(info.as_xml())
        # Discard any queued samples; observe only newly arriving data.
        inlet.flush()
        started = last_received = time.monotonic()
        samples = 0
        first_ts = last_ts = None
        while time.monotonic() - started < args.seconds:
            chunk, timestamps = inlet.pull_chunk(timeout=0.2, max_samples=1024)
            if timestamps:
                if len(chunk) != len(timestamps) or any(len(row) != info.channel_count() for row in chunk):
                    raise RuntimeError("Unexpected sample dimensions")
                samples += len(timestamps)
                first_ts = timestamps[0] if first_ts is None else first_ts
                last_ts = timestamps[-1]
                last_received = time.monotonic()
            if time.monotonic() - last_received > 3:
                raise RuntimeError("No fresh samples for over three seconds; check acquisition.")
        if not samples:
            raise RuntimeError("The outlet was found but delivered no fresh samples.")
        print(f"Received {samples} samples in {time.monotonic() - started:.2f} seconds.")
        print(f"Raw outlet-clock timestamps: first={first_ts}, last={last_ts}")
        print("No recording saved. This does not verify EEG quality, hardware triggers or acoustic timing.")
    finally:
        inlet.close_stream()


if __name__ == "__main__":
    main()

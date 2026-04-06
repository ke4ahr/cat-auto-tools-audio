#!/usr/bin/env python3
"""Peak-normalize WAV files to just below digital full-scale."""

import argparse
import struct
import wave
from pathlib import Path

MAX_GAIN = 100.0   # skip near-silent files above this multiplier
DEFAULT_TARGET = 32700  # ~2 LSB headroom below 32767


def normalize_wav(src: Path, dst: Path, target: int) -> float | None:
    """Normalize src WAV, write to dst. Return gain applied, or None if skipped."""
    with wave.open(str(src), 'rb') as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    n = len(raw) // 2
    samples = struct.unpack(f"<{n}h", raw)

    peak = max(abs(s) for s in samples) if samples else 0
    if peak == 0:
        dst.write_bytes(src.read_bytes())
        return 1.0

    gain = target / peak
    if gain > MAX_GAIN:
        return None

    scaled = [max(-32768, min(32767, int(round(s * gain)))) for s in samples]
    out_raw = struct.pack(f"<{n}h", *scaled)

    with wave.open(str(dst), 'wb') as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(out_raw)

    return gain


def main():
    ap = argparse.ArgumentParser(description="Peak-normalize WAV files.")
    ap.add_argument("--input", required=True, metavar="DIR", help="Source directory")
    ap.add_argument("--output", required=True, metavar="DIR", help="Output directory")
    ap.add_argument("--headroom", type=int, default=67, metavar="LSB",
                    help="LSB headroom below 32767 (default: 67 -> target 32700)")
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = 32767 - args.headroom

    wavs = sorted(in_dir.glob("*.wav"))
    if not wavs:
        print(f"No WAV files found in {in_dir}")
        return

    for src in wavs:
        dst = out_dir / src.name
        gain = normalize_wav(src, dst, target)
        if gain is None:
            print(f"{src.name:<40}  SKIPPED (near-silent, gain >{MAX_GAIN:.0f}x)")
        else:
            print(f"{src.name:<40}  {gain:.2f}x")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# Copyright (C) 2026 Kris Kirby, KE4AHR
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""cat-310dx_synth.py — Synthesize CAT-310DX LPC clips to WAV.

Bit-direction validation tool. Reads .lpc files from scout_310dx_words/,
synthesizes both MSB-first and LSB-first (bit-reversed) variants for a
configurable subset, writes to tmp/cat-310dx_wav/.

Usage:
    python3 cat-310dx_synth.py [--all] [--dir DIR]

    --all      Synthesize all clips (default: Set 1 only)
    --dir DIR  Source directory (default: scout_310dx_words)
"""

import argparse
import os
import sys

# Reuse synthesis code from cat-1000_lpc_export
sys.path.insert(0, os.path.dirname(__file__))
from cat-1000_lpc_export import render_phrase_to_pcm, write_wav

_BIT_REVERSE = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))


def synth_clip(lpc_bytes: bytes, out_path: str, reverse_bits: bool = False):
    data = bytes(_BIT_REVERSE[b] for b in lpc_bytes) if reverse_bits else lpc_bytes
    pcm = render_phrase_to_pcm(data)
    write_wav(pcm, out_path)
    dur_ms = len(pcm) // 2 * 1000 // 8000
    return dur_ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='Synthesize all clips')
    ap.add_argument('--dir', default='scout_310dx_words',
                    help='Source directory of .lpc files')
    ap.add_argument('--both', action='store_true',
                    help='Emit both MSB and LSB variants for comparison')
    args = ap.parse_args()

    src_dir = args.dir
    out_dir = 'tmp/cat-310dx_wav'
    os.makedirs(out_dir, exist_ok=True)

    lpc_files = sorted(
        f for f in os.listdir(src_dir) if f.endswith('.lpc')
    )

    if not args.all:
        # Default: just Set 1 (w1_*) for initial vocabulary identification
        lpc_files = [f for f in lpc_files if f.startswith('w1_')]

    print(f"Synthesizing {len(lpc_files)} clips from {src_dir}/ → {out_dir}/")

    for fname in lpc_files:
        src = os.path.join(src_dir, fname)
        with open(src, 'rb') as fh:
            lpc_bytes = fh.read()

        stem = fname[:-4]  # strip .lpc

        if args.both:
            for rev, suffix in ((False, '_msb'), (True, '_lsb')):
                out = os.path.join(out_dir, stem + suffix + '.wav')
                try:
                    ms = synth_clip(lpc_bytes, out, reverse_bits=rev)
                    print(f"  {stem}{suffix}.wav  {ms} ms")
                except Exception as e:
                    print(f"  FAIL {stem}{suffix}: {e}", file=sys.stderr)
        else:
            # Try MSB-first (native EPROM order, same as CAT-1000)
            out = os.path.join(out_dir, stem + '.wav')
            try:
                ms = synth_clip(lpc_bytes, out, reverse_bits=False)
                print(f"  {stem}.wav  {ms} ms")
            except Exception as e:
                print(f"  FAIL {stem}: {e}", file=sys.stderr)

    print("Done.")


if __name__ == '__main__':
    main()

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

"""
cat-1000_lpc_repack.py — Repack a TMS5220 .lpc file between bit-endianness formats.

The CAT-1000 voice EPROM stores LPC data in native MSB-first order: bit 7 of
each byte is the first LPC parameter bit.  The ISR at program EPROM offset
0x152F writes these bytes directly to the TSP53C30 (I/O port 0x0180) with no
transformation — so MSB-first is also the bit order the chip receives.

Some external tools (including the PyTI LPCSynthesizer) use LSB-first
(bit-0-first) byte packing instead.  This tool converts between the two
formats by reversing the bit order within each byte.

Note: only the DATA bits are affected.  The logical meaning of each frame
(energy, repeat, pitch, K values) is preserved; only the byte encoding
is mirrored.  A double conversion restores the original file.

Usage:
    # MSB-first (native EPROM) ↔ LSB-first (the operation is its own inverse)
    python3 cat-1000_lpc_repack.py input.lpc output.lpc

    # Repack a whole directory
    python3 cat-1000_lpc_repack.py --batch lpc_phrases/ repacked/

    # Verify: double-convert should reproduce the original
    python3 cat-1000_lpc_repack.py input.lpc round_trip.lpc
    python3 cat-1000_lpc_repack.py round_trip.lpc should_match_input.lpc
    diff <(xxd input.lpc) <(xxd should_match_input.lpc) && echo "OK"
"""

import argparse
import os
import sys

# Pre-computed bit-reversal lookup table (reverse all 8 bits of a byte)
_REVERSE = bytes(
    int(f"{b:08b}"[::-1], 2) for b in range(256)
)


def reverse_bits(data: bytes) -> bytes:
    """Return a new bytes object with the bit order of each byte reversed."""
    return bytes(_REVERSE[b] for b in data)


def repack_file(src: str, dst: str) -> int:
    """
    Bit-reverse every byte in src and write to dst.
    Returns the number of bytes processed.
    """
    with open(src, 'rb') as f:
        data = f.read()
    repacked = reverse_bits(data)
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    with open(dst, 'wb') as f:
        f.write(repacked)
    return len(data)


def repack_batch(src_dir: str, dst_dir: str) -> tuple[int, int]:
    """
    Repack all .lpc files in src_dir into dst_dir.
    Returns (file_count, byte_count).
    """
    os.makedirs(dst_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(src_dir) if f.lower().endswith('.lpc'))
    total_bytes = 0
    for name in files:
        n = repack_file(os.path.join(src_dir, name),
                        os.path.join(dst_dir, name))
        total_bytes += n
    return len(files), total_bytes


def main():
    ap = argparse.ArgumentParser(
        description='Repack TMS5220 .lpc files between LSB-first and MSB-first bit packing.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('input',
                    help='Input .lpc file, or source directory when --batch is used')
    ap.add_argument('output',
                    help='Output .lpc file, or destination directory when --batch is used')
    ap.add_argument('--batch', action='store_true',
                    help='Process all .lpc files in the input directory')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='Print per-file information')
    args = ap.parse_args()

    if args.batch:
        if not os.path.isdir(args.input):
            print(f"ERROR: --batch requires input to be a directory: {args.input}",
                  file=sys.stderr)
            sys.exit(1)
        n_files, n_bytes = repack_batch(args.input, args.output)
        print(f"Repacked {n_files} file(s), {n_bytes} bytes total → {args.output}/")
    else:
        if not os.path.isfile(args.input):
            print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        n_bytes = repack_file(args.input, args.output)
        if args.verbose:
            print(f"Repacked {n_bytes} bytes: {args.input} → {args.output}")
        else:
            print(f"Done: {args.output} ({n_bytes} bytes)")


if __name__ == '__main__':
    main()

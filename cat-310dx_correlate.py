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

"""cat-310dx_correlate.py — Correlate CAT-310DX clips with CAT-1000 clips by checksum.

Performs two independent byte-for-byte comparisons:

  LPC match  — .lpc files in dx-dir vs. cat1000-dir
  WAV match  — .wav files in dx-wav-dir vs. cat1000-wav-dir

Each technique is reported separately.  A clip may match on LPC only,
WAV only, both, or neither.  The match_type column summarises the result:
"both", "lpc_only", "wav_only", or "none".

When both techniques agree on a label the result is unambiguous.  When
only one technique matches the match_type column identifies which one.
When both match but disagree on label the CSV records both labels and
match_type is "conflict".

Exports two output files:

  tmp/cat-310dx_correlation.csv   — full table for every 310DX clip
  tmp/cat-310dx_rename_map.json   — {old_basename: new_basename} for matched clips

The rename map prefers the LPC-matched label; falls back to WAV-matched
label when there is no LPC match.

CSV columns:
  dx_filename          — e.g. 0001_One.lpc
  dx_size              — .lpc file size in bytes
  algo                 — hash algorithm used
  lpc_checksum         — hex digest of .lpc file
  lpc_match            — yes / no
  lpc_cat1000_filename — matched CAT-1000 .lpc filename, or empty
  lpc_cat1000_word_id  — matched CAT-1000 word number, or empty
  lpc_cat1000_label    — matched CAT-1000 label, or empty
  wav_checksum         — hex digest of .wav file (empty if WAV file absent)
  wav_match            — yes / no / n/a
  wav_cat1000_filename — matched CAT-1000 .wav filename, or empty
  wav_cat1000_word_id  — matched CAT-1000 word number, or empty
  wav_cat1000_label    — matched CAT-1000 label, or empty
  match_type           — both / lpc_only / wav_only / conflict / none

Usage:
    python3 cat-310dx_correlate.py [options]

Options:
    --algo {md5,sha1,sha256}   Hash algorithm (default: sha256)
    --dx-dir DIR               CAT-310DX LPC directory (default: cat-310dx_lpc_clips)
    --310dx-wav-dir DIR        CAT-310DX WAV directory (default: cat-310dx_wav_clips)
    --cat1000-dir DIR          CAT-1000 LPC directory (default: cat-1000_lpc_clips)
    --cat1000-wav-dir DIR      CAT-1000 WAV directory (default: cat-1000_wav_clips)
    -o FILE                    Output CSV (default: tmp/cat-310dx_correlation.csv)
    --map FILE                 Output JSON rename map (default: tmp/cat-310dx_rename_map.json)
    -q                         Suppress per-file progress
"""

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hash_file(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def parse_stem(stem: str):
    """Return (word_id_str, label) from '0001_One' or '0220_Affirmative'."""
    idx = stem.index('_')
    return stem[:idx], stem[idx + 1:]


def build_hash_index(directory: Path, ext: str, algo: str, quiet: bool) -> dict:
    """Hash all files with the given extension in directory.

    Returns {checksum: {filename, word_id, label, size}}.
    """
    index = {}
    files = sorted(directory.glob(f'*{ext}'))
    for path in files:
        chk = hash_file(path, algo)
        try:
            word_id, label = parse_stem(path.stem)
        except ValueError:
            word_id, label = path.stem, path.stem
        index[chk] = {
            'filename': path.name,
            'word_id':  word_id,
            'label':    label,
            'size':     path.stat().st_size,
        }
    if not quiet:
        print(f'  {len(files)} files → {len(index)} unique checksums')
    return index


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Correlate CAT-310DX clips with CAT-1000 clips by LPC and WAV checksum.')
    ap.add_argument('--algo', choices=['md5', 'sha1', 'sha256'], default='sha256',
                    metavar='{md5,sha1,sha256}',
                    help='Hash algorithm (default: sha256)')
    ap.add_argument('--dx-dir', default='cat-310dx_lpc_clips', metavar='DIR',
                    help='CAT-310DX LPC directory (default: cat-310dx_lpc_clips)')
    ap.add_argument('--310dx-wav-dir', dest='cat310dx_wav_dir',
                    default='cat-310dx_wav_clips', metavar='DIR',
                    help='CAT-310DX WAV directory (default: cat-310dx_wav_clips)')
    ap.add_argument('--cat1000-dir', default='cat-1000_lpc_clips', metavar='DIR',
                    help='CAT-1000 LPC directory (default: cat-1000_lpc_clips)')
    ap.add_argument('--cat1000-wav-dir', default='cat-1000_wav_clips', metavar='DIR',
                    help='CAT-1000 WAV directory (default: cat-1000_wav_clips)')
    ap.add_argument('-o', '--output', default='tmp/cat-310dx_correlation.csv', metavar='FILE',
                    help='Output CSV file (default: tmp/cat-310dx_correlation.csv)')
    ap.add_argument('--map', default='tmp/cat-310dx_rename_map.json', metavar='FILE',
                    help='Output JSON rename map (default: tmp/cat-310dx_rename_map.json)')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='Suppress per-file progress output')
    args = ap.parse_args()

    dx_dir          = Path(args.dx_dir)
    dx_wav_dir      = Path(args.cat310dx_wav_dir)
    cat1000_dir     = Path(args.cat1000_dir)
    cat1000_wav_dir = Path(args.cat1000_wav_dir)
    algo            = args.algo

    if not dx_dir.is_dir():
        print(f'error: CAT-310DX LPC directory not found: {dx_dir}', file=sys.stderr)
        sys.exit(1)
    if not cat1000_dir.is_dir():
        print(f'error: CAT-1000 LPC directory not found: {cat1000_dir}', file=sys.stderr)
        sys.exit(1)

    have_dx_wav      = dx_wav_dir.is_dir()
    have_cat1000_wav = cat1000_wav_dir.is_dir()
    do_wav           = have_dx_wav and have_cat1000_wav

    if not do_wav and not args.quiet:
        if not have_dx_wav:
            print(f'warning: CAT-310DX WAV directory not found: {dx_wav_dir} — WAV matching skipped',
                  file=sys.stderr)
        if not have_cat1000_wav:
            print(f'warning: CAT-1000 WAV directory not found: {cat1000_wav_dir} — WAV matching skipped',
                  file=sys.stderr)

    # ---- Hash CAT-1000 LPC clips --------------------------------------------
    if not args.quiet:
        print(f'Hashing CAT-1000 LPC clips ({algo})...')
    cat1000_lpc_idx = build_hash_index(cat1000_dir, '.lpc', algo, args.quiet)

    # ---- Hash CAT-1000 WAV clips --------------------------------------------
    cat1000_wav_idx = {}
    if do_wav:
        if not args.quiet:
            print(f'Hashing CAT-1000 WAV clips ({algo})...')
        cat1000_wav_idx = build_hash_index(cat1000_wav_dir, '.wav', algo, args.quiet)

    # ---- Correlate CAT-310DX clips ------------------------------------------
    if not args.quiet:
        print(f'Hashing CAT-310DX clips ({algo})...')

    dx_lpc_files = sorted(dx_dir.glob('*.lpc'))
    rows         = []
    rename_map   = {}
    lpc_matched  = 0
    wav_matched  = 0

    for lpc_path in dx_lpc_files:
        lpc_chk  = hash_file(lpc_path, algo)
        lpc_size = lpc_path.stat().st_size
        lpc_hit  = cat1000_lpc_idx.get(lpc_chk)

        # WAV match: look for a .wav file with the same stem
        wav_chk = ''
        wav_hit = None
        if do_wav:
            wav_path = dx_wav_dir / (lpc_path.stem + '.wav')
            if wav_path.is_file():
                wav_chk = hash_file(wav_path, algo)
                wav_hit = cat1000_wav_idx.get(wav_chk)

        # Determine match_type
        lpc_label = lpc_hit['label'] if lpc_hit else None
        wav_label = wav_hit['label'] if wav_hit else None

        if lpc_hit and wav_hit:
            match_type = 'both' if lpc_label == wav_label else 'conflict'
        elif lpc_hit:
            match_type = 'lpc_only'
        elif wav_hit:
            match_type = 'wav_only'
        else:
            match_type = 'none'

        if lpc_hit:
            lpc_matched += 1
        if wav_hit:
            wav_matched += 1

        # Rename map: prefer LPC label, fall back to WAV label
        best_hit = lpc_hit or wav_hit
        if best_hit:
            new_stem = lpc_path.stem + '_' + best_hit['label']
            rename_map[lpc_path.stem] = new_stem

        rows.append({
            'dx_filename':          lpc_path.name,
            'dx_size':              lpc_size,
            'algo':                 algo,
            'lpc_checksum':         lpc_chk,
            'lpc_match':            'yes' if lpc_hit else 'no',
            'lpc_cat1000_filename': lpc_hit['filename'] if lpc_hit else '',
            'lpc_cat1000_word_id':  lpc_hit['word_id']  if lpc_hit else '',
            'lpc_cat1000_label':    lpc_hit['label']     if lpc_hit else '',
            'wav_checksum':         wav_chk,
            'wav_match':            ('yes' if wav_hit else 'no') if do_wav else 'n/a',
            'wav_cat1000_filename': wav_hit['filename'] if wav_hit else '',
            'wav_cat1000_word_id':  wav_hit['word_id']  if wav_hit else '',
            'wav_cat1000_label':    wav_hit['label']     if wav_hit else '',
            'match_type':           match_type,
        })

    # ---- Write CSV ----------------------------------------------------------
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    fieldnames = [
        'dx_filename', 'dx_size', 'algo',
        'lpc_checksum', 'lpc_match',
        'lpc_cat1000_filename', 'lpc_cat1000_word_id', 'lpc_cat1000_label',
        'wav_checksum', 'wav_match',
        'wav_cat1000_filename', 'wav_cat1000_word_id', 'wav_cat1000_label',
        'match_type',
    ]
    with open(args.output, 'w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames,
                                quoting=csv.QUOTE_NONNUMERIC)
        writer.writeheader()
        writer.writerows(rows)

    # ---- Write rename map ---------------------------------------------------
    with open(args.map, 'w') as fh:
        json.dump(rename_map, fh, indent=2)

    # ---- Summary ------------------------------------------------------------
    total = len(rows)
    print(f'Correlated {total} CAT-310DX clips:')
    print(f'  LPC matches: {lpc_matched}  ({total - lpc_matched} unmatched)')
    if do_wav:
        print(f'  WAV matches: {wav_matched}  ({total - wav_matched} unmatched)')
    else:
        print(f'  WAV matches: skipped (WAV directories not available)')
    print(f'Wrote {args.output}  ({total} rows)')
    print(f'Wrote {args.map}  ({len(rename_map)} rename entries)')


if __name__ == '__main__':
    main()

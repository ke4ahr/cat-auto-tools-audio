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

"""cat-310dx_rename_clips.py — Rename CAT-310DX LPC/WAV clips from a correlation map.

Reads the JSON rename map produced by cat-310dx_correlate.py and renames .lpc
and .wav file pairs in the CAT-310DX clip directory.  By default runs in
dry-run mode and prints what would happen without making any changes.

This script is intended for old-style address-named clips (e.g. 4FED_w01.lpc)
extracted before the cat-310dx_extract.py rewrite.  Clips extracted with the updated
cat-310dx_extract.py already use sequence-numbered names and do not need renaming.

Idempotent: if the source file is missing but the destination already exists,
the pair is counted as already-done rather than an error.

Usage:
    python3 cat-310dx_rename_clips.py [options]

Options:
    --map FILE     JSON rename map from cat-310dx_correlate.py
                   (default: tmp/cat-310dx_rename_map.json)
    --dir DIR      Directory containing files to rename
                   (default: cat-310dx_lpc_clips)
    --apply        Actually rename files (default: dry-run only)
    --no-wav       Skip .wav files; rename .lpc only
    -q             Suppress per-file output
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(
        description='Rename CAT-310DX LPC/WAV clips using a correlation map.')
    ap.add_argument('--map', default='tmp/cat-310dx_rename_map.json', metavar='FILE',
                    help='JSON rename map (default: tmp/cat-310dx_rename_map.json)')
    ap.add_argument('--dir', default='cat-310dx_lpc_clips', metavar='DIR',
                    help='Directory containing clips (default: cat-310dx_lpc_clips)')
    ap.add_argument('--apply', action='store_true',
                    help='Actually rename files (default: dry-run)')
    ap.add_argument('--no-wav', action='store_true',
                    help='Skip .wav files; rename .lpc only')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='Suppress per-file output')
    args = ap.parse_args()

    map_path = Path(args.map)
    clip_dir = Path(args.dir)

    if not map_path.exists():
        print(f'error: rename map not found: {map_path}', file=sys.stderr)
        sys.exit(1)
    if not clip_dir.is_dir():
        print(f'error: clip directory not found: {clip_dir}', file=sys.stderr)
        sys.exit(1)

    with open(map_path) as fh:
        rename_map = json.load(fh)

    if not args.apply:
        print('Dry-run mode — use --apply to rename files.')

    extensions = ['.lpc'] if args.no_wav else ['.lpc', '.wav']

    done = skipped = missing = 0

    for old_stem, new_stem in sorted(rename_map.items()):
        for ext in extensions:
            old_path = clip_dir / (old_stem + ext)
            new_path = clip_dir / (new_stem + ext)

            if not old_path.exists():
                if new_path.exists():
                    # Already renamed in a previous run — count as done
                    if not args.quiet:
                        print(f'  already done: {new_path.name}')
                    done += 1
                else:
                    if not args.quiet:
                        print(f'  missing: {old_path.name}')
                    missing += 1
                continue

            if new_path.exists() and new_path != old_path:
                if not args.quiet:
                    print(f'  skip (target exists): {new_path.name}')
                skipped += 1
                continue

            verb = 'rename' if args.apply else 'would rename'
            if not args.quiet:
                print(f'  {verb}: {old_path.name} → {new_path.name}')

            if args.apply:
                old_path.rename(new_path)
            done += 1

    pairs = len(extensions)
    action = 'Renamed' if args.apply else 'Would rename'
    print(f'{action} {done // pairs} pair(s)  '
          f'({skipped // pairs} skipped, {missing // pairs} missing).')


if __name__ == '__main__':
    main()

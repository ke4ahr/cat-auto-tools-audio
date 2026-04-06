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

"""cat-310dx_extract.py — Extract and synthesize all LPC clips from CAT-310DX EPROM.

Parses the word dispatch table at 0x4AC3 (415 3-byte entries ending at 0x4FA0),
assigns sequence numbers using the same group*100+index scheme as the CAT-1000,
and writes .lpc files to cat-310dx_lpc_clips/ and .wav files to cat-310dx_wav_clips/
(or custom directories via -o and --wav-dir).

Also writes a cat-310dx_clips.csv phrase table in the same format as cat-1000_clips.csv.

Dispatch table group layout (confirmed from CJNE chain at 0x49E6):

  Group | Table start | Entry range | Entry count
  ------+-------------+-------------+------------
    0   |   0x4AC3    |     0–27    |    28
    2   |   0x4B17    |    28–98    |    71
    3   |   0x4BEC    |    99–146   |    48
    4   |   0x4C7C    |   147–171   |    25
    5   |   0x4CC7    |   172–221   |    50   (boundary estimated; docs give ~0x4CC8)
    6   |   0x4D5D    |   222–276   |    55
    7   |   0x4E02    |   277–325   |    49
    8   |   0x4E95    |   326–379   |    54
    9   |   0x4F37    |   380–414   |    35

  Sequence number formula (mirrors CAT-1000):
    sequence_number = group_id * 100 + index_within_group

  File naming: {seq:04d}_{spoken_label}.lpc  (e.g. 0600_Sixty.lpc)
  CSV format (no header row):  NNN,"Label",0xSTART,0xEND

Word IDs: 0x00–0x63 = decimal 0–99 (hex value equals spoken decimal number).
Multiple dispatch entries per word ID = context-specific intonation recordings.
Bit order: MSB-first (identical to CAT-1000).

Usage:
    python3 cat-310dx_extract.py [ROM] [--wav] [-o DIR] [--wav-dir DIR] [--csv FILE] [-q]
    python3 cat-310dx_extract.py --dump-csv
"""

import argparse
import csv
import importlib
import os
import sys

# ---------------------------------------------------------------------------
# Import render_phrase_to_pcm and write_wav from cat-1000_lpc_export
# (module filename contains a hyphen, so importlib is required)
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    _cat = importlib.import_module('cat-1000_lpc_export')
    render_phrase_to_pcm = _cat.render_phrase_to_pcm
    write_wav = _cat.write_wav
    _HAS_CAT = True
except ImportError:
    _HAS_CAT = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ROM_PATH = 'eprom_images/CAT-310DX_V1-00_1998_7A69.BIN'
DEFAULT_OUT_DIR  = 'cat-310dx_lpc_clips'
DEFAULT_WAV_DIR  = 'cat-310dx_wav_clips'
DEFAULT_CSV_PATH = 'cat-310dx_clips.csv'

TABLE_START  = 0x4AC3   # first byte of dispatch table
TABLE_END    = 0x4FA0   # first byte of speech data (exclusive)
TOTAL_ENTRIES = (TABLE_END - TABLE_START) // 3   # 415

# Group layout: (group_id, first_entry_index)
# Entry k occupies bytes [TABLE_START + k*3 : TABLE_START + k*3 + 3]
# Boundaries for groups 0,2,3,4,6,7,8,9 derived from documented table addresses
# (each address verified as TABLE_START + n*3 for integer n).
# Group 5 boundary at entry 172 (address 0x4CC7) is the nearest valid 3-byte
# boundary to the documented value of 0x4CC8.
GROUP_BOUNDARIES = [
    (0,   0),    # 0x4AC3 — verified
    (2,  28),    # 0x4B17 — verified
    (3,  99),    # 0x4BEC — verified
    (4, 147),    # 0x4C7C — verified
    (5, 172),    # 0x4CC7 — estimated (nearest valid boundary to docs' 0x4CC8)
    (6, 222),    # 0x4D5D — verified
    (7, 277),    # 0x4E02 — verified
    (8, 326),    # 0x4E95 — verified
    (9, 380),    # 0x4F37 — verified
]

# ---------------------------------------------------------------------------
# Number-to-label table (word_id decimal value → spoken English)
# ---------------------------------------------------------------------------

_ONES = [
    'Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
    'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
    'Seventeen', 'Eighteen', 'Nineteen',
]
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
         'Sixty', 'Seventy', 'Eighty', 'Ninety']


def word_id_to_label(wid: int) -> str:
    """Return the spoken English label for a word ID (0x00–0x63 = 0–99)."""
    if wid < 20:
        return _ONES[wid]
    t, o = divmod(wid, 10)
    return _TENS[t] if o == 0 else f'{_TENS[t]}-{_ONES[o]}'


# ---------------------------------------------------------------------------
# Dispatch table parsing
# ---------------------------------------------------------------------------

def parse_dispatch_table(rom: bytes):
    """Return list of (entry_index, word_id, addr) for all 415 table entries."""
    entries = []
    for k in range(TOTAL_ENTRIES):
        pos    = TABLE_START + k * 3
        wid    = rom[pos]
        addr   = (rom[pos + 1] << 8) | rom[pos + 2]
        entries.append((k, wid, addr))
    return entries


def assign_sequence_numbers(entries):
    """Add sequence_number = group_id * 100 + index_within_group to each entry.

    Returns list of dicts with keys: seq, group, idx_in_group, word_id, addr.
    """
    # Build a lookup: entry_index → (group_id, idx_within_group)
    boundaries = sorted(GROUP_BOUNDARIES, key=lambda x: x[1])
    group_of   = {}
    for gi, (gid, gstart) in enumerate(boundaries):
        gend = boundaries[gi + 1][1] if gi + 1 < len(boundaries) else TOTAL_ENTRIES
        for k in range(gstart, gend):
            group_of[k] = (gid, k - gstart)

    result = []
    for k, wid, addr in entries:
        gid, idx = group_of.get(k, (0, k))
        result.append({
            'seq':          gid * 100 + idx,
            'group':        gid,
            'idx_in_group': idx,
            'entry_index':  k,
            'word_id':      wid,
            'addr':         addr,
            'label':        word_id_to_label(wid),
        })
    return result


def build_clips(rom: bytes, annotated_entries):
    """Return list of clip dicts, one per unique LPC address.

    For addresses shared by multiple dispatch entries, the entry with the
    lowest sequence number is used as the primary (determines filename).
    """
    # Find last address in speech region using STOP-byte scan for the final clip
    addrs_sorted = sorted(set(e['addr'] for e in annotated_entries))

    # Map address → primary entry (lowest seq) + list of all seq numbers
    addr_to_primary   = {}
    addr_to_all_seqs  = {}
    for e in sorted(annotated_entries, key=lambda x: x['seq']):
        a = e['addr']
        if a not in addr_to_primary:
            addr_to_primary[a] = e
        addr_to_all_seqs.setdefault(a, []).append(e['seq'])

    clips = []
    for i, addr in enumerate(addrs_sorted):
        if i + 1 < len(addrs_sorted):
            end = addrs_sorted[i + 1]
        else:
            end = _find_stop_byte(rom, addr)
        primary = addr_to_primary[addr]
        clips.append({
            'seq':          primary['seq'],
            'label':        primary['label'],
            'word_id':      primary['word_id'],
            'addr':         addr,
            'end':          end,
            'size':         end - addr,
            'all_seqs':     addr_to_all_seqs[addr],
            'undocumented': False,
        })
    return clips


def _find_stop_byte(rom: bytes, start: int, max_scan: int = 8192) -> int:
    """Return offset one past the first byte with high nibble 0xF (STOP frame)."""
    for i in range(start, min(start + max_scan, len(rom))):
        if (rom[i] & 0xF0) == 0xF0:
            return i + 1
    return start + max_scan


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_clips_csv(clips, path: str):
    """Write 310dx_clips.csv in the same format as cat-1000_clips.csv.

    Format (no header row): seq_num,"Label",0xSTART,0xEND
    """
    with open(path, 'w', newline='') as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC)
        for c in sorted(clips, key=lambda x: x['seq']):
            row = [
                f'{c["seq"]:03d}',
                c['label'],
                f'0x{c["addr"]:04X}',
                f'0x{c["end"]:04X}',
            ]
            if c.get('undocumented'):
                row.append('undocumented')
            writer.writerow(row)


def dump_csv_to_stdout(clips):
    """Print the phrase table to stdout (--dump-csv mode)."""
    import csv as _csv
    import io
    buf = io.StringIO()
    writer = _csv.writer(buf, quoting=_csv.QUOTE_NONNUMERIC)
    for c in sorted(clips, key=lambda x: x['seq']):
        row = [
            f'{c["seq"]:03d}',
            c['label'],
            f'0x{c["addr"]:04X}',
            f'0x{c["end"]:04X}',
        ]
        if c.get('undocumented'):
            row.append('undocumented')
        writer.writerow(row)
    sys.stdout.write(buf.getvalue())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Extract CAT-310DX LPC clips with sequence-numbered filenames.')
    ap.add_argument('rom', nargs='?', default=DEFAULT_ROM_PATH,
                    metavar='ROM',
                    help=f'EPROM image (default: {DEFAULT_ROM_PATH})')
    ap.add_argument('--wav', action='store_true',
                    help='Synthesize WAV files (requires PyTI_LPC_CMD via '
                         'cat-1000_lpc_export)')
    ap.add_argument('-o', '--output', default=DEFAULT_OUT_DIR, metavar='DIR',
                    help=f'LPC output directory (default: {DEFAULT_OUT_DIR})')
    ap.add_argument('--wav-dir', default=DEFAULT_WAV_DIR, metavar='DIR',
                    help=f'WAV output directory (default: {DEFAULT_WAV_DIR})')
    ap.add_argument('--csv', default=DEFAULT_CSV_PATH, metavar='FILE',
                    help=f'Write phrase table CSV (default: {DEFAULT_CSV_PATH})')
    ap.add_argument('--no-csv', action='store_true',
                    help='Do not write a CSV file')
    ap.add_argument('--dump-csv', action='store_true',
                    help='Print phrase table to stdout and exit (no extraction)')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='Suppress per-clip output')
    args = ap.parse_args()

    if not os.path.exists(args.rom):
        print(f'error: ROM not found: {args.rom}', file=sys.stderr)
        sys.exit(1)

    if args.wav and not _HAS_CAT:
        print('error: --wav requires cat-1000_lpc_export (import failed)',
              file=sys.stderr)
        sys.exit(1)

    rom      = open(args.rom, 'rb').read()
    entries  = parse_dispatch_table(rom)
    annotated = assign_sequence_numbers(entries)
    clips    = build_clips(rom, annotated)

    if args.dump_csv:
        dump_csv_to_stdout(clips)
        return

    os.makedirs(args.output, exist_ok=True)
    if args.wav:
        os.makedirs(args.wav_dir, exist_ok=True)

    if not args.quiet:
        print(f'ROM: {args.rom} ({len(rom)} bytes)')
        print(f'Dispatch table: 0x{TABLE_START:04X}–0x{TABLE_END:04X}, '
              f'{len(entries)} entries, {len(clips)} unique clips')
        speech_start = clips[0]['addr']
        speech_end   = clips[-1]['end']
        print(f'Speech data: 0x{speech_start:04X}–0x{speech_end:04X} '
              f'({speech_end - speech_start} bytes = '
              f'{(speech_end - speech_start) / 1024:.1f} KB)')
        print(f'LPC output: {args.output}/')
        if args.wav:
            print(f'WAV output: {args.wav_dir}/')
        print()

    ok = err = skipped = 0

    for clip in sorted(clips, key=lambda x: x['seq']):
        lpc_bytes = rom[clip['addr']:clip['end']]
        name      = f'{clip["seq"]:04d}_{clip["label"]}'
        lpc_path  = os.path.join(args.output, name + '.lpc')

        with open(lpc_path, 'wb') as fh:
            fh.write(lpc_bytes)

        if args.wav:
            wav_path = os.path.join(args.wav_dir, name + '.wav')
            try:
                pcm    = render_phrase_to_pcm(lpc_bytes)
                write_wav(pcm, wav_path)
                dur_ms = len(pcm) // 2 * 1000 // 8000
                if not args.quiet:
                    print(f'  {name}  {clip["size"]:>5} B  {dur_ms:>4} ms  '
                          f'wid=0x{clip["word_id"]:02X}  seqs={clip["all_seqs"]}')
                ok += 1
            except Exception as exc:
                print(f'  FAIL {name}: {exc}', file=sys.stderr)
                err += 1
        else:
            if not args.quiet:
                print(f'  {name}  {clip["size"]:>5} B  '
                      f'wid=0x{clip["word_id"]:02X}  seqs={clip["all_seqs"]}')
            ok += 1

    if not args.no_csv:
        write_clips_csv(clips, args.csv)
        if not args.quiet:
            print(f'\nWrote {len(clips)} entries to {args.csv}')

    print()
    msg = f'Exported {ok} clip(s)'
    if err:
        msg += f', {err} failed'
    if skipped:
        msg += f', {skipped} skipped'
    print(msg + '.')


if __name__ == '__main__':
    main()

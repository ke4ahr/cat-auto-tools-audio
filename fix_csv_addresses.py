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

"""Bulk-fix truncated end addresses in cat-1000_clips.csv and cat-1000_phrases.csv.

Strategy: replace end_address with voice EPROM index end (= start of next phrase
in address-sorted order) for all words where csv_end < index_end.

Skip the 9 discrepant words that need individual investigation:
17, 260, 344, 348, 476, 602, 663, 732, 831
"""

import csv, io, sys

VOICE_EPROM = 'eprom_images/cat-1000-voice_27SF512.BIN'

PHRASE_GROUPS = {
    0: (0x009F, 0x1D),
    2: (0x00F3, 0x4F),
    3: (0x01E0, 0x36),
    4: (0x0282, 0x2B),
    5: (0x0303, 0x32),
    6: (0x0399, 0x41),
    7: (0x0459, 0x3D),
    8: (0x0510, 0x3C),
    9: (0x05C4, 0x2A),
}

SKIP_WORDS = {17, 260, 344, 348, 476, 602, 663, 732, 831}

# ── Build phrase index ──────────────────────────────────────────────────────

with open(VOICE_EPROM, 'rb') as f:
    voice = f.read()

entries = []
for grp, (off, cnt) in PHRASE_GROUPS.items():
    for i in range(cnt):
        base = off + i * 3
        pid  = voice[base]
        addr = voice[base+1] | (voice[base+2] << 8)
        entries.append((grp * 100 + pid, addr))

sorted_starts = sorted(set(a for _, a in entries if 0x0642 <= a < 0x10000))
start_to_word = {addr: wn for wn, addr in entries if 0x0642 <= addr < 0x10000}

def index_end(start):
    i = sorted_starts.index(start)
    return sorted_starts[i+1] if i+1 < len(sorted_starts) else 0xC4C1

# Precompute: word_number -> index_end_address
word_to_index_end = {}
for wn, addr in entries:
    if 0x0642 <= addr < 0x10000:
        word_to_index_end[wn] = index_end(addr)

# ── Fix cat-1000_clips.csv ───────────────────────────────────────────────────
# Format: NNN,"Label",0xSTART,0xEND   (no header row)

clips_fixed = 0
clips_skipped = 0
clips_out_rows = []

with open('cat-1000_clips.csv', newline='') as f:
    reader = csv.reader(f)
    for row in reader:
        wn = int(row[0])
        label = row[1]
        start = int(row[2], 16)
        end   = int(row[3], 16)

        undoc = row[4].strip() if len(row) > 4 else ''

        if wn in SKIP_WORDS:
            clips_out_rows.append(row)
            clips_skipped += 1
            continue

        ie = word_to_index_end.get(wn)
        if ie is not None and end < ie:
            new_row = [row[0], row[1], row[2], f'0x{ie:04X}']
            if undoc:
                new_row.append(undoc)
            print(f"  clips  word {wn:4d} {label:30s}  end {end:#06x} -> {ie:#06x}  (+{ie-end})")
            clips_out_rows.append(new_row)
            clips_fixed += 1
        else:
            clips_out_rows.append(row)

with open('cat-1000_clips.csv', 'w', newline='') as f:
    w = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
    for row in clips_out_rows:
        w.writerow(row)

print(f"\ncat-1000_clips.csv: fixed {clips_fixed} rows, skipped {clips_skipped} discrepant\n")

# ── Fix cat-1000_phrases.csv ─────────────────────────────────────────────────
# Format: word_number,word_text,start_address,end_address,notes  (has header)

phrases_fixed = 0
phrases_skipped = 0
phrases_out_rows = []

with open('cat-1000_phrases.csv', newline='') as f:
    reader = csv.reader(f)
    header = next(reader)
    phrases_out_rows.append(header)
    for row in reader:
        wn    = int(row[0])
        label = row[1]
        start = int(row[2], 16)
        end   = int(row[3], 16)
        notes = row[4] if len(row) > 4 else ''

        if wn in SKIP_WORDS:
            phrases_out_rows.append(row)
            phrases_skipped += 1
            continue

        ie = word_to_index_end.get(wn)
        if ie is not None and end < ie:
            new_row = [row[0], row[1], row[2], f'0x{ie:04X}', notes]
            phrases_fixed += 1
            phrases_out_rows.append(new_row)
        else:
            phrases_out_rows.append(row)

with open('cat-1000_phrases.csv', 'w', newline='') as f:
    w = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
    for row in phrases_out_rows:
        w.writerow(row)

print(f"cat-1000_phrases.csv: fixed {phrases_fixed} rows, skipped {phrases_skipped} discrepant")

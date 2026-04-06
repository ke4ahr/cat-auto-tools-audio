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
cat-1000_lpc_export.py — Export LPC phrase data from the CAT-1000 voice EPROM.

Voice EPROM phrase table format (confirmed from dispatch code disassembly):
  Each group has a table of 3-byte entries: [phrase_id][offset_lo][offset_hi]
  The dispatch code does a LINEAR SEARCH for a matching phrase_id, then reads
  the 2-byte LE offset that follows.

  Group | Table start | Entry count
  ------+-------------+------------
    0   |   0x009F    |  28 (0x1C)   digits / numeric helpers (0–20, 30, 40…90)
    2   |   0x00F3    |  79 (0x4F)
    3   |   0x01E0    |  54 (0x36)
    4   |   0x0282    |  43 (0x2B)
    5   |   0x0303    |  50 (0x32)
    6   |   0x0399    |  65 (0x41)
    7   |   0x0459    |  61 (0x3D)
    8   |   0x0510    |  60 (0x3C)
    9   |   0x05C4    |  42 (0x2A)

  Word number formula (verified against 302-entry word list, 0 mismatches):
    word_number = group * 100 + phrase_id

  LPC data region: 0x0642–0xC42C (~49 KB)
  Error sentinel (phrase not found): BX = 0x81F1

Input CSV format (no header row — new format):
    NNN,"word_text",0xSTART,0xEND[,0xSTART2,0xEND2,...]

    NNN           – 3-digit zero-padded word number
    word_text     – label string in double quotes (used in output filename)
    0xSTART/END   – hex byte offsets into the voice EPROM (exclusive end)
    Additional address pairs on the same row for non-contiguous clips (rare).

    Legacy format (with header row) is also accepted:
    word_number,word_text,start_address,end_address

Output (per row):
    <output_dir>/<word_number>_<word_text>.lpc   raw LPC bytes
    <output_dir>/<word_number>_<word_text>.wav   synthesized audio  (--wav)

Usage examples:
    # Generate a starter CSV from the built-in phrase table, then edit and re-import
    python3 cat-1000_lpc_export.py --dump-csv > phrases.csv

    # Export raw LPC files only
    python3 cat-1000_lpc_export.py phrases.csv

    # Export raw LPC + WAV files into a custom directory
    python3 cat-1000_lpc_export.py phrases.csv --wav -o lpc_out/

    # Use a different voice EPROM image
    python3 cat-1000_lpc_export.py phrases.csv --voice other_voice.bin --wav
"""

import argparse
import csv
import math
import os
import re
import struct
import sys
import wave
from typing import Dict, Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# PyTI_LPC_CMD import (working TMS5220 synthesizer)
# ---------------------------------------------------------------------------

# Search for PyTI_LPC_CMD in order:
#   1. PyTI_LPC_CMD/ subdirectory of this project (installed by INSTALL_PyTI.sh)
#   2. Legacy sibling-tree location (developer workstation path)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTI_SEARCH = [
    os.path.join(_SCRIPT_DIR, 'PyTI_LPC_CMD'),
    os.path.normpath(os.path.join(_SCRIPT_DIR,
                                  '..', '..', '..', 'ti_speech',
                                  'PyTI_LPC_CMD', 'PyTI_LPC_CMD')),
]
for _p in _PYTI_SEARCH:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from pyti_lpc_cmd.synthesizer import LPCSynthesizer as _LPCSynthesizer
    from pyti_lpc_cmd.chips.tms5220 import PARAMS as _TMS5220_PARAMS
    _HAS_PYTI = True
except ImportError:
    _HAS_PYTI = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR         = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VOICE_PATH = os.path.join(SCRIPT_DIR, "eprom_images", "cat-1000-voice_27SF512.BIN")

# ---------------------------------------------------------------------------
# Phrase group table (confirmed from dispatch code disassembly at 0x0000)
# Format: group_id → (table_start_offset, entry_count)
# Each entry is 3 bytes: [phrase_id][offset_lo][offset_hi]
# The dispatch code does a linear search for phrase_id, then reads the 2-byte
# LE offset that follows.
# ---------------------------------------------------------------------------

PHRASE_GROUPS: Dict[int, Tuple[int, int]] = {
    0: (0x009F, 28),   # digits + decade words (0-20, 30, 40, 50, 60, 70, 80, 90)
    2: (0x00F3, 79),
    3: (0x01E0, 54),
    4: (0x0282, 43),
    5: (0x0303, 50),
    6: (0x0399, 65),
    7: (0x0459, 61),
    8: (0x0510, 60),
    9: (0x05C4, 42),
}

LPC_DATA_START = 0x0642   # first byte of speech data
LPC_DATA_END   = 0xC4C1   # end of speech data (exclusive; last phrase 0982 ends here)
ERROR_SENTINEL = 0x81F1   # returned by dispatch when phrase not found

# ---------------------------------------------------------------------------
# TMS5220 LPC synthesis tables
# ---------------------------------------------------------------------------

# K coefficient tables: integer values pre-scaled by 512 (from tms5220.txt).
# The synthesis engine divides each value by 512.0 to get the reflection coefficient.
K1_TABLE = [
    -501, -498, -497, -495, -493, -491, -488, -482,
    -478, -474, -469, -464, -459, -452, -445, -437,
    -412, -380, -339, -288, -227, -158,  -81,   -1,
      80,  157,  226,  287,  337,  379,  411,  436,
]
K2_TABLE = [
    -328, -303, -274, -244, -211, -175, -138,  -99,
     -59,  -18,   24,   64,  105,  143,  180,  215,
     248,  278,  306,  331,  354,  374,  392,  408,
     422,  435,  445,  455,  463,  470,  476,  506,
]
K3_TABLE = [
    -441, -387, -333, -279, -225, -171, -117,  -63,
      -9,   45,   98,  152,  206,  260,  314,  368,
]
K4_TABLE = [
    -328, -273, -217, -161, -106,  -50,    5,   61,
     116,  172,  228,  283,  339,  394,  450,  506,
]
K5_TABLE = [
    -328, -282, -235, -189, -142,  -96,  -50,   -3,
      43,   90,  136,  182,  229,  275,  322,  368,
]
K6_TABLE = [
    -256, -212, -168, -123,  -79,  -35,   10,   54,
      98,  143,  187,  232,  276,  320,  365,  409,
]
K7_TABLE = [
    -308, -260, -212, -164, -117,  -69,  -21,   27,
      75,  122,  170,  218,  266,  314,  361,  409,
]
K8_TABLE  = [-256, -161,  -66,   29,  124,  219,  314,  409]
K9_TABLE  = [-256, -176,  -96,  -15,   65,  146,  226,  307]
K10_TABLE = [-205, -132,  -59,   14,   87,  160,  234,  307]

# TMS5220 energy table (16 levels: 0=silence, 15=STOP)
ENERGY_TABLE = [
    0, 1, 2, 3, 4, 6, 8, 11,
    16, 23, 33, 47, 63, 85, 114, 0,
]

# TMS5220 glottal chirp table (uint8 values from tms5220.txt; 52 entries).
# Values are stored as unsigned bytes in hardware; all TMS5220 values are < 128
# so they are identical when interpreted as signed int8.  Only the first 21
# entries are non-zero; synthesis uses period_counter < 41 as the active window.
CHIRP = [
    0x00, 0x03, 0x0f, 0x28, 0x4c, 0x6c, 0x71, 0x50,
    0x25, 0x26, 0x4c, 0x44, 0x1a, 0x32, 0x3b, 0x13,
    0x37, 0x1a, 0x25, 0x1f, 0x1d, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
]

# TMS5220 pitch period lookup table: 6-bit pitch code → sample period at 8 kHz.
# Code 0 = unvoiced.  64 entries per tms5220.txt.
PITCH_TABLE = [
      0,  15,  16,  17,  18,  19,  20,  21,
     22,  23,  24,  25,  26,  27,  28,  29,
     30,  31,  32,  33,  34,  35,  36,  37,
     38,  39,  40,  41,  42,  44,  46,  48,
     50,  52,  53,  56,  58,  60,  62,  65,
     68,  70,  72,  76,  78,  80,  84,  86,
     91,  94,  98, 101, 105, 109, 114, 118,
    122, 127, 132, 137, 142, 148, 153, 159,
]

K_TABLES = [K1_TABLE, K2_TABLE, K3_TABLE, K4_TABLE, K5_TABLE,
            K6_TABLE, K7_TABLE, K8_TABLE, K9_TABLE, K10_TABLE]
K_BITS   = [5, 5, 4, 4, 4, 4, 4, 3, 3, 3]

ENERGY_BITS     = 4
REPEAT_BITS     = 1
PITCH_BITS      = 6
SAMPLES_PER_FRAME = 200   # 25 ms × 8 kHz

# ---------------------------------------------------------------------------
# LSB-first bit-stream reader (TMS5220 bit packing)
# ---------------------------------------------------------------------------

class BitStream:
    def __init__(self, data: bytes):
        self._data     = data
        self._byte_pos = 0
        self._bit_pos  = 7   # 7 = MSB of current byte; decrements toward 0

    def read_bits(self, n: int) -> int:
        value = 0
        for _ in range(n):
            if self._byte_pos >= len(self._data):
                return value
            bit = (self._data[self._byte_pos] >> self._bit_pos) & 1
            value = (value << 1) | bit   # first bit read becomes MSB of result
            self._bit_pos -= 1
            if self._bit_pos < 0:
                self._bit_pos = 7
                self._byte_pos += 1
        return value

    @property
    def exhausted(self) -> bool:
        return self._byte_pos >= len(self._data)

    @property
    def byte_position(self) -> int:
        """Current read position rounded up to next byte boundary."""
        return self._byte_pos + (1 if self._bit_pos < 7 else 0)

# ---------------------------------------------------------------------------
# LPC frame container
# ---------------------------------------------------------------------------

class LPCFrame:
    __slots__ = ('energy', 'repeat', 'pitch', 'k', 'stop', 'silent')

    def __init__(self):
        self.energy = 0.0
        self.repeat = False
        self.pitch  = 0
        self.k      = [0] * 10     # integer K values (÷512 in filter)
        self.stop   = False
        self.silent = False

# ---------------------------------------------------------------------------
# LPC decoder
# ---------------------------------------------------------------------------

def decode_lpc_frames(data: bytes) -> List[LPCFrame]:
    """Decode TMS5220 bit-packed LPC frames from raw bytes."""
    bs = BitStream(data)
    frames: List[LPCFrame] = []
    while not bs.exhausted:
        frm = LPCFrame()
        energy_idx = bs.read_bits(ENERGY_BITS)
        frm.energy = ENERGY_TABLE[energy_idx]

        if energy_idx == 0:
            frm.silent = True
            frames.append(frm)
            continue

        if energy_idx == 15:
            frm.stop = True
            frames.append(frm)
            break

        frm.repeat = bool(bs.read_bits(REPEAT_BITS))
        frm.pitch  = bs.read_bits(PITCH_BITS)

        if not frm.repeat:
            # Voiced: K1-K10; Unvoiced (pitch=0): K1-K4 only
            num_k = 10 if frm.pitch > 0 else 4
            for i in range(num_k):
                idx = bs.read_bits(K_BITS[i])
                frm.k[i] = K_TABLES[i][idx] if idx < len(K_TABLES[i]) else 0

        frames.append(frm)
    return frames


def lpc_byte_length(data: bytes) -> int:
    """
    Return the number of bytes consumed by the LPC bitstream up through and
    including the STOP frame, or len(data) if no STOP frame is found.
    """
    bs = BitStream(data)
    while not bs.exhausted:
        energy_idx = bs.read_bits(ENERGY_BITS)
        if energy_idx == 15:   # STOP frame
            return bs.byte_position
        if energy_idx == 0:    # silent frame — no more bits for this frame
            continue
        bs.read_bits(REPEAT_BITS)
        pitch  = bs.read_bits(PITCH_BITS)
        repeat = False   # we consumed repeat above; reconstruct flag from already-read bit
        # The repeat bit was already consumed; we need to know it to decide K count.
        # Re-do properly: rewind and re-read repeat.
        # Actually, we already read it — but we discarded it. Fix: read all fields in order.
        # This function reads: energy(4), repeat(1), pitch(6), [K params]
        # We already consumed those bits. Now handle K params:
        # But we don't know 'repeat' anymore. Workaround: always try to read K1-K10
        # bits — for a correct stream the STOP frame will appear eventually.
        # Better approach: use the full decoder:
        break
    # Fall back to full decoder for byte-count
    frames = decode_lpc_frames(data)
    bs2 = BitStream(data)
    for frm in frames:
        bs2.read_bits(ENERGY_BITS)
        if frm.stop:
            return bs2.byte_position
        if frm.silent:
            continue
        bs2.read_bits(REPEAT_BITS)
        bs2.read_bits(PITCH_BITS)
        if not frm.repeat:
            num_k = 10 if frm.pitch > 0 else 4
            for i in range(num_k):
                bs2.read_bits(K_BITS[i])
    return len(data)

# ---------------------------------------------------------------------------
# LPC audio synthesis
# ---------------------------------------------------------------------------

def render_phrase_to_pcm(lpc_data: bytes, gain: float = 0.75) -> bytes:
    """Decode TMS5220 LPC data and return raw signed 16-bit mono PCM at 8 kHz.

    Uses PyTI_LPC_CMD's LPCSynthesizer when available (preferred).  The native
    EPROM bytes are bit-reversed before passing to PyTI because its
    BitstreamReader applies per-byte bit-reversal internally; double-reversing
    restores the native reading order.

    Falls back to the built-in synthesizer when PyTI is not importable.
    gain: audio gain factor (default 0.75, matching ti_lpc_cmd default of 75%).
    """
    if _HAS_PYTI:
        # Bit-reverse each byte so PyTI's internal reversal restores native order
        rev_data = bytes(_BIT_REVERSE[b] for b in lpc_data)
        synth = _LPCSynthesizer()
        float_samples = synth.synthesize(rev_data, _TMS5220_PARAMS)
        peak = 32767
        pcm = [max(-32768, min(32767, int(round(s * gain * peak))))
               for s in float_samples]
        return struct.pack(f"<{len(pcm)}h", *pcm)

    # --- Built-in fallback synthesizer ---
    frames = decode_lpc_frames(lpc_data)
    x_state     = [0.0] * 10
    prev_k      = [0] * 10          # integer K values (÷512 in filter)
    pcm_samples: List[int] = []

    synth_rand    = 1       # LFSR initialised to 1 per utterance
    period_ctr    = 0       # chirp position within current pitch period
    drain_frames  = 0       # count-down after STOP frame

    frame_idx = 0
    while frame_idx < len(frames):
        frm = frames[frame_idx]
        frame_idx += 1

        if frm.stop:
            # Render 2 silent drain frames so the lattice filter can decay
            drain_frames = 2
            for _ in range(drain_frames):
                for _ in range(SAMPLES_PER_FRAME):
                    u = [0.0] * 11  # u[10] = 0 excitation
                    for i in range(9, -1, -1):
                        u[i] = u[i + 1] - (prev_k[i] / 512.0) * x_state[i]
                    for i in range(9, 0, -1):
                        x_state[i] = x_state[i - 1] + (prev_k[i - 1] / 512.0) * u[i - 1]
                    x_state[0] = u[0]
                    u0 = max(-1.0, min(1.0, u[0]))
                    pcm_samples.append(max(-32768, min(32767, int(u0 * 1.5 * 32767))))
            break

        k = frm.k if not frm.repeat else prev_k
        period = PITCH_TABLE[frm.pitch] if frm.pitch < len(PITCH_TABLE) else 0
        voiced = (period > 0)

        if not frm.silent and not frm.repeat:
            prev_k = list(k)

        for _ in range(SAMPLES_PER_FRAME):
            if frm.silent:
                pcm_samples.append(0)
                continue

            # --- Excitation ---
            if voiced:
                if period_ctr < len(CHIRP):
                    u10 = (CHIRP[period_ctr] / 256.0) * (frm.energy / 256.0)
                else:
                    u10 = 0.0
                if period_ctr >= period - 1:
                    period_ctr = 0
                else:
                    period_ctr += 1
            else:
                synth_rand = (synth_rand >> 1) ^ (0xB800 if (synth_rand & 1) else 0)
                noise = frm.energy if (synth_rand & 1) else -frm.energy
                u10 = noise / 2048.0

            # --- 10-stage PARCOR lattice filter ---
            # K values are integer (×512); divide by 512.0 to get reflection coeff.
            # Forward pass: u[i] = u[i+1] - (k[i]/512)*x[i]
            u = [0.0] * 11
            u[10] = u10
            for i in range(9, -1, -1):
                u[i] = u[i + 1] - (k[i] / 512.0) * x_state[i]

            u[0] = max(-1.0, min(1.0, u[0]))

            # Backward pass (per spec): x[i] = x[i-1] + (k[i-1]/512)*u[i-1]
            for i in range(9, 0, -1):
                x_state[i] = x_state[i - 1] + (k[i - 1] / 512.0) * u[i - 1]
            x_state[0] = u[0]

            if not math.isfinite(u[0]):
                x_state = [0.0] * 10
                pcm_samples.append(0)
                continue

            out = max(-32768, min(32767, int(u[0] * 1.5 * 32767)))
            pcm_samples.append(out)

    return struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)


def write_wav(pcm_data: bytes, path: str, sample_rate: int = 8000):
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)

# ---------------------------------------------------------------------------
# Voice EPROM phrase table access
# ---------------------------------------------------------------------------

def lookup_phrase_offset(voice_data: bytes, group: int, phrase_id: int) -> Optional[int]:
    """
    Linear-search the group's 3-byte entry table for phrase_id.
    Returns the 2-byte LE offset stored in bytes 1-2 of the matching entry,
    or None if not found.
    """
    if group not in PHRASE_GROUPS:
        return None
    table_off, count = PHRASE_GROUPS[group]
    for i in range(count):
        pos = table_off + i * 3
        if pos + 3 > len(voice_data):
            break
        if voice_data[pos] == phrase_id:
            return voice_data[pos + 1] | (voice_data[pos + 2] << 8)
    return None


def iter_all_phrases(voice_data: bytes) -> Iterator[Tuple[int, int, int, int]]:
    """
    Yield (group, phrase_id, start_offset, end_offset) for every phrase in
    the voice EPROM, in table order.  end_offset is determined by scanning for
    the STOP frame; it is the byte offset just past the STOP frame bits.
    """
    for group in sorted(PHRASE_GROUPS):
        table_off, count = PHRASE_GROUPS[group]
        entries = []
        for i in range(count):
            pos = table_off + i * 3
            pid    = voice_data[pos]
            offset = voice_data[pos + 1] | (voice_data[pos + 2] << 8)
            entries.append((pid, offset))

        for pid, start in entries:
            # Note: ERROR_SENTINEL (0x81F1) is NOT filtered here — it is also the
            # real start address of Pause 1 (word 960, group 9 phrase_id 0x3C).
            if start < LPC_DATA_START or start >= len(voice_data):
                continue
            # Limit scan to the LPC data region + a small buffer for a STOP frame
            # that straddles the boundary.  Without this cap, clips near the end of
            # the LPC region (e.g. Good Afternoon, Good Evening) would scan into the
            # 0x00-fill area, misinterpreting zero bytes as SILENCE frames and
            # producing a bogus end address far into the non-LPC region.
            hard_limit = min(start + 16384, LPC_DATA_END + 4, len(voice_data))
            end_rel    = lpc_byte_length(voice_data[start:hard_limit])
            end_abs    = start + end_rel
            # If no STOP frame was found, cap at LPC_DATA_END
            if end_abs > LPC_DATA_END:
                end_abs = LPC_DATA_END
            yield group, pid, start, end_abs


def generate_phrase_table(voice_data: bytes) -> List[dict]:
    """
    Walk every phrase and return rows for CSV output.
    word_text is set to 'wordNNN' as a placeholder — edit the CSV to add labels.
    """
    rows = []
    for group, pid, start, end in iter_all_phrases(voice_data):
        word_number = group * 100 + pid
        rows.append({
            'word_number':   word_number,
            'word_text':     f'word{word_number}',
            'address_pairs': [(start, end)],
            'undocumented':  False,
        })
    return rows

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(text: str) -> str:
    return re.sub(r'[^\w\-.()\[\] ]+', '_', text).strip()


def parse_address(value: str) -> int:
    value = value.strip()
    if value.lower().startswith('0x'):
        return int(value, 16)
    return int(value)

# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def load_csv(path: str) -> List[dict]:
    """Load CSV with variable-column format:
        word_number,"word_text",0xSTART1,0xEND1[,0xSTART2,0xEND2,...][,undocumented]

    Accepts both:
      - New format: no header row, word_number is a zero-padded 3-digit string
      - Legacy format: header row present (word_number,word_text,start_address,end_address)
    Any row whose first column is not a valid integer is silently skipped (header or comment).
    """
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader, 1):
            if not row or not row[0].strip():
                continue
            try:
                word_number = int(row[0].strip())
            except ValueError:
                continue  # skip header rows and comment lines
            try:
                word_text   = row[1].strip()
                cols = [c.strip() for c in row[2:] if c.strip()]
                undocumented = False
                if cols and cols[-1].lower() == 'undocumented':
                    undocumented = True
                    cols = cols[:-1]
                if len(cols) < 2 or len(cols) % 2 != 0:
                    print(f"WARNING: skipping CSV row {i}: expected pairs of addresses, "
                          f"got {len(cols)} column(s)", file=sys.stderr)
                    continue
                address_pairs = []
                for j in range(0, len(cols), 2):
                    address_pairs.append((parse_address(cols[j]), parse_address(cols[j + 1])))
                rows.append({
                    'word_number':   word_number,
                    'word_text':     word_text,
                    'address_pairs': address_pairs,
                    'undocumented':  undocumented,
                })
            except (IndexError, ValueError) as exc:
                print(f"WARNING: skipping CSV row {i}: {exc}", file=sys.stderr)
    return rows

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

# Pre-computed bit-reversal lookup table (reverses all 8 bits of a byte)
_BIT_REVERSE = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))


def _apply_endian(data: bytes, endian: str) -> bytes:
    """Return data in the requested bit-endianness.

    The TMS5220 data bus has D0 labeled as the MSB (TI convention).  When the
    CPU writes a byte to port 0x0180, bit 0 of that byte arrives on D0 and is
    processed first by the LPC decoder.  This is what we call 'lsb' order
    (bit 0 first per byte) and it is the native, correct format for synthesis.

    endian='lsb'  — native D0-first order (bit 0 first; correct for TMS5220)
    endian='msb'  — bit-reversed per byte (for external tools expecting
                    conventional MSB-first packing)
    """
    if endian == 'msb':
        return bytes(_BIT_REVERSE[b] for b in data)
    return data  # 'lsb' — no transformation


def export_phrases(rows: List[dict], voice_data: bytes,
                   output_dir: str, make_wav: bool, verbose: bool,
                   endian: str = 'msb', wav_dir: str = None):
    os.makedirs(output_dir, exist_ok=True)
    if wav_dir is None:
        wav_dir = output_dir
    if make_wav and wav_dir != output_dir:
        os.makedirs(wav_dir, exist_ok=True)
    ok = err = 0

    for row in rows:
        num   = row['word_number']
        text  = _safe_filename(row['word_text'])
        pairs = row['address_pairs']

        # Build contiguous LPC bytes from potentially multiple EPROM segments.
        # For multi-segment words, the STOP frame of each non-final segment is
        # trimmed (last byte dropped) before concatenation.
        segments = []
        valid = True
        for idx, (start, end) in enumerate(pairs):
            if start >= end:
                print(f"SKIP  {num:>4}  {text!r}: segment {idx}: "
                      f"start >= end ({start:#06x} >= {end:#06x})", file=sys.stderr)
                valid = False
                break
            if end > len(voice_data):
                print(f"WARN  {num:>4}  {text!r}: segment {idx}: end {end:#06x} beyond "
                      f"EPROM size {len(voice_data):#06x}, clamping", file=sys.stderr)
                end = len(voice_data)
            seg = voice_data[start:end]
            if idx < len(pairs) - 1 and len(seg) > 0:
                # Strip the STOP frame byte so the decoder continues into the next segment
                stop_len = lpc_byte_length(seg)
                seg = seg[:max(0, stop_len - 1)]
            segments.append(seg)
        if not valid:
            err += 1
            continue

        lpc_native = b''.join(segments)
        lpc_out    = _apply_endian(lpc_native, endian)
        base       = f"{num:04d}_{text}"

        bin_path = os.path.join(output_dir, base + '.lpc')
        with open(bin_path, 'wb') as f:
            f.write(lpc_out)

        if make_wav:
            wav_path = os.path.join(wav_dir, base + '.wav')
            try:
                pcm = render_phrase_to_pcm(lpc_native)  # always synthesize from native LSB-first data
                write_wav(pcm, wav_path)
                if verbose:
                    dur_ms = len(pcm) // 2 * 1000 // 8000
                    print(f"OK  {num:>4}  {text!r}  {len(lpc_out):>5} B LPC"
                          f"{dur_ms:>4} ms  → {base}.lpc/.wav")
            except Exception as exc:
                print(f"WARN  {num:>4}  {text!r}: WAV failed: {exc}", file=sys.stderr)
                if verbose:
                    print(f"OK  {num:>4}  {text!r}  {len(lpc_out):>5} B LPC→ {base}.lpc")
        else:
            if verbose:
                print(f"OK  {num:>4}  {text!r}  {len(lpc_out):>5} B LPC→ {base}.lpc")
        ok += 1

    print(f"\nExported {ok} phrase(s), {err} skipped.")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description='Export TMS5220 LPC phrase data from the CAT-1000 voice EPROM.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('csv', nargs='?',
                    help='Input CSV (word_number,word_text,start_address,end_address)')
    ap.add_argument('--voice', default=DEFAULT_VOICE_PATH,
                    help='Voice EPROM BIN path (default: eprom_images/cat-1000-voice_27SF512.BIN)')
    ap.add_argument('-o', '--output', default='lpc_phrases',
                    help='Output directory for .lpc files (default: lpc_phrases/)')
    ap.add_argument('--wav-dir', default=None, metavar='DIR',
                    help='Separate output directory for .wav files (default: same as -o)')
    ap.add_argument('--wav', action='store_true',
                    help='Synthesize each phrase to a WAV file as well')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='Suppress per-file output lines')
    ap.add_argument('--endian', choices=['lsb', 'msb'], default='msb',
                    help='Bit endianness of output .lpc files (default: msb). '
                         '"lsb" = native TMS5220 order: bit 0 (D0) first per byte '
                         '(correct for direct TMS5220/TSP53C30 streaming). '
                         '"msb" = bit-reversed per byte (default); use for tools that '
                         'expect conventional MSB-first byte packing.')
    ap.add_argument('--dump-csv', action='store_true',
                    help='Print a starter CSV from the built-in phrase table and exit. '
                         'Names are auto-loaded from cat-1000_phrases.csv if present; '
                         'unmatched entries use word{N} placeholders. '
                         'Redirect output to a file, then re-import.')
    args = ap.parse_args()

    try:
        with open(args.voice, 'rb') as f:
            voice_data = f.read()
    except FileNotFoundError:
        print(f"ERROR: voice EPROM not found: {args.voice}", file=sys.stderr)
        sys.exit(1)

    if len(voice_data) != 65536:
        print(f"WARNING: expected 65536 bytes, got {len(voice_data)}", file=sys.stderr)

    if args.dump_csv:
        # Load names and undocumented flags from cat-1000_phrases.csv if present.
        # Falls back to 'word{N}' placeholder for any unmatched word number.
        phrase_names: Dict[int, str] = {}
        undoc_words: set = set()
        phrases_path = os.path.join(SCRIPT_DIR, 'cat-1000_phrases.csv')
        if os.path.exists(phrases_path):
            with open(phrases_path, newline='', encoding='utf-8') as pf:
                for prow in csv.reader(pf):
                    try:
                        wn = int(prow[0].strip())
                        phrase_names[wn] = prow[1].strip()
                        if len(prow) > 4 and prow[4].strip().lower() == 'undocumented':
                            undoc_words.add(wn)
                    except (ValueError, IndexError):
                        continue

        rows = generate_phrase_table(voice_data)
        # Format (no header row): NNN,"name",0xSTART,0xEND[,0xSTART2,0xEND2][,"undocumented"]
        import io as _io
        buf = _io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_NONNUMERIC)
        for row in rows:
            num  = row['word_number']
            text = phrase_names.get(num, f'word{num}')
            cols = [f"{num:03d}", text]
            for start, end in row['address_pairs']:
                cols += [f'0x{start:04X}', f'0x{end:04X}']
            if num in undoc_words:
                cols.append('undocumented')
            w.writerow(cols)
        sys.stdout.write(buf.getvalue())
        return

    if not args.csv:
        ap.print_help()
        sys.exit(0)

    rows = load_csv(args.csv)
    if not rows:
        print("ERROR: no valid rows in CSV", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(rows)} phrase(s) from {args.csv}")
    print(f"Voice EPROM: {args.voice}  ({len(voice_data)} bytes)")
    print(f"Output dir:  {args.output}")
    print(f"Bit endian:  {args.endian.upper()}-first"
          f"{'  (native TMS5220 D0-first)' if args.endian == 'lsb' else '  (bit-reversed; default)'}")
    print(f"WAV output:  {'yes' if args.wav else 'no'}")
    print()

    export_phrases(rows, voice_data,
                   output_dir=args.output,
                   make_wav=args.wav,
                   verbose=not args.quiet,
                   endian=args.endian,
                   wav_dir=args.wav_dir)


if __name__ == '__main__':
    main()

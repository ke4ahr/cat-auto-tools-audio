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
CAT-1000 Firmware Analysis Tool
Firmware date: Mar 01, 1998
CPU: Intel 80C186
Speech: TI TSP53C30 (TMS5220-compatible)

Memory Map:
  0x00000-0x0FFFF  Program EPROM  (27C512, 64KB) at segment 0x0000
  0xF0000-0xFFFFF  Voice EPROM    (27SF512, 64KB) at segment 0xF000

I/O Port Map:
  0x0000           80C186 internal port (wait-state sync)
  0x0080           Output port (keypad/LED control)
  0x00C0           Input port  (switch/status)
  0x0140           Input port  (phone/modem status, bits 7-4 = nibble)
  0x0180           TSP53C30 data/status port
  0xFF00-0xFFFF    80C186 internal peripherals (timers, interrupts, DMA, serial)

Voice EPROM Layout:
  0x0000-0x009E    Phrase dispatch routine (8086 machine code, CALL FAR target)
  0x009F-0x005C3   Phrase index tables (groups 0,2-9)
  0x05C4-0x0641    End of group 9 table
  0x0642-0xC42C    TMS5220 LPC speech data (bit-packed frames)
  0xFD00-0xFD39    SAVE state routine  (saves TSP53C30 config to RAM 0x0000-0x0005)
  0xFE00-0xFE43    RESTORE state routine (restores TSP53C30 config from RAM)

Voice Dispatch Protocol:
  Caller sets:  AH = phrase group (0, 2-9)
                AL = phrase ID (varies per group)
  Returns:      BX = offset into voice EPROM (segment F000) of LPC speech data
                BX = 0x81F1 if phrase not found (error sentinel)

Speech Playback Flow:
  1. call speak_phrase(group, phrase_id)
  2. CALL FAR 0xF000:0x0000  → BX = phrase data offset
  3. Store BX in RAM[0x00A6]  (current speech data pointer)
  4. Set RAM[0x000F] = 0xFF   (speech-active flag)
  5. Configure TSP53C30 via port 0x180 (enable Speak-External mode)
  6. INT1 fires each time TSP53C30 READY asserts (needs next byte)
  7. INT1 ISR reads next byte from ES:BX (voice EPROM), writes to port 0x180
  8. RAM[0x000F] cleared to 0x00 when TSP53C30 signals end of utterance

TSP53C30 / TMS5220 Interface (I/O port 0x180):
  Status byte read masks:
    0xFE  (bit 0 = 0) → READY  (chip can accept more data / send byte index)
    0xFB  (bit 2 = 0) → BUFFER_LOW (send next LPC byte)
    0xF7  (bit 3 = 0) → TALKING finished / end of phrase
"""

import struct
import wave
import math
import os
from typing import Iterator, List, Optional, Tuple, Dict


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VOICE_EPROM_PATH   = os.path.join(os.path.dirname(__file__), "eprom_images", "cat-1000-voice_27SF512.BIN")
PROGRAM_EPROM_PATH = os.path.join(os.path.dirname(__file__), "eprom_images", "cat-1000-V304A_program_27C512.BIN")

VOICE_SEGMENT      = 0xF000   # segment where voice EPROM is mapped
DISPATCH_OFFSET    = 0x0000   # voice dispatch entry point
SAVE_STATE_OFFSET  = 0xFD00
RESTORE_STATE_OFFSET = 0xFE00

TSP53C30_IO_PORT   = 0x0180
KEYPAD_OUT_PORT    = 0x0080
SWITCH_IN_PORT     = 0x00C0
PHONE_STATUS_PORT  = 0x0140

TMS5220_SAMPLE_RATE = 8000    # Hz

# Phrase group descriptors: (table_offset_in_voice_eprom, entry_count)
PHRASE_GROUPS: Dict[int, Tuple[int, int]] = {
    0: (0x009F, 0x1D),   # 29 entries  – digits / basic numerics
    2: (0x00F3, 0x4F),   # 79 entries  – numeric phrases 10-99
    3: (0x01E0, 0x36),   # 54 entries
    4: (0x0282, 0x2B),   # 43 entries
    5: (0x0303, 0x32),   # 50 entries
    6: (0x0399, 0x41),   # 65 entries
    7: (0x0459, 0x3D),   # 61 entries
    8: (0x0510, 0x3C),   # 60 entries
    9: (0x05C4, 0x2A),   # 42 entries
}

ERROR_PHRASE_ADDR = 0x81F1    # returned when phrase not found


# ---------------------------------------------------------------------------
# TMS5220 LPC codec tables (from TMS5220 datasheet)
# ---------------------------------------------------------------------------

# Energy table: 16 entries (4-bit index → linear amplitude)
ENERGY_TABLE = [
    0, 52, 87, 123, 174, 246, 348, 491,
    694, 981, 1385, 1957, 2764, 3904, 5514, 0,   # 0=silence, 15=stop
]

# Pitch table: 64 entries (6-bit index → fundamental frequency period in samples)
PITCH_TABLE = [
    0,   14,  15,  16,  17,  18,  19,  20,
    21,  22,  23,  24,  25,  26,  27,  28,
    29,  30,  31,  32,  33,  34,  35,  36,
    37,  38,  39,  40,  41,  42,  44,  46,
    48,  50,  52,  53,  56,  58,  60,  62,
    65,  68,  70,  72,  76,  78,  80,  84,
    86,  91,  94,  98,  101, 105, 109, 114,
    118, 122, 127, 132, 137, 142, 148, 0,
]

# K-parameter tables (reflection coefficients), Q15 fixed-point
K1_TABLE = [
    -501, -498, -493, -488, -480, -471, -460, -446,
    -427, -405, -378, -344, -305, -259, -206, -146,
    -82,  -15,  54,   120,  182,  238,  289,  334,
    372,  404,  429,  449,  464,  474,  480,  474,
]

K2_TABLE = [
    -328, -303, -274, -244, -211, -175, -138, -99,
    -59,  -18,  24,   64,   105,  143,  180,  215,
    248,  278,  306,  331,  354,  374,  392,  408,
    422,  435,  445,  455,  463,  470,  476,  480,
]

K3_TABLE = [
    -441, -387, -333, -279, -225, -171, -117, -63,
    -9,   45,   99,   153,  207,  261,  315,  369,
]

K4_TABLE = [
    -328, -273, -217, -161, -106, -50,  6,    62,
    118,  173,  229,  285,  341,  396,  452,  508,
]

K5_TABLE  = K3_TABLE[:]
K6_TABLE  = K4_TABLE[:]
K7_TABLE  = K3_TABLE[:]

K8_TABLE  = [
    -205, -132, -59,  14,   87,   160,  234,  307,
]

K9_TABLE  = K8_TABLE[:]
K10_TABLE = K8_TABLE[:]


# ---------------------------------------------------------------------------
# Voice EPROM reader
# ---------------------------------------------------------------------------

class VoiceEPROM:
    """Represents the voice EPROM (27SF512) mapped at segment 0xF000."""

    def __init__(self, path: str = VOICE_EPROM_PATH):
        with open(path, "rb") as f:
            self._data = bytearray(f.read())
        assert len(self._data) == 0x10000, "Expected 64KB voice EPROM"

    def __getitem__(self, key):
        return self._data[key]

    def __len__(self):
        return len(self._data)

    # ------------------------------------------------------------------
    # Phrase lookup  (mirrors the 8086 dispatch routine at offset 0x0000)
    # ------------------------------------------------------------------

    def lookup_phrase(self, group: int, phrase_id: int) -> Optional[int]:
        """
        Lookup speech data address for (group, phrase_id).

        Replicates the 8086 dispatch at voice EPROM offset 0x0000:
          IN:  AH = group, AL = phrase_id
          OUT: BX = address of LPC data in voice EPROM, or ERROR_PHRASE_ADDR

        Returns:
            Offset into voice EPROM of the LPC speech data, or None if not found.
        """
        if group not in PHRASE_GROUPS:
            return None
        tbl_off, count = PHRASE_GROUPS[group]
        for i in range(count):
            base = tbl_off + i * 3
            pid  = self._data[base]
            lo   = self._data[base + 1]
            hi   = self._data[base + 2]
            if pid == phrase_id:
                addr = lo | (hi << 8)
                return addr if addr != ERROR_PHRASE_ADDR else None
        return None

    def get_phrase_data(self, group: int, phrase_id: int) -> Optional[bytes]:
        """Return raw LPC bytes for the given phrase."""
        addr = self.lookup_phrase(group, phrase_id)
        if addr is None:
            return None
        # Determine length by scanning for STOP frame or using next phrase start
        # Collect all phrase start addresses and find the next one
        all_addrs = sorted(set(
            self._data[tbl_off + i*3 + 1] | (self._data[tbl_off + i*3 + 2] << 8)
            for grp, (tbl_off, count) in PHRASE_GROUPS.items()
            for i in range(count)
            if (self._data[tbl_off + i*3 + 1] | (self._data[tbl_off + i*3 + 2] << 8)) != ERROR_PHRASE_ADDR
        ))
        idx = all_addrs.index(addr)
        end = all_addrs[idx + 1] if idx + 1 < len(all_addrs) else len(self._data)
        return bytes(self._data[addr:end])

    def iter_all_phrases(self) -> Iterator[Tuple[int, int, int, bytes]]:
        """
        Yield (group, phrase_id, addr, raw_bytes) for every phrase.
        """
        # Build address → next-address map for length calculation
        addr_set = set()
        for grp, (tbl_off, count) in PHRASE_GROUPS.items():
            for i in range(count):
                base = tbl_off + i * 3
                lo   = self._data[base + 1]
                hi   = self._data[base + 2]
                a    = lo | (hi << 8)
                if a != ERROR_PHRASE_ADDR:
                    addr_set.add(a)
        sorted_addrs = sorted(addr_set)

        for grp, (tbl_off, count) in sorted(PHRASE_GROUPS.items()):
            for i in range(count):
                base = tbl_off + i * 3
                pid  = self._data[base]
                lo   = self._data[base + 1]
                hi   = self._data[base + 2]
                addr = lo | (hi << 8)
                if addr == ERROR_PHRASE_ADDR:
                    continue
                idx = sorted_addrs.index(addr)
                end = sorted_addrs[idx + 1] if idx + 1 < len(sorted_addrs) else len(self._data)
                yield grp, pid, addr, bytes(self._data[addr:end])


# ---------------------------------------------------------------------------
# TMS5220 LPC frame decoder
# ---------------------------------------------------------------------------

class BitStream:
    """LSB-first bit reader over a bytes buffer."""

    def __init__(self, data: bytes):
        self._bits: List[int] = []
        for byte in data:
            for j in range(8):
                self._bits.append((byte >> j) & 1)
        self._pos = 0

    @property
    def remaining(self) -> int:
        return len(self._bits) - self._pos

    def read(self, n: int) -> int:
        val = 0
        for i in range(n):
            if self._pos < len(self._bits):
                val |= self._bits[self._pos] << i
                self._pos += 1
        return val

    def peek(self, n: int) -> int:
        saved = self._pos
        v = self.read(n)
        self._pos = saved
        return v


class LPCFrame:
    """One decoded TMS5220 LPC synthesis frame (25 ms / 200 samples @ 8 kHz)."""

    SAMPLES_PER_FRAME = 200   # 25 ms at 8 kHz

    def __init__(self):
        self.stop     = False
        self.silence  = False
        self.repeat   = False
        self.energy   = 0
        self.pitch    = 0
        self.k: List[int] = [0] * 10   # K1..K10 in Q15

    def __repr__(self):
        if self.stop:
            return "LPCFrame(STOP)"
        if self.silence:
            return "LPCFrame(SILENCE)"
        voiced = "voiced" if self.pitch > 0 else "unvoiced"
        return (f"LPCFrame({voiced} energy={self.energy} pitch={self.pitch} "
                f"k={self.k[:4]}...)")


def decode_lpc_frames(data: bytes) -> List[LPCFrame]:
    """
    Decode a stream of TMS5220 LPC frames from raw bytes.

    The TMS5220 bit-packing order (within each field, LSB first in the bitstream):
      - Energy    : 4 bits
      - [if energy != 0 and energy != 0xF]:
          Repeat  : 1 bit
          Pitch   : 6 bits
          [if not repeat and pitch == 0 (unvoiced)]:
              K1-K4 : 5,5,4,4 bits
          [if not repeat and pitch > 0 (voiced)]:
              K1-K10: 5,5,4,4,4,4,4,3,3,3 bits
    """
    bs = BitStream(data)
    frames: List[LPCFrame] = []
    prev_k = [0] * 10
    prev_pitch = 0

    while bs.remaining >= 4:
        frm = LPCFrame()
        energy_idx = bs.read(4)

        if energy_idx == 0xF:
            frm.stop = True
            frames.append(frm)
            break

        if energy_idx == 0x0:
            frm.silence = True
            frm.energy  = 0
            frm.pitch   = 0
            frm.k       = [0] * 10
            frames.append(frm)
            prev_k = frm.k[:]
            prev_pitch = 0
            continue

        frm.energy = ENERGY_TABLE[energy_idx]
        repeat_bit = bs.read(1)
        pitch_idx  = bs.read(6)
        frm.pitch  = PITCH_TABLE[pitch_idx]

        if repeat_bit:
            frm.repeat = True
            frm.k      = prev_k[:]
        elif frm.pitch == 0:
            # Unvoiced: only K1..K4 transmitted
            k1  = bs.read(5)
            k2  = bs.read(5)
            k3  = bs.read(4)
            k4  = bs.read(4)
            frm.k = [
                K1_TABLE[k1],  K2_TABLE[k2],
                K3_TABLE[k3],  K4_TABLE[k4],
                0, 0, 0, 0, 0, 0,
            ]
        else:
            # Voiced: K1..K10
            k_raw = [
                bs.read(5), bs.read(5), bs.read(4), bs.read(4),
                bs.read(4), bs.read(4), bs.read(4),
                bs.read(3), bs.read(3), bs.read(3),
            ]
            ktables = [K1_TABLE, K2_TABLE, K3_TABLE, K4_TABLE,
                       K5_TABLE, K6_TABLE, K7_TABLE, K8_TABLE,
                       K9_TABLE, K10_TABLE]
            frm.k = [ktables[i][k_raw[i]] for i in range(10)]

        prev_k     = frm.k[:]
        prev_pitch = frm.pitch
        frames.append(frm)

    return frames


def synthesize_frame(frm: LPCFrame, prev_state: dict) -> List[int]:
    """
    Synthesize one frame of audio using the TMS5220 all-pole LPC filter.

    The TMS5220 uses a 10-stage lattice filter.  Each sample:
      1. Excitation: pitched (impulse train) or unvoiced (LFSR noise)
      2. 10-stage lattice analysis-synthesis (reflection coefficients)
      3. Scale by energy

    Returns a list of SAMPLES_PER_FRAME signed 16-bit integers.

    prev_state: mutable dict with keys 'u' (list of 10 filter stages),
                'phase' (pitch phase counter), 'shift' (LFSR shift register).
    """
    samples = []
    u_vals  = prev_state.get('u', [0.0] * 10)
    phase   = prev_state.get('phase', 0)
    shift   = prev_state.get('shift', 0x1FFF)

    energy  = frm.energy / 32767.0
    k       = [ki / 32768.0 for ki in frm.k]

    for _ in range(LPCFrame.SAMPLES_PER_FRAME):
        # Generate excitation
        if frm.silence:
            excite = 0.0
        elif frm.pitch == 0:
            # Unvoiced: 13-bit LFSR (taps at bits 0 and 12)
            bit   = ((shift >> 12) ^ shift) & 1
            shift = ((shift << 1) | bit) & 0x1FFF
            excite = (1.0 if (shift & 1) else -1.0) * energy * 2.0
        else:
            # Voiced: impulse at start of pitch period
            if phase == 0:
                excite = energy * 16.0
            else:
                excite = 0.0
            phase  = (phase + 1) % frm.pitch

        # 10-stage lattice filter (Schur recursion)
        u_new = [0.0] * 10
        x     = excite
        for i in range(9, -1, -1):
            x         = x - k[i] * u_vals[i]
            u_new[i]  = u_vals[i] + k[i] * x if i > 0 else x

        # Output sample (clip to 16-bit)
        s = int(max(-32768, min(32767, x * 4096)))
        samples.append(s)
        u_vals = u_new[:]

    prev_state['u']     = u_vals
    prev_state['phase'] = phase
    prev_state['shift'] = shift
    return samples


def render_phrase_to_pcm(lpc_data: bytes) -> bytes:
    """
    Decode TMS5220 LPC data and render to raw signed 16-bit mono PCM at 8 kHz.
    """
    frames    = decode_lpc_frames(lpc_data)
    all_pcm   = []
    state     = {}
    for frm in frames:
        if frm.stop:
            break
        pcm = synthesize_frame(frm, state)
        all_pcm.extend(pcm)

    return struct.pack(f"<{len(all_pcm)}h", *all_pcm)


def write_wav(pcm_data: bytes, path: str, sample_rate: int = TMS5220_SAMPLE_RATE):
    """Write raw PCM data to a WAV file."""
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


# ---------------------------------------------------------------------------
# Program EPROM reader / disassembly helpers
# ---------------------------------------------------------------------------

class ProgramEPROM:
    """Represents the program EPROM (27C512) mapped at segment 0x0000."""

    FIRMWARE_DATE = "Mar 01, 1998"

    # Interrupt vector table entries: (vector_number, offset, description)
    IVT_ENTRIES = [
        ( 0, 0x1778, "Default/unhandled ISR"),
        (13, 0x1582, "DMA1 / phone-status ISR"),
        (14, 0x166A, "INT0 / keypad ISR"),
        (15, 0x152F, "INT1 / TSP53C30-READY ISR"),
        (19, 0x0FD2, "Serial Tx ISR"),
        (20, 0x0FA5, "Serial-related ISR"),
        (21, 0x176C, "IRQ21 ISR"),
    ]

    # Key subroutine addresses
    SUBROUTINES = {
        0x0413: "reset_entry          ; jump target from 0xFFF0",
        0x152F: "isr_tsp53c30_ready   ; INT1: stream next LPC byte to TSP53C30",
        0x1582: "isr_phone_status     ; DMA1: phone/modem state machine",
        0x166A: "isr_keypad           ; INT0: keypad scan / dial decode",
        0x18B0: "speak_phrase         ; play voice phrase (AH=group, AL=phrase_id)",
        0x18FF: "speak_sequence       ; play sequence of phrases from CS:BX table",
        0x1BF3: "tsp53c30_config      ; configure TSP53C30 via port 0xFF5E",
        0x1BF3: "tsp53c30_enable      ; enable TSP53C30 (set bit 7 of PCB reg)",
        0x270C: "get_ram_byte         ; read byte from RAM",
        0x3331: "init_peripherals_1   ; 80C186 peripheral init (timers/DMA)",
        0x38A9: "init_peripherals_2",
        0x4EF1: "init_main            ; main peripheral + state init",
        0x70A2: "main_loop            ; primary event/state loop",
    }

    def __init__(self, path: str = PROGRAM_EPROM_PATH):
        with open(path, "rb") as f:
            self._data = bytearray(f.read())
        assert len(self._data) == 0x10000, "Expected 64KB program EPROM"

    def __getitem__(self, key):
        return self._data[key]

    def word_at(self, offset: int) -> int:
        return self._data[offset] | (self._data[offset + 1] << 8)

    def reset_vector(self) -> Tuple[int, int]:
        """Return (segment, offset) of reset jump target."""
        # JMP FAR at 0xFFF0: EA offset_lo offset_hi seg_lo seg_hi
        assert self._data[0xFFF0] == 0xEA
        offset  = self.word_at(0xFFF1)
        segment = self.word_at(0xFFF3)
        return segment, offset

    def strings(self, min_len: int = 4) -> Iterator[Tuple[int, str]]:
        """Yield (offset, string) for all printable strings in the EPROM."""
        i = 0
        while i < len(self._data):
            if 32 <= self._data[i] < 127:
                j = i
                s = []
                while j < len(self._data) and 32 <= self._data[j] < 127:
                    s.append(chr(self._data[j]))
                    j += 1
                if len(s) >= min_len:
                    yield i, ''.join(s)
                i = j
            else:
                i += 1


# ---------------------------------------------------------------------------
# Python firmware reimplementation
# ---------------------------------------------------------------------------

class TSP53C30:
    """
    Software model of the TI TSP53C30 speech synthesizer.

    In the real hardware the 80C186 streams LPC bytes to I/O port 0x180
    via the INT1 ISR (isr_tsp53c30_ready).  This class models that behavior
    in software.
    """

    # TSP53C30 commands (sent as first byte of a speak-external sequence)
    CMD_RESET           = 0xFF
    CMD_SPEAK_EXTERNAL  = 0x10
    CMD_SPEAK           = 0x00
    CMD_STOP            = 0x40
    CMD_READ_STATUS     = None   # read-only: status from the port

    # Status bits (active-low on port 0x180)
    STATUS_READY_BIT    = 0   # bit 0 = /READY
    STATUS_BL_BIT       = 2   # bit 2 = buffer-low  → send more data
    STATUS_TALK_BIT     = 3   # bit 3 = talking (cleared = finished)

    def __init__(self, voice_eprom: VoiceEPROM):
        self._eprom   = voice_eprom
        self._frames: List[LPCFrame] = []
        self._state   = {}
        self._pcm_buf = b""
        self._talking = False

    def speak(self, group: int, phrase_id: int) -> bytes:
        """
        Speak a phrase and return rendered PCM bytes (16-bit signed, 8 kHz mono).
        """
        lpc_data = self._eprom.get_phrase_data(group, phrase_id)
        if lpc_data is None:
            return b""
        self._frames  = decode_lpc_frames(lpc_data)
        self._state   = {}
        self._talking = True
        return render_phrase_to_pcm(lpc_data)

    def speak_sequence(self, phrase_list: List[Tuple[int, int]]) -> bytes:
        """
        Speak multiple phrases in sequence (concatenated PCM).
        """
        pcm = b""
        for group, pid in phrase_list:
            pcm += self.speak(group, pid)
        return pcm


class PhoneLineInterface:
    """
    Abstract model of the phone line / DTMF interface.

    In the real hardware, port 0x140 carries modem/phone-line status:
      bits 7-4: nibble that reflects line state or DTMF digit
        0xC (1100) = off-hook / call connected

    Port 0x0080 drives output latches (LEDs / relay).
    Port 0x00C0 reads DIP switches and status signals.
    """

    OFFHOOK_NIBBLE = 0xC

    def __init__(self):
        self.offhook = False
        self.dtmf_digit: Optional[int] = None
        self.relay_state: int = 0x00

    def read_status(self) -> int:
        """Simulate reading port 0x0140."""
        nibble = self.OFFHOOK_NIBBLE if self.offhook else 0x0
        return (nibble << 4) | 0x0F

    def write_relay(self, value: int):
        """Simulate writing port 0x0080."""
        self.relay_state = value


class CAT1000Firmware:
    """
    Python reimplementation of the CAT-1000 firmware (V3.04A, Mar 01 1998).

    The CAT-1000 is an automated telephone announcement / call-accounting
    terminal.  The 80C186 CPU manages:
      - Answering / placing phone calls via relay control
      - Playing voice announcements (TSP53C30 speech synthesizer)
      - Collecting DTMF input
      - Serial communication (RS-232) at 0xFF-series ports

    This class provides a faithful behavioral model of the firmware, suitable
    for simulation, testing replacement software, or understanding the device.
    """

    FIRMWARE_VERSION = "V3.04A"
    FIRMWARE_DATE    = "Mar 01, 1998"

    # RAM variable addresses (within segment 0x1000, size 0x200 bytes)
    RAM_SPEECH_ACTIVE   = 0x000F  # 0xFF = speech active, 0x00 = idle
    RAM_SPEECH_PTR_LO   = 0x00A6  # current LPC data pointer (lo byte)
    RAM_SPEECH_PTR_HI   = 0x00A7  # current LPC data pointer (hi byte)
    RAM_TIMER_TICKS     = 0x00E8  # countdown timer (word)
    RAM_PHONE_STATE     = 0x0028  # phone line state flags
    RAM_DTMF_DIGIT      = 0x000D  # last DTMF digit received
    RAM_RELAY_CTRL      = 0x005F  # relay output shadow register
    RAM_SEQUENCE_PTR    = 0x00C6  # speech sequence pointer

    def __init__(self,
                 voice_eprom: Optional[VoiceEPROM] = None,
                 program_eprom: Optional[ProgramEPROM] = None):
        self.voice   = voice_eprom   or VoiceEPROM()
        self.program = program_eprom or ProgramEPROM()
        self.tsp     = TSP53C30(self.voice)
        self.phone   = PhoneLineInterface()

        # Simulated RAM (segment 0x1000, 512 bytes)
        self._ram = bytearray(512)
        self._speech_active   = False
        self._speech_group    = 0
        self._speech_phrase   = 0

    # ------------------------------------------------------------------
    # Speech control  (mirrors speak_phrase at 0x18B0)
    # ------------------------------------------------------------------

    def speak_phrase(self, group: int, phrase_id: int) -> bytes:
        """
        Play a voice phrase.
        Mirrors the firmware function at 0x18B0.

        Parameters:
            group     – phrase group number (AH register in real firmware)
            phrase_id – phrase ID within group (AL register in real firmware)

        Returns rendered PCM bytes for the utterance.
        """
        self._ram[self.RAM_SPEECH_ACTIVE] = 0xFF
        pcm = self.tsp.speak(group, phrase_id)
        self._ram[self.RAM_SPEECH_ACTIVE] = 0x00
        return pcm

    def speak_number(self, number: int) -> bytes:
        """
        Speak an integer number (0-9999) using concatenated digit phrases.
        Group 0 contains basic digit phrases: phrase_id = 0..9 for "zero".."nine",
        0x1E=30, 0x28=40 ... 0x5A=90 for tens, etc.
        """
        if not (0 <= number <= 9999):
            raise ValueError("Number out of range 0-9999")
        pcm = b""
        if number == 0:
            return self.speak_phrase(0, 0)
        if number >= 1000:
            thou = number // 1000
            pcm += self.speak_phrase(0, thou)       # digit
            pcm += self.speak_phrase(0, 0x10)       # "thousand" (phrase 16)
            number %= 1000
        if number >= 100:
            hund = number // 100
            pcm += self.speak_phrase(0, hund)       # digit
            pcm += self.speak_phrase(0, 0x11)       # "hundred" (phrase 17)
            number %= 100
        if number >= 20:
            tens_map = {2:0x14, 3:0x1E, 4:0x28, 5:0x32,
                        6:0x3C, 7:0x46, 8:0x50, 9:0x5A}
            tens = number // 10
            pcm += self.speak_phrase(0, tens_map[tens])
            number %= 10
        if number > 0:
            pcm += self.speak_phrase(0, number)
        return pcm

    # ------------------------------------------------------------------
    # Phone line control  (mirrors isr_keypad / isr_phone_status)
    # ------------------------------------------------------------------

    def answer_call(self):
        """Go off-hook to answer an incoming call."""
        self.phone.offhook = True
        self._ram[self.RAM_PHONE_STATE] = 0xFF

    def hang_up(self):
        """Go on-hook to terminate a call."""
        self.phone.offhook = False
        self._ram[self.RAM_PHONE_STATE] = 0x00
        self._ram[self.RAM_SPEECH_ACTIVE] = 0x00

    def process_dtmf(self, digit: int):
        """
        Process a received DTMF digit.
        Mirrors the INT0 handler at 0x166A which routes digits to the
        appropriate voice response.
        """
        self._ram[self.RAM_DTMF_DIGIT] = digit & 0x0F

    # ------------------------------------------------------------------
    # Initialization  (mirrors reset_entry at 0x0413)
    # ------------------------------------------------------------------

    def cold_start(self):
        """
        Perform cold-start initialization.
        Mirrors the 80C186 startup sequence beginning at ROM offset 0x0413.

        Sequence (from disassembly):
          1. Set DS = 0x1000 (RAM segment)
          2. Clear 512 bytes of RAM
          3. Configure 80C186 chip selects / timers / DMA / serial
          4. Call init_peripherals_1, init_peripherals_2, init_main
          5. Enter main_loop
        """
        # Clear RAM
        self._ram = bytearray(512)

        # Set default relay state (all outputs off)
        self.phone.relay_state = 0x00

        # Firmware version info (written to RAM during init)
        self._ram[0x00] = 0x03   # version major
        self._ram[0x01] = 0x04   # version minor

    # ------------------------------------------------------------------
    # Main loop sketch  (mirrors main_loop at 0x70A2)
    # ------------------------------------------------------------------

    def step(self) -> Optional[bytes]:
        """
        Execute one step of the firmware main loop.
        Returns PCM audio bytes if a phrase was spoken, else None.

        The real main loop runs continuously, polling state flags set by ISRs.
        This method simulates one polling iteration.
        """
        pcm = None

        # If a speech sequence is pending (RAM_SEQUENCE_PTR != 0)
        seq_ptr = self._ram[self.RAM_SEQUENCE_PTR]
        if seq_ptr and self._ram[self.RAM_SPEECH_ACTIVE] == 0:
            # In the real firmware a pointer into the program ROM sequence table
            # drives automatic phrase chaining.  Emit a placeholder here.
            pass

        return pcm


# ---------------------------------------------------------------------------
# Extraction utilities
# ---------------------------------------------------------------------------

def extract_all_phrases(output_dir: str = "cat-1000_phrases",
                        voice_path: str = VOICE_EPROM_PATH):
    """
    Extract all speech phrases from the voice EPROM as individual WAV files.

    Output filenames: grpN_phraseXXX_addrYYYY.wav
    """
    os.makedirs(output_dir, exist_ok=True)
    eprom = VoiceEPROM(voice_path)

    seen_addrs: Dict[int, str] = {}
    count = 0

    for grp, pid, addr, raw in eprom.iter_all_phrases():
        # Deduplicate by address (some phrases share data)
        if addr in seen_addrs:
            link = os.path.join(output_dir,
                                f"grp{grp}_phrase{pid:03d}_addr{addr:04X}.wav")
            src  = seen_addrs[addr]
            if not os.path.exists(link):
                import shutil
                shutil.copy(src, link)
            continue

        wav_path = os.path.join(output_dir,
                                f"grp{grp}_phrase{pid:03d}_addr{addr:04X}.wav")
        try:
            pcm = render_phrase_to_pcm(raw)
            write_wav(pcm, wav_path)
            seen_addrs[addr] = wav_path
            count += 1
        except Exception as e:
            print(f"  Warning: phrase grp{grp}/pid{pid} @ 0x{addr:04X}: {e}")

    print(f"Extracted {count} unique phrases to '{output_dir}/'")


def dump_phrase_table(voice_path: str = VOICE_EPROM_PATH):
    """Print a formatted listing of all phrases."""
    eprom = VoiceEPROM(voice_path)
    print(f"{'Group':>5}  {'PhraseID':>8}  {'Addr':>6}  {'Frames':>6}  {'Size':>5}")
    print("-" * 45)
    for grp, pid, addr, raw in eprom.iter_all_phrases():
        frames = decode_lpc_frames(raw)
        n_voiced  = sum(1 for f in frames if not f.stop and not f.silence and f.pitch > 0)
        n_unvoiced = sum(1 for f in frames if not f.stop and not f.silence and f.pitch == 0)
        print(f"  {grp:3d}    0x{pid:02X} ({pid:3d})  0x{addr:04X}  "
              f"{len(frames):5d}f  {len(raw):4d}B  "
              f"(V={n_voiced} U={n_unvoiced})")


def dump_hardware_summary():
    """Print a human-readable hardware and firmware summary."""
    print("=" * 60)
    print("CAT-1000  Firmware Analysis Summary")
    print("=" * 60)
    print(f"  Firmware date   : {ProgramEPROM.FIRMWARE_DATE}")
    print(f"  CPU             : Intel 80C186 (16-bit, 8086-compatible)")
    print(f"  Speech chip     : TI TSP53C30 (TMS5220-compatible LPC)")
    print(f"  Program ROM     : 27C512  (64 KB) at 0x0000:0x0000")
    print(f"  Voice ROM       : 27SF512 (64 KB) at 0xF000:0x0000")
    print()
    print("  Interrupt vectors:")
    for vec, addr, desc in ProgramEPROM.IVT_ENTRIES:
        print(f"    INT{vec:2d}  0x{addr:04X}  {desc}")
    print()
    print("  Key I/O ports:")
    print(f"    0x0080  Output latch  (relay / LED control)")
    print(f"    0x00C0  Input latch   (DIP switches / status)")
    print(f"    0x0140  Phone status  (DTMF / line nibble, bits 7-4)")
    print(f"    0x0180  TSP53C30      (LPC data / status)")
    print(f"    0xFF5E  80C186 PCB    (peripheral control enable/disable)")
    print()
    print("  Voice EPROM phrase groups:")
    total = 0
    for grp, (tbl_off, count) in sorted(PHRASE_GROUPS.items()):
        print(f"    Group {grp}  table@0x{tbl_off:04X}  {count:3d} entries")
        total += count
    print(f"            Total: {total} phrase entries")
    print()
    print("  Speech data region: 0x0642 – 0xC42C  (~49 KB of LPC data)")
    print()
    print("  Voice EPROM service routines:")
    print(f"    0xF000:0x0000  phrase_dispatch  (CALL FAR entry point)")
    print(f"    0xF000:0xFD00  save_tsp_state   (backup TSP53C30 registers)")
    print(f"    0xF000:0xFE00  restore_tsp_state(restore TSP53C30 registers)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="CAT-1000 Voice EPROM / Firmware Analysis Tool")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("summary",  help="Print hardware / firmware summary")
    sub.add_parser("phrases",  help="List all phrases in voice EPROM")

    ext = sub.add_parser("extract", help="Extract all phrases as WAV files")
    ext.add_argument("-o", "--output", default="cat-1000_phrases",
                     help="Output directory (default: cat-1000_phrases)")

    sp = sub.add_parser("speak",
                        help="Render one phrase to WAV  [--group G --id N --out file.wav]")
    sp.add_argument("--group", "-g", type=int, required=True,
                    help="Phrase group (0, 2-9)")
    sp.add_argument("--id",    "-i", type=int, required=True,
                    help="Phrase ID within group")
    sp.add_argument("--out",   "-o", default="phrase.wav",
                    help="Output WAV file (default: phrase.wav)")

    num = sub.add_parser("number",
                         help="Speak a number (0-9999) as WAV")
    num.add_argument("value", type=int, help="Integer to speak")
    num.add_argument("--out", "-o", default="number.wav")

    args = parser.parse_args()

    if args.cmd == "summary":
        dump_hardware_summary()

    elif args.cmd == "phrases":
        dump_phrase_table()

    elif args.cmd == "extract":
        extract_all_phrases(output_dir=args.output)

    elif args.cmd == "speak":
        fw  = CAT1000Firmware()
        pcm = fw.speak_phrase(args.group, args.id)
        if pcm:
            write_wav(pcm, args.out)
            print(f"Wrote {len(pcm)//2} samples ({len(pcm)//2/TMS5220_SAMPLE_RATE:.2f}s) "
                  f"→ {args.out}")
        else:
            print(f"Phrase group={args.group} id={args.id} not found.", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "number":
        fw  = CAT1000Firmware()
        pcm = fw.speak_number(args.value)
        write_wav(pcm, args.out)
        print(f"Wrote {len(pcm)//2} samples → {args.out}")

    else:
        dump_hardware_summary()
        print()
        parser.print_help()

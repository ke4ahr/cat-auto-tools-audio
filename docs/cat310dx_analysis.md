# CAT-310DX V1.00 -- EPROM Analysis and CAT-1000 Comparison

**Image:** `eprom_images/CAT-310DX_V1-00_1998_7A69.BIN`
**Size:** 65,536 bytes (64 KB), single unified EPROM
**Checksum:** 7A69 (from version string)
**Copyright:** (C) 1998, V1.00
**Device:** CAT-310DX digital weather station console

---

## Executive Summary

The CAT-310DX and CAT-1000 are fundamentally different devices built on incompatible hardware platforms:

| Property | CAT-1000 | CAT-310DX V1.00 |
|----------|----------|-----------------|
| CPU | Intel 8086 (x86) | Intel 8052 (MCS-51) |
| EPROM count | Two separate chips (program + voice) | One unified chip |
| EPROM size | 64 KB program + 64 KB voice = 128 KB total | 64 KB combined |
| Voice synthesis | TMS5220 (external chip, I/O port 0x0180) | TSP53C30 (external chip, MOVX 0xC000) |
| LPC speech data | 48,767 bytes, 481 unique clips, 482 index entries | 41,820 bytes, 414 unique clips, 415 dispatch entries |
| Word vocabulary | 481 unique phrases (weather terms + numeric) | 100 word IDs (0x00–0x63 = numbers 0–99), 4–9 recordings each |
| Bit order | MSB-first (bit 7 = first parameter bit) | MSB-first (same format) |
| Serial protocol | None | XMODEM download/upload |
| Data transfer | No | Yes (weather data I/O) |
| DST/timezone | Not applicable | Yes, with a bug in V1.00 |

Both devices use **TMS5220-compatible MSB-first LPC bit-packed speech** and share the same codec format. They differ in vocabulary: the CAT-1000 covers a full weather-station phrase library; the CAT-310DX covers only numbers 0–99 (with multiple context-specific recordings per number for natural concatenative speech).

---

## False-Positive Analysis from LPC Scanner (Whole-Image Scan)

Running `eprom_scout.py` on the full CAT-310DX image produces misleading results because the 8052 firmware code region (0x0000–0x4F9F) is statistically similar to LPC data at the minimum 5-frame threshold:

```
MSB-first blobs: 364,  LSB-first blobs: 341  → using MSB-first
Found 364 LPC blobs, 59,091 bytes total speech data
Best phrase index: offset 0x1098, 44 entries, 100% confidence
Phrases exported: 23
```

Every result is a false positive from the code region:

- **"Phrase index" at 0x1098**: This is a `CJNE A, #imm, rel` dispatch table -- the keypad input handler. The 3-byte entry structure (`B4 imm rel`) matches the `[pid][lo][hi]` phrase index pattern coincidentally.
- **23 "phrases"**: Code segments that satisfy the minimum-frames LPC validity test.

The real speech data is at 0x4FA0–0xF6E4. When `eprom_scout.py` is run constrained to that region (`--data-start 0x4FA0 --data-end 0xF6E4`) it finds 58 legitimate blobs. The CAT-310DX word boundaries are defined by the firmware dispatch table at 0x4AC3 rather than a phrase-index structure, so `eprom_scout.py` phrase-index detection still fails on the constrained region -- the correct extraction method is to read the dispatch table directly.

---

## EPROM Memory Map

```
0x0000–0x002F   8052 interrupt vector table (8 vectors)
0x0030–0x007C   Zero-filled (unused vector space)
0x007D–0x34BF   Main firmware code, ISRs, display routines, clock, DST, alarm
0x34C0–0x351E   Speech word sequence tables (pairs + 0xFF terminators)
0x351F–0x4AC2   Firmware subroutines (XMODEM, sensor math, keypad, speech dispatch)
0x4AC3–0x4F9F   Word address dispatch table (415 × 3-byte entries)
0x4FA0–0xF6E3   TMS5220-compatible LPC speech data (41,820 bytes = 40.8 KB)
0xF6E4–0xF7FF   Firmware code (speech control routines calling 0x4900, 0x48B2)
0xF800–0xFA14   Serial I/O routines, XMODEM, transfer menu strings
0xFA15–0xFAF0   Reset handler and system initialization
0xFAF1–0xFFFF   Zero-filled (unused)
```

Total non-zero bytes: approximately 63,473 of 65,536 (96.9%). This is a fully-packed firmware image consistent with a mature product at final release.

---

## 8052 Interrupt Vector Table

```
Address   Vector         Opcode            Target
0x0000    RESET          02 FA 15          LJMP 0xFA15
0x0003    INT0 (EXT0)    02 24 7B          LJMP 0x247B
0x000B    TIMER0 OVF     02 23 8C          LJMP 0x238C
0x0013    INT1 (EXT1)    00 00 00          NOP × 3 (unused)
0x001B    TIMER1 OVF     00 00 00          NOP × 3 (Timer1 used as baud-rate clock; no ISR)
0x0023    UART (RI/TI)   00 00 00          NOP × 3 (handled via polling in INT0 ISR)
0x002B    TIMER2 OVF     B2 B4 C2 C6 32   inline: CPL P3.4; CLR 0xC6; RETI
```

The presence of a Timer2 vector at 0x002B confirms the CPU is an **8052** (not the base 8051), which adds a 16-bit auto-reload/capture Timer2.

**Timer2 ISR (inline at 0x002B, 5 bytes):**
```asm
CPL   P3.4        ; toggle port 3 bit 4 (heartbeat or clock output)
CLR   0xC6        ; clear extended SFR bit
RETI
```

---

## Interrupt Handler Functions

### RESET → 0xFA15 (System Initialization)

```asm
; SFR initialization at 0xFA20–0xFA50
D2 BE        SETB  0xBE          ; enable extended peripheral
D2 AE        SETB  EA (bit 0xAE) ; global interrupt enable
53 B0 CF     ANL   P3, #0xCF     ; configure I/O
53 90 E8     ANL   P1, #0xE8
75 81 C0     MOV   SP, #0xC0     ; stack pointer = 0xC0
75 D8 80     MOV   T2CON, #0x80  ; Timer2: auto-reload mode
75 98 5A     MOV   SCON, #0x5A   ; serial mode 1, 8-bit UART, REN=1
75 89 21     MOV   TH1, #0x21    ; Timer1 reload (baud rate divisor)
75 8C 3C     MOV   TH0, #0x3C    ; Timer0 reload high byte (16 ms tick)
75 8A AF     MOV   TL0, #0xAF    ; Timer0 reload low byte
75 88 01     MOV   TCON, #0x01   ; start Timer0, edge-trigger INT0
```

### Timer0 ISR → 0x238C (System Clock Tick, ~32 ms)

Fires every ~16 ms; every other tick (~32 ms) it decrements countdown timers, synchronizes RTC shadow registers with display RAM, and dispatches to clock-update and alarm-check routines.

### INT0 ISR → 0x247B (Keypad + Serial Polling)

Polls UART TI/RI flags and handles keypad input (INT0 is connected to a keypad interrupt line; the handler additionally services pending serial data).

---

## TSP53C30 Speech Synthesizer Interface

The CAT-310DX board carries a **TSP53C30** (Texas Instruments speech synthesizer chip). The chip is mapped to the 8052 external bus at address **0xC000** and is accessed via `MOVX @DPTR`:

| Operation | Instruction | Meaning |
|-----------|-------------|---------|
| Read status | `MOVX A, @DPTR` (DPTR=0xC000) | 0xFE = ready/idle; 0xFB = STOP frame detected |
| Write data | `MOVX @DPTR, A` (DPTR=0xC000) | Stream one LPC byte to the chip |

### LPC Streaming Routine at 0x4A78

The routine at 0x4A78 streams an LPC clip from ROM to the TSP53C30:

```asm
4A78: C0 82 C0 83       PUSH DPL; PUSH DPH     ; save clip DPTR
      C2 96 00 D2 96    CLR/SETB (enable flags)
      D2 AE D2 BE       SETB EA; SETB BE
      74 0A F5 43       MOV A,#10; MOV 0x43,A   ; init counter
      E5 43 60 03       JZ → done check
      20 B3 F9          JB 0xB3, $-7            ; busy-wait on ready flag

4A90: 90 C0 00          MOV DPTR, #0xC000       ; → TSP53C30
4A93: E0                MOVX A, @DPTR           ; read status
4A94: B4 FE 27          CJNE A, #0xFE, $+0x27   ; wait until ready (0xFE)
4A97: 74 06 F0          MOV A,#6; MOVX @DPTR,A  ; send init command 0x06
      D2 AE D2 BE       re-enable interrupts
      74 0A F5 43       reload counter

; Main stream loop (from saved clip DPTR):
4AA4: E5 43 60 03 20 B3 F9   busy-wait / retry loop
4AAC: D0 83 D0 82       POP DPH; POP DPL        ; restore clip DPTR
4AB0: E4 93             MOVC A, @A+DPTR         ; read LPC byte from ROM
4AB2: A3                INC DPTR                ; advance to next byte
4AB3: C0 82 C0 83       PUSH DPL; PUSH DPH
4AB7: 90 C0 00          MOV DPTR, #0xC000
4ABA: F0                MOVX @DPTR, A           ; write LPC byte to TSP53C30
4ABB: 80 DC             SJMP → loop
4ABD: D0 83 D0 82       POP DPH; POP DPL
4AC1: 22                RET
```

The chip status bytes observed in firmware:
- **0xFE** -- chip idle/ready (bit pattern `1111 1110` -- all energy bits set except bit 0 = ready flag)
- **0xFB** -- STOP frame detected by chip (`1111 1011` -- ready=1, stop=1)

---

## Word Address Dispatch Table (0x4AC3–0x4F9F)

The dispatch table contains **415 three-byte entries** in the format `[word_id_byte, addr_hi, addr_lo]`:

```
0x4AC3: 00 4F A0  → word 0x00 ( 0) → 0x4FA0
0x4AC6: 01 4F ED  → word 0x01 ( 1) → 0x4FED
0x4AC9: 02 50 30  → word 0x02 ( 2) → 0x5030
...
0x4AD6: 05 50 FF  → word 0x05 ( 5) → 0x50FF
...
0x4AFA: 0A 52 59  → word 0x0A (10) → 0x5259
...
0x4B6C: 14 57 4C  → word 0x14 (20) → 0x574C
0x4B6F: 1E 57 98  → word 0x1E (30) → 0x5798
0x4B72: 28 57 F0  → word 0x28 (40) → 0x57F0
...
```

**Word number mapping (hex → spoken number):**

| Hex | Dec | | Hex | Dec | | Hex | Dec |
|-----|-----|-|-----|-----|-|-----|-----|
| 0x00–0x09 | 0–9 | | 0x0A–0x13 | 10–19 | | 0x14 | 20 |
| 0x15–0x1D | 21–29 | | 0x1E | 30 | | 0x1F–0x27 | 31–39 |
| 0x28 | 40 | | 0x29–0x31 | 41–49 | | 0x32 | 50 |
| 0x33–0x3B | 51–59 | | 0x3C | 60 | | 0x3D–0x45 | 61–69 |
| 0x46 | 70 | | 0x47–0x4F | 71–79 | | 0x50 | 80 |
| 0x51–0x59 | 81–89 | | 0x5A | 90 | | 0x5B–0x63 | 91–99 |

The word number is the decimal value itself encoded directly in hex: `0x00` = zero, `0x09` = nine, `0x0A` = ten, `0x14` = twenty (0x14 = decimal 20), `0x1E` = thirty (0x1E = decimal 30), etc.

**Multiple recordings per word:** 96 of the 100 word IDs appear multiple times (4–9 occurrences each) with different ROM addresses. These are different recordings of the same number for different intonation contexts (e.g., "forty" when it ends a phrase vs. "forty" before a following word in concatenative speech). Total unique clips: **414** (one address, 0x4FA0, is shared by word 0 and word 52 = both point to the same recording).

**Dispatch routine at 0x49E6:** The dispatch uses a 9-way CJNE chain (A=word_id) to jump to a pre-computed sub-range of the dispatch table:

| Word ID | Table start | Count |
|---------|-------------|-------|
| 0x00 | 0x4AC3 | 29 |
| 0x02 | 0x4B17 | 71 |
| 0x03 | 0x4BEC | 48 |
| 0x04 | 0x4C7C | 36 |
| 0x05 | 0x4CC8 | 40 |
| 0x06 | 0x4D5D | 55 |
| 0x07 | 0x4E02 | 49 |
| 0x08 | 0x4E95 | 54 |
| 0x09 | 0x4F37 | 36 |
| (others) | 0x4AC3 | 29 |

Within each sub-range, the routine does a linear search for a matching word_id, reads `addr_hi:addr_lo`, and calls the streaming routine at 0x4A78.

---

## Speech Sequence Tables (0x34C0–0x351E)

At 0x34C0 the firmware stores pre-built speech sequences for reading numeric displays. Each sequence is a variable-length list of word-ID pairs terminated by `0xFF`:

```
Format: [0x00, word_id] [0x00, word_id] ... 0xFF
```

Sequences for digits 1–9 (each 7 bytes):

```
34C0: 00 06 00 00 00 01 FF  → word6, word0, word1  (context for "one" in a sequence)
34C7: 00 06 00 00 00 02 FF  → word6, word0, word2
...
34F8: 00 06 00 00 00 09 FF  → word6, word0, word9
```

Sequences for 10 and above (shorter):

```
3500: 00 06 00 0A FF        → word6, word10
3505: 00 06 00 0B FF        → word6, word11
...
```

The leading `00 06` (word 6 = "six") in every sequence appears to serve as a fixed prefix -- possibly a carrier tone, silence frame, or contextual intro used before every spoken readout. Alternatively, word 6 may be a special control word (e.g., silence or a tone burst) rather than the digit "six" proper. Disambiguation requires listening to the synthesized WAV output.

The routine at 0x4888 reads these pairs sequentially:
1. Read byte → if `0xFF`, return; else save as context byte in R6
2. Read next byte → save as word_id in R7
3. LCALL 0x49E6 (dispatch)
4. Advance DPTR, loop

---

## LPC Speech Data Region (0x4FA0–0xF6E3)

```
Region start:    0x4FA0
Region end:      0xF6E3 (inclusive)
Total bytes:     41,820  (40.8 KB)
Unique clips:    414
Word IDs:        100 (0x00–0x63 = numbers 0–99)
```

The LPC data is in **MSB-first** format: bit 7 of each byte is the first parameter bit, identical to the CAT-1000's TMS5220 format. The `cat-1000_lpc_export.py` synthesizer (`render_phrase_to_pcm`) processes this data correctly without any bit reversal.

**Clip size distribution:**

| Range | Count |
|-------|-------|
| < 100 bytes | 207 |
| 100–199 bytes | 163 |
| 200–499 bytes | 35 |
| ≥ 500 bytes | 9 |

Typical clip: 50–200 bytes, 375–875 ms synthesized duration. Outliers (e.g., word 72 / 0x48 at 0xF24B: 558 bytes, ~2.8 s) may be composite phrases stored as single clips.

**Extraction:** All 414 clips extracted and synthesized to WAV by `cat-310dx_extract.py` into `cat-310dx_lpc_clips/` (LPC) and `cat-310dx_wav_clips/` (WAV). CSV output: `cat-310dx_clips.csv`.

---

## External RAM Layout (0xA000–0xA4FF)

The firmware uses external 8-bit SRAM at 0xA000–0xA4FF (1.25 KB). Key regions derived from initialization writes at 0x1E00–0x2100:

```
0xA000–0xA027   System flags and state variables
0xA028–0xA03F   Clock/time registers (shadow copy)
0xA040–0xA0B0   Alarm and interval configuration
0xA0B0–0xA0FF   Weather sensor calibration offsets
0xA100–0xA3FF   Data logging ring buffer (rainfall, temperature, wind, etc.)
0xA400–0xA4FF   XMODEM transfer workspace
```

The initialization code at 0x1E00–0x2100 uses `F0` (MOVX @DPTR,A) + `A3` (INC DPTR) sequences to write factory defaults.

---

## Serial I/O and Data Transfer

Strings at 0xF85C–0xF929:

```
0xF85C  "CAT-310 Data Transfer, D=Download...U=Upload...Q=Quit. Select "
0xF89B  "CAT-310 Data Transfer Program Terminated..."
0xF8EF  "Please press (ENTER) to begin."
0xF91A  "CRT error!"
0xF97C  "0123456789ABCDEF"   (hex digit lookup)
```

And at 0x1A7A–0x1B0F:

```
0x1A7A  "Select XMODEM file DOWNLOAD protocol now..."
0x1AA7  "Data Error or Timeout has Expired..."
0x1ACE  "Data transfer successful"
0x1AE7  "Select XMODEM file UPLOAD protocol now..."
```

The XMODEM handler at 0x1A00–0x1B10 implements 128-byte block XMODEM (CRC or checksum) for upload/download of stored weather records. UART baud rate: TH1=0x21 ≈ 2400 baud at 11.0592 MHz.

---

## DST / Timezone Processing and the V1.00 Bug

### Overview

DST processing is enabled by **bit 3 of internal RAM byte 0xDB** (`DST_EN` flag).

| Address | Role |
|---------|------|
| `0xDB` bit 3 | DST enable flag |
| `0x2780–0x2BB0` | Month dispatch table (10 cases) |
| `0x412A` | Spring-forward clock-adjust subroutine (April) |
| `0x418A` | Fall-back clock-adjust subroutine (October) |
| `0x3BF5–0x3C1F` | DST transition day-of-month tables |

### Month Dispatch at 0x2780

```asm
2780: B4 0A 03  CJNE  A, #10, $+3      ; Oct  → LJMP 0x2865
      B4 01 02  CJNE  A, #1,  $+2      ; Jan
      B4 04 03  CJNE  A, #4,  $+3      ; Apr  → LJMP 0x2948
      ...
      B4 09 03  CJNE  A, #9,  $+3      ; Sep  → LJMP 0x2B63
```

### The V1.00 Bug: Duplicate DST Application

The firmware has **three separate code paths** that check for April and apply the spring-forward adjustment (at 0x136A, 0x154A, and 0x2B82). The October fall-back has only two corresponding paths.

```asm
; April branch at 0x2B80 (representative):
B4 04 33     CJNE  A, #4, $+0x33    ; if not April, skip
E5 DB        MOV   A, 0xDB          ; load flags byte
30 E3 47     JNB   0xDB.3, $+0x47  ; if DST disabled, skip
74 04        MOV   A, #4            ; +1 hr adjustment
12 41 2A     LCALL 0x412A           ; apply spring-forward
```

Effect: under certain conditions (device running through DST midnight), April spring-forward fires twice, advancing the clock by **two hours instead of one**. October fall-back is unaffected (two paths, correctly symmetric). A unit with DST disabled (`0xDB` bit 3 = 0) or powered off during the transition is unaffected.

### DST Transition Tables at 0x3BF5–0x3C1F

The subroutine at 0x3BA8 uses three packed tables (pointers at 0x3BF5 / 0x3BFC / 0x3C03) selected by year-index to determine the transition day. This implements the pre-2007 US DST rule (first Sunday in April / last Sunday in October).

---

## Comparison Summary: CAT-1000 vs CAT-310DX V1.00

### Architecture

| Feature | CAT-1000 | CAT-310DX V1.00 |
|---------|----------|-----------------|
| Processor | Intel 8086 (16-bit x86) | Intel 8052 (8-bit MCS-51) |
| Operating frequency | ~8 MHz | ~11 MHz (from baud rate) |
| ROM chips | Two 27C512 (program + voice) | One 27C512 (unified) |
| RAM | External, x86 segment mapped | External SRAM at 0xA000–0xA4FF |
| I/O | PC/AT I/O ports (IN/OUT instructions) | Memory-mapped MOVX |

### ROM Structure

| Property | CAT-1000 voice EPROM | CAT-310DX unified EPROM |
|---------|----------------------|------------------------|
| Speech data | 48,767 bytes (0x0642–0xC4C1) | 41,820 bytes (0x4FA0–0xF6E3) |
| Code/index | 1,603 bytes preamble + program EPROM | ~22,672 bytes firmware |
| Free space | ~14 KB zero-filled tail | ~2 KB zero-filled tail |
| Phrase/word index | 0x009F–0x0641, 482 entries, [pid][lo][hi] | 0x4AC3–0x4F9F, 415 entries, [wid][hi][lo] |
| Bit order | MSB-first (native TMS5220) | MSB-first (same) |

### Voice Synthesis

| Property | CAT-1000 | CAT-310DX |
|----------|----------|-----------|
| Speech chip | TMS5220 | TSP53C30 |
| Interface | I/O port 0x0180 (`OUT DX, AL`) | MOVX write to 0xC000 (`MOVX @DPTR, A`) |
| Ready poll | `IN AL, DX` → `CMP AL, #0xFE` | `MOVX A, @DPTR` → `CJNE A, #0xFE` |
| Stop detection | ISR counts STOP nibble | Chip signals 0xFB on STOP |
| LPC format | MSB-first bit-packed frames | MSB-first bit-packed frames (identical) |
| Streaming | x86 ISR, one byte/interrupt | 8052 loop with MOVC A,@A+DPTR |

### Speech Clip Comparison

| Aspect | CAT-1000 | CAT-310DX |
|--------|----------|-----------|
| Total unique clips | 481 | 414 |
| Vocabulary type | Weather terms + numeric phrases | Numbers 0–99 only |
| Multi-recording | No (one recording per phrase) | Yes (4–9 per word ID for intonation) |
| Phrase index | Sorted by address, group×100+id scheme | Not sorted; dispatch by linear search in buckets |
| Matching clips | None | Numeric recordings for 0–90 overlap in concept; different recordings/speaker |

The LPC bitstream **format** is identical between devices (MSB-first, same TMS5220 frame structure). The content has no byte-level matches -- these are independent recordings on different hardware at different times.

### Calendar / Timekeeping

The CAT-310DX has full real-time clock support driven by the Timer0 ISR. The CAT-1000 is a standalone repeater controller driven by its own 80C186 CPU; timekeeping is managed by the control firmware.

---

## Key Discoveries

1. **TSP53C30 on-board, LPC speech present**: The CAT-310DX carries a TSP53C30 speech synthesis chip (TMS5220-compatible) mapped at MOVX address 0xC000. The firmware streams 41.8 KB of MSB-first LPC data from ROM via the 8052's `MOVC A,@A+DPTR` instruction. `eprom_scout.py` run on the full image produces only false positives (8052 machine code misidentified as LPC); the real speech region (0x4FA0–0xF6E3) must be extracted using the dispatch table at 0x4AC3.

2. **Complete 0–99 numeric vocabulary with context recordings**: The 100 word IDs (0x00–0x63) map directly to numbers 0–99. With 96 of 100 words having 4–9 recordings each, the device can speak any number concatenatively with natural intonation variation -- suitable for reading back temperature, humidity, wind speed, and other numeric sensor values.

3. **Identical LPC codec, different vocabulary**: Both the CAT-1000 and CAT-310DX use MSB-first TMS5220-compatible LPC. The `cat-1000_lpc_export.py` synthesizer (`render_phrase_to_pcm`) processes both devices' data correctly without modification. This confirms TI's TSP53C30 is a direct TMS5220-family descendant using the same bit-packed codec.

4. **Single-chip architecture**: Unlike the CAT-1000's two-EPROM scheme, the CAT-310DX packs 22 KB of firmware + 41 KB of speech + an 8052 dispatch table into one 64 KB image. With ~2 KB free, V1.00 had little room for growth.

5. **8052 (not 8051)**: Timer2 vector at 0x002B (CPL P3.4; CLR 0xC6; RETI) confirms MCS-51 variant is the 8052 with 16-bit auto-reload Timer2.

6. **DST bug scope**: Three spring-forward code paths vs. two fall-back paths cause a potential double-advance of +2 hours in April for running devices. October is unaffected. V1.00 only.

7. **XMODEM for data access**: All weather history accessible via RS-232 serial port using the built-in D/U/Q transfer menu. UART baud rate ~2400 baud at 11.0592 MHz (TH1=0x21).

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.

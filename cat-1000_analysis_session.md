# CAT-1000 EPROM Reverse Engineering Session

**Date:** 2026-03-22
**Files analyzed:**
- `eprom_images/cat-1000-V304A_program_27C512.BIN` -- program EPROM (27C512, 64 KB)
- `eprom_images/cat-1000-voice_27SF512.BIN` -- voice EPROM (27SF512, 64 KB)

**Output:** `cat-1000_analysis.py`

---

## Device Identification

| Field | Value |
|---|---|
| Device | CAT-1000 Repeater Controller |
| Firmware version | V3.04A |
| Firmware date | `Mar 01, 1998` (string at program ROM offset `0x0406`) |
| CPU | Intel 80C186 (16-bit, 8086/8085-compatible instruction set) |
| Speech synthesizer | TI TSP53C30 (TMS5220-compatible LPC-10 codec) |
| Program ROM | 27C512, 64 KB |
| Voice ROM | 27SF512, 64 KB |

---

## Memory Map

| Physical address | Contents |
|---|---|
| `0x00000–0x0FFFF` | Program EPROM mapped at segment `0x0000` |
| `0xF0000–0xFFFFF` | Voice EPROM mapped at segment `0xF000` |
| `0x10000–0x101FF` | RAM (512 bytes, segment `0x1000`) |

The 80C186 chip-select unit is configured at startup (port `0xFF60` ← `0x8BA6`) to decode both EPROMs simultaneously. The program EPROM also appears at the top of the 1 MB address space (physical `0xFFFFF`) via the reset mirror, providing the startup jump vector.

### Reset sequence

```
ROM[0xFFF0]:  EA 13 04 00 00  →  JMP FAR 0x0000:0x0413
ROM[0x0400]:  EB 11           →  JMP SHORT 0x0413  (skips "Mar 01, 1998\0" version string)
ROM[0x0413]:  startup code begins
```

---

## I/O Port Map

| Port | Direction | Function |
|---|---|---|
| `0x0000` | IN | 80C186 internal sync (wait-state reads) |
| `0x0080` | OUT | Output latch — relay / LED control |
| `0x00C0` | IN | Input latch — DIP switches / status signals |
| `0x0140` | IN | Phone/modem status; bits 7–4 = nibble (`0xC` = off-hook) |
| `0x0180` | IN/OUT | TSP53C30 data and status port |
| `0xFF00–0xFFFF` | IN/OUT | 80C186 internal peripherals (timers, DMA, interrupt controller, serial) |
| `0xFF5E` | IN/OUT | 80C186 PCB — enables/disables TSP53C30 interrupt (bit 7) |
| `0xFF60` | OUT | MPCS — memory-partition chip-select configuration |

---

## Interrupt Vector Table

| Vector | Offset | Handler description |
|---|---|---|
| 0–9, 12, 16–18 | `0x1778` | Default/unhandled ISR (no-op stub) |
| 13 | `0x1582` | DMA1 — phone-line / modem state machine |
| 14 | `0x166A` | INT0 — keypad scan and DTMF digit router |
| 15 | `0x152F` | INT1 — **TSP53C30 READY** (streams next LPC byte to chip) |
| 19 | `0x0FD2` | Serial Tx ISR |
| 20 | `0x0FA5` | Serial-related ISR |
| 21 | `0x176C` | IRQ21 ISR |

---

## Key Subroutines (Program EPROM)

| Offset | Name / description |
|---|---|
| `0x0413` | `reset_entry` — cold-start init; clears RAM, configures 80C186 peripherals |
| `0x152F` | `isr_tsp53c30_ready` — INT1 ISR; feeds next LPC byte to port `0x180` |
| `0x1582` | `isr_phone_status` — DMA1 ISR; phone line state machine |
| `0x166A` | `isr_keypad` — INT0 ISR; reads port `0x140`, routes DTMF digits |
| `0x18B0` | `speak_phrase` — calls voice dispatch, sets up speech pointer, enables chip |
| `0x18FF` | `speak_sequence` — iterates a ROM table of (group, phrase_id) pairs |
| `0x1BF3` | `tsp53c30_enable` — sets bit 7 of PCB reg via port `0xFF5E` |
| `0x1FD8` | `dtmf_dispatch` — routes digit 0x00–0x0F to response handlers |
| `0x3331` | `init_peripherals_1` — timer / DMA init |
| `0x38A9` | `init_peripherals_2` |
| `0x4EF1` | `init_main` — main state and hardware init |
| `0x70A2` | `main_loop` — primary event/polling loop |

---

## Voice EPROM Layout

### Segment 0xF000 map

| Offset | Contents |
|---|---|
| `0x0000–0x009E` | 8086 phrase-dispatch routine (called via `CALL FAR 0xF000:0x0000`) |
| `0x009F–0x0641` | Phrase index tables — 9 groups, 483 entries total |
| `0x0642–0xC4C1` | TMS5220 LPC bit-packed speech data (~49 KB, 481 unique phrases) |
| `0xC4C2–0xFCFF` | Unused / `0x00` fill |
| `0xFD00–0xFD39` | `save_tsp_state` — saves TSP53C30 shadow registers to RAM `0x0000–0x0005` |
| `0xFE00–0xFE43` | `restore_tsp_state` — restores TSP53C30 registers from RAM |
| `0xFFD0–0xFFFF` | Reset-mirror stub matching program EPROM structure |

### Phrase dispatch protocol

The program ROM calls the voice EPROM as a far subroutine:

```asm
; Caller sets:
;   AH = group number  (0, 2, 3, 4, 5, 6, 7, 8, or 9)
;   AL = phrase ID     (varies per group)
CALL FAR 0xF000:0x0000

; Returns:
;   BX = offset within voice EPROM (segment 0xF000) of LPC speech data
;   BX = 0x81F1 if phrase not found (error sentinel)
```

Disassembly of the dispatch routine:

```asm
0000: PUSH CX
0001: PUSH SI
0002: CMP  AH, 0x00    ; group 0?
0005: JZ   0x32
0007: CMP  AH, 0x02    ; group 2?
000A: JZ   0x3B
      ... (groups 3–9) ...
002F: JMP  0x95        ; not found → return error BX=0x81F1

; Group 0 handler:
0032: MOV  SI, 0x009F  ; table base
0036: MOV  CL, 0x1D    ; 29 entries
0038: JMP  0x83        ; → search loop

; Search loop at 0x83:
0083: CMP  AL, [CS:SI]  ; compare phrase_id with table entry[0]
0086: JZ   0x91         ; found
0088: DEC  CL           ; not found yet
008A: JZ   0x98         ; exhausted → error
008C: INC  SI           ; skip 3 bytes
008D: INC  SI
008E: INC  SI
008F: JMP  0x83

; Found at 0x91:
0091: INC  SI           ; skip phrase_id byte
0092: MOV  BX, [CS:SI]  ; load 2-byte address
0095: POP  SI
0096: POP  CX
0097: RETF              ; far return with BX = LPC data address

; Error at 0x98:
0098: MOV  BX, 0x81F1   ; error sentinel
009B: POP  SI
009C: POP  CX
009E: RETF
```

Each table entry is **3 bytes**: `[phrase_id (1B), lo_addr (1B), hi_addr (1B)]`.

### Phrase group summary

| Group | Table offset | Entries | Approx. content |
|---|---|---|---|
| 0 | `0x009F` | 29 | Digits 0–9, tens 10–90, "hundred", "thousand", etc. |
| 2 | `0x00F3` | 79 | Numbers 10–99 and related phrases |
| 3 | `0x01E0` | 54 | |
| 4 | `0x0282` | 43 | |
| 5 | `0x0303` | 50 | |
| 6 | `0x0399` | 65 | |
| 7 | `0x0459` | 61 | |
| 8 | `0x0510` | 60 | |
| 9 | `0x05C4` | 42 | |

### TSP53C30 service routines in voice EPROM

**`save_tsp_state` at 0xFD00:**
```asm
FD00: PUSH AX
FD01: IN   AL, 0x00      ; sync
FD03: POP  AX
FD04: CLI
FD05: PUSH BP
FD06: MOV  BP, 0x7FF8    ; TSP53C30 memory-mapped registers (phys 0xF7FF8)
FD09: MOV  AL, 0x40
FD0B: MOV  [BP+0], AL    ; write 0x40 = save-state command
FD0E: MOV  AL, [BP+2]    ; read register 2
FD11: MOV  [0x0000], AL  ; save to RAM
      ...                ; repeat for registers 3–7
FD32: MOV  AL, 0x00
FD34: MOV  [BP+0], AL    ; clear command register
FD37: POP  BP
FD38: STI
FD39: RETF
```

**`restore_tsp_state` at 0xFE00:** mirrors the above, writing `0x80` and restoring registers from RAM.

---

## Speech Streaming (INT1 ISR at 0x152F)

```asm
152F: PUSHF
1530: PUSHA
1531: MOV  DX, 0x0180         ; TSP53C30 I/O port
1534: IN   AL, DX             ; read status
1535: CMP  AL, 0xFE           ; bit 0 clear = command/index byte needed
1537: JZ   0x1564
1539: CMP  AL, 0xF7           ; bit 3 clear = talking finished
153B: JZ   0x1575
153D: CMP  AL, 0xFB           ; bit 2 clear = buffer low, send data byte
153F: JZ   0x154F

; Send next LPC byte (0x154F):
154F: MOV  BX, 0xF000
1552: MOV  ES, BX             ; ES = 0xF000 (voice EPROM segment)
1554: MOV  BX, [0x00A6]       ; load current speech data pointer
1558: MOV  AL, [ES:BX]        ; fetch byte from voice EPROM
155B: OUT  DX, AL             ; send to TSP53C30
155C: INC  BX
155D: MOV  [0x00A6], BX       ; advance pointer
1561: JMP  0x1567

; Talking finished (0x1575):
1575: MOV  WORD [0x00A6], 0   ; reset speech pointer
157B: MOV  BYTE [0x000F], 0   ; clear speech-active flag
```

---

## TMS5220 LPC Speech Data Format

Data is **bit-packed**, LSB-first within each field, fields packed continuously:

```
Frame structure (one frame = 25 ms = 200 samples @ 8 kHz):

  SILENCE frame:   energy[3:0] = 0000
  STOP frame:      energy[3:0] = 1111  (end of utterance)

  UNVOICED frame:  energy[3:0] != 0/F
                   repeat[0]   = 0
                   pitch[5:0]  = 000000
                   K1[4:0], K2[4:0], K3[3:0], K4[3:0]

  VOICED frame:    energy[3:0] != 0/F
                   repeat[0]   = 0
                   pitch[5:0]  > 0
                   K1[4:0] .. K10[2:0]   (total 10 reflection coefficients)

  REPEAT frame:    energy[3:0] != 0/F
                   repeat[0]   = 1
                   pitch[5:0]             (reuse previous K values)
```

### Example decode — Group 0, Phrase 0 (77 bytes, 15 frames)

```
Frame  0: REPEAT   energy=6   pitch=60
Frame  1: REPEAT   energy=11  pitch=26
Frame  2: VOICED   energy=14  pitch=23  k=(5,14,5,7,13,11,5,5,5,7)
Frame  3: SILENCE
Frame  4: VOICED   energy=5   pitch=15  k=(6,0,5,2,0,4,8,7,2,1)
Frame  5: REPEAT   energy=4   pitch=38
Frame  6: REPEAT   energy=7   pitch=21
Frame  7: VOICED   energy=1   pitch=52  k=(16,26,7,2,15,14,11,2,0,3)
Frame  8: VOICED   energy=10  pitch=60  k=(5,24,4,14,10,11,6,5,3,3)
Frame  9: VOICED   energy=13  pitch=61  k=(26,11,12,4,3,2,1,3,7,4)
Frame 10: REPEAT   energy=1   pitch=44
Frame 11: REPEAT   energy=5   pitch=54
Frame 12: REPEAT   energy=8   pitch=7
Frame 13: VOICED   energy=10  pitch=37  k=(23,19,3,10,5,13,15,3,1,6)
Frame 14: REPEAT   energy=2   pitch=17
Frame 15: STOP
```

---

## Device Function

The decoded speech sequences confirm the CAT-1000 is an **amateur radio repeater controller with telephone interconnect**. Key announcements found in the program ROM:

| Decoded sequence | Meaning |
|---|---|
| "S Q Off" / "S Q On" | Squelch open / close |
| "Repeater Reset" | Repeater reset announcement |
| "Telephone Line In Service" | Phone line ready |
| "Telephone Number Is [n]" | Announce connected phone number |
| "Preset Position" | Announce active preset |
| "Mega Hertz" | Frequency unit announcement |
| "Keypad Test [pause]" | DTMF keypad test sequence |
| "Start Test Now" | Begin diagnostic test |
| "Start Message" | Begin recorded message playback |
| "Power" / "Receiver" / "Transmitter" | Status announcements |
| "No Message" | No message stored |
| "Lockout Number Is [n]" | Announce lockout code |
| "Warning nine one one Number Lockout" | 911 emergency lockout |
| "O K" | Confirmation |
| "Manual [mode]" / "Timer [mode]" | Operating mode announcements |
| "Macro Is [n]" / "Program Is [n]" | Macro/program status |
| "Repeater Reset" | Self-identification on reset |
| "Speed [n] [pause] two" | Speed setting confirmation |
| "[word275] one Thousand [pause] Version" | Startup ID (system name + firmware version) |

---

## Voice Sample Arrangement and Word Addressing

### No Single "Word Number" — But There Is a Formula

The `cat-1000_words.txt` reference file lists every programmable word with its word number. The mapping to `(group, phrase_id)` follows an exact formula:

```
word_number = group × 100 + phrase_id
```

This is confirmed against all 302 words in the file — **zero mismatches**.

Examples:

| Word number | Word | group | phrase_id |
|---|---|---|---|
| 900 | Welcome | 9 | 0 (0x00) |
| 689 | Please | 6 | 89 (0x59) |
| 828 | Thank-You | 8 | 28 (0x1C) |
| 835 | Thousand | 8 | 35 (0x23) |
| 746 | Repeater | 7 | 46 (0x46) |
| 747 | Reset | 7 | 47 (0x47) |
| 560 | Line | 5 | 60 (0x3C) |
| 482 | Is | 4 | 82 (0x52) |
| 962 | Pause 3 | 9 | 62 (0x3E) |
| 980 | Good Morning | 9 | 80 (0x50) |
| 981 | Good Afternoon | 9 | 81 (0x51) |
| 982 | Good Evening | 9 | 82 (0x52) |

**Special cases:**
- Word numbers **100–199**: handled by group 1 firmware dispatch (special functions: Time of Day, Day of Week, User Functions, Tones, Macros, DTMF digits — no voice EPROM data)
- Word numbers **0–99**: digits 0–9 and multiples of 10 in group 0, accessed directly by the firmware; not user-addressable by word number

### How the Dispatch Is Called

```asm
; To play word 689 ("Please"):
MOV AH, 6       ; group   = word_number / 100
MOV AL, 89      ; phrase_id = word_number mod 100  (= 0x59)
CALL FAR 0xF000:0x0000
; BX = LPC data offset in voice EPROM
```

There is **no single "word number"** passed to the voice chip. The program ROM pre-computes group and phrase_id and passes them as AH/AL to the voice dispatch.

```asm
; To play a phrase:
MOV AH, <group>      ; 0, 2–9
MOV AL, <phrase_id>
CALL FAR 0xF000:0x0000
; BX now holds the physical offset of the LPC data in the voice EPROM
```

Complete announcements are built by chaining multiple `(group, phrase_id)` pairs through the `speak_sequence` routine at `0x18FF`. This routine reads pairs from a table in the program ROM terminated by a group byte ≥ `0x0A` or `0x80+`.

### Sequence Table Format

```
[group_0][phrase_id_0]  ; first word
[group_1][phrase_id_1]  ; second word
...
[0xFF]                  ; terminator (any byte >= 0x0A or >= 0x80)
```

Example sequences found in the program ROM:

| ROM address | Pairs played | Description |
|---|---|---|
| `0x062D` *(first startup call)* | (2,75)+(0,1)+(8,35)+(9,62)+(8,83) | System greeting/ID announcement |
| `0x5C99` | (0,4)+(0,30) | "four thirty" |
| `0x6E0F` | (0,1)+(0,40) | "one forty" |
| `0x6E14` | (0,4)+(0,40) | "four forty" |
| `0x6E19` | (0,50) | "fifty" |
| `0x6E1C` | (0,20) | "twenty" |

### Group 0 — Universal Digit Bank

Group 0 is the only group where `phrase_id` equals its spoken decimal value:

| phrase_id | Word | phrase_id | Word |
|---|---|---|---|
| `0x00` (0) | "zero" | `0x0A` (10) | "ten" |
| `0x01` (1) | "one" | `0x0B` (11) | "eleven" |
| `0x02` (2) | "two" | `0x0C` (12) | "twelve" |
| `0x03` (3) | "three" | `0x0D` (13) | "thirteen" |
| `0x04` (4) | "four" | `0x0E` (14) | "fourteen" |
| `0x05` (5) | "five" | `0x0F` (15) | "fifteen" |
| `0x06` (6) | "six" | `0x10` (16) | "sixteen" |
| `0x07` (7) | "seven" | `0x11` (17) | "seventeen" |
| `0x08` (8) | "eight" | `0x12` (18) | "eighteen" |
| `0x09` (9) | "nine" | `0x13` (19) | "nineteen" |
| | | `0x14` (20) | "twenty" |
| | | `0x1E` (30) | "thirty" |
| | | `0x28` (40) | "forty" |
| | | `0x32` (50) | "fifty" |
| | | `0x3C` (60) | "sixty" |
| | | `0x46` (70) | "seventy" |
| | | `0x50` (80) | "eighty" |
| | | `0x5A` (90) | "ninety" |

Compound numbers are assembled by concatenating group-0 phrases — e.g., "forty-seven" = `(0, 0x28)` + `(0, 0x07)`.

### Groups 2–9 — Context-Specific Word Banks

Each of groups 2–9 is a themed vocabulary bank. Where the `phrase_id` falls in the range `0x0A–0x63` (10–99), it encodes a number's decimal value in that bank's context. Phrase IDs below `0x0A` or in specific non-sequential ranges are **non-numeric words** (connectors, nouns, verbs specific to the announcement context).

| Group | Non-numeric IDs | Numeric range | Likely context |
|---|---|---|---|
| 2 | none | 10–99 (complete) | General counting / amounts |
| 3 | none | 10–99 (gapped) | Time / duration |
| 4 | none | 10–55 (gapped) | Date / period |
| 5 | `0x00–0x04` (5 words) | 30–99 (gapped) | Connector words + numbers |
| 6 | `0x00–0x0C` (13 words) | 20–99 (gapped) | Full vocabulary + connectors |
| 7 | `0x00–0x04` (5 words) | 20–99 (gapped) | Mixed |
| 8 | `0x00–0x02` (3 words) | 20–82 (gapped) | Mixed |
| 9 | `0x00–0x0A` (11 words) | 20–82 (gapped) | Many non-numeric words |

### High-Frequency Phrase Pairs (Inferred Meanings)

Certain `(group, phrase_id)` entries appear repeatedly across the ~140 sequence tables, allowing inference of their likely content:

| (Group, Phrase_id) | Appearances | Inferred word / phrase |
|---|---|---|
| `(6, 0x5D)` | ~12 sequences, often alone | Single word — likely "please" |
| `(4, 0x52)` + `(2, 0x53)` | Last 2 words in ~15 sequences | Closing phrase — likely "thank you" |
| `(8, 0x17)` + `(6, 0x21)` + `(4, 0x52)` | Recurring 3-word block | Likely "for your call" |
| `(2, 0x60)` | Mid-sentence in many sequences | Connector word (unknown) |
| `(9, 0x3F)` | Frequent mid-sentence use | Unknown word |
| `(5, 0x52)` or `(4, 0x52)` | Alone or with `(2,0x53)` | Likely "wait" or "hold" |

### Locating "Welcome" and Other Words

The **startup greeting** — the very first sequence played when the device boots — is at ROM `0x062D`:

```
(group=2, phrase=75)  →  voice EPROM 0x2A36
(group=0, phrase= 1)  →  "one"
(group=8, phrase=35)  →  voice EPROM 0x116B
(group=9, phrase=62)  →  voice EPROM 0x8203
(group=8, phrase=83)  →  voice EPROM 0xB4E6
```

This 5-word sequence is the most likely location of "welcome" or a system-name phrase. To identify it, extract and listen:

```bash
# Listen to each word of the startup greeting
python3 cat-1000_analysis.py speak --group 2 --id 75  --out startup_w1.wav
python3 cat-1000_analysis.py speak --group 0 --id 1   --out startup_w2.wav   # "one"
python3 cat-1000_analysis.py speak --group 8 --id 35  --out startup_w3.wav
python3 cat-1000_analysis.py speak --group 9 --id 62  --out startup_w4.wav
python3 cat-1000_analysis.py speak --group 8 --id 83  --out startup_w5.wav

# Or render the entire greeting as one file
python3 -c "
from cat-1000_analysis import *
fw = CAT1000Firmware()
seq = [(2,75),(0,1),(8,35),(9,62),(8,83)]
pcm = b''.join(fw.speak_phrase(g,p) for g,p in seq)
write_wav(pcm, 'startup_greeting.wav')
print('Written: startup_greeting.wav')
"
```

To scan all non-numeric words in any group (likely to contain "welcome", "please", "thank you", etc.):

```bash
# Group 6 has 13 non-numeric words at IDs 0–12
for i in $(seq 0 12); do
    python3 cat-1000_analysis.py speak --group 6 --id $i --out g6_word_${i}.wav
done

# Group 9 has 11 non-numeric words at IDs 0–10
for i in $(seq 0 10); do
    python3 cat-1000_analysis.py speak --group 9 --id $i --out g9_word_${i}.wav
done
```

### Complete speak_sequence Call Map

There are **~140 `speak_sequence` calls** in the program ROM. Selected representative calls:

| Call site | Sequence table | Phrase chain |
|---|---|---|
| `0x05BA` | `0x062D` | (2,75)+(0,1)+(8,35)+(9,62)+(8,83) |
| `0x2BAD` | `0x2BC3` | (2,24)+(2,83) |
| `0x3969` | `0x3922` | (6,50)+(5,30)+(3,24) |
| `0x515D` | `0x5167` | (7,70)+(2,50)+(0,1)+(8,0) |
| `0x51AD` | `0x51B7` | (7,70)+(2,50)+(0,2)+(8,0) |
| `0x5C99` | `0x5C9D` | (0,4)+(0,30) |
| `0x6D5F` | `0x6E02` | (8,80)+(3,70)+(6,50)+(2,50)+(3,88)+(4,82) |
| `0x9375` | `0x9375` | (3,10)+(8,80)+(7,30)+(6,99)+(7,82)+(3,84) |

---

## Python Tool Usage

File: `cat-1000_analysis.py`

```bash
# Print full hardware/firmware summary
python3 cat-1000_analysis.py summary

# List all 483 phrase entries with frame/size stats
python3 cat-1000_analysis.py phrases

# Export every unique phrase as a WAV file (8 kHz, 16-bit mono)
python3 cat-1000_analysis.py extract -o cat-1000_phrases/

# Render a single phrase to WAV
python3 cat-1000_analysis.py speak --group 0 --id 5 --out five.wav

# Speak an integer (0–9999) by concatenating digit phrases
python3 cat-1000_analysis.py number 1998 --out year_1998.wav
```

### Python API

```python
from cat-1000_analysis import VoiceEPROM, decode_lpc_frames, render_phrase_to_pcm, write_wav

# Load voice EPROM
eprom = VoiceEPROM("eprom_images/cat-1000-voice_27SF512.BIN")

# Look up phrase address
addr = eprom.lookup_phrase(group=0, phrase_id=5)   # returns int offset

# Get raw LPC bytes
raw = eprom.get_phrase_data(group=0, phrase_id=5)

# Decode LPC frames
frames = decode_lpc_frames(raw)
for f in frames:
    print(f)

# Synthesize to PCM and write WAV
pcm = render_phrase_to_pcm(raw)
write_wav(pcm, "five.wav")

# Iterate all phrases
for group, phrase_id, addr, raw_bytes in eprom.iter_all_phrases():
    print(f"G{group} P{phrase_id:#04x} @ {addr:#06x}  {len(raw_bytes)} bytes")
```

---

## Files

| File | Description |
|---|---|
| `eprom_images/cat-1000-V304A_program_27C512.BIN` | Program EPROM image (64 KB) |
| `eprom_images/cat-1000-voice_27SF512.BIN` | Voice EPROM image (64 KB) |
| `cat-1000_analysis.py` | Python analysis tool and firmware reimplementation |
| `cat-1000_analysis_session.md` | This document |

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.

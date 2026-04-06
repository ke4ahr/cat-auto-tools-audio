# CAT-1000 Speak-Sequence Analysis

Firmware version V3.04A, dated 1998-03-01.

---

## 1. Hardware Context

| Component | Part | Notes |
|-----------|------|-------|
| CPU | Intel 80C186 | 8086-compatible, 1 MB address space |
| Speech chip | TI TSP53C30 | TMS5220-compatible LPC-10 codec |
| Program EPROM | 27C512 (64 KB) | `eprom_images/cat-1000-V304A_program_27C512.BIN` |
| Voice EPROM | 27SF512 (64 KB) | `eprom_images/cat-1000-voice_27SF512.BIN` |

---

## 2. Physical Memory Map

| Physical address | Segment | Contents |
|-----------------|---------|---------|
| `0x00000–0x0FFFF` | `0x0000` | Program EPROM — firmware code and data |
| `0xF0000–0xFFFFF` | `0xF000` | Voice EPROM — phrase dispatch and LPC speech data |
| `0x10000–0x101FF` | `0x1000` | RAM, 512 bytes |

On reset the CPU starts at `0xFFFF:0x0000` (physical `0xFFFF0`). The chip-select unit immediately redirects via a reset-mirror stub, and the real startup entry point is at `0x0000:0x0413` in the program EPROM.

---

## 3. Voice EPROM Layout

| Offset range | Contents |
|-------------|---------|
| `0x0000–0x009E` | 8086 phrase-dispatch routine, called via `CALL FAR 0xF000:0x0000` |
| `0x009F–0x0641` | Phrase index tables -- 9 groups, 482 entries total |
| `0x0642–0xC4C1` | TMS5220 LPC bit-packed speech data (~49 KB, 481 unique clips) |
| `0xC4C2–0xFCFF` | Unused / `0x00` fill |
| `0xFD00–0xFD39` | `save_tsp_state` |
| `0xFE00–0xFE43` | `restore_tsp_state` |

### 3.1 Phrase Dispatch Protocol

The program EPROM calls the voice EPROM as a far subroutine:

```asm
; Set AH = group number (0, 2–9)
; Set AL = phrase_id
CALL FAR 0xF000:0x0000

; Returns:
;   BX = voice EPROM offset of LPC data for that phrase
;   BX = 0x81F1  if phrase not found (error sentinel)
```

Phrase index entries are 3 bytes each: `[phrase_id][offset_lo][offset_hi]`.
Word number formula (verified against all 482 entries, zero mismatches):

```
word_number = group × 100 + phrase_id
```

### 3.2 Dispatch Groups

| Group | Table offset | Entry count | Word-number range |
|-------|-------------|------------|------------------|
| 0 | `0x009F` | 28 | 0–90 (digits + decade words 0–20, 30, 40, 50, 60, 70, 80, 90) |
| 2 | `0x00F3` | 79 | 200–278 |
| 3 | `0x01E0` | 54 | 300–353 |
| 4 | `0x0282` | 43 | 410–486 |
| 5 | `0x0303` | 50 | 500–579 |
| 6 | `0x0399` | 65 | 600–664 |
| 7 | `0x0459` | 61 | 700–761 |
| 8 | `0x0510` | 60 | 800–872 |
| 9 | `0x05C4` | 42 | 900–982 |

Group 1 is absent. Phrases in group 1 (e.g. those that appear in the time-announcement self-ID sequence) are not resolved by the standard dispatch and return the error sentinel.

---

## 4. Key Speech Functions (Program EPROM, Segment 0x0000)

| Address | Function | Notes |
|---------|---------|-------|
| `0x18B0` | `speak_phrase` | Play one phrase; AH = group, AL = phrase_id |
| `0x18B2` | `speak_phrase` (alternate entry) | AX = encoded phrase (AH = group, AL = pid) |
| `0x18FF` | `speak_sequence` | Play a sequence of phrases from a table at CS:BX |
| `0x48B9` | (setup helper) | Precedes most speak_sequence calls |
| `0x48DD` | (cleanup helper) | Follows most speak_sequence calls |
| `0x2638` | (unknown) | Called between MOV BX and speak_sequence at many sites |

---

## 5. speak_sequence Table Format

Tables live in the **program EPROM** (segment `0x0000`). This is a critical distinction: the tables are read via `CS:BX` where `CS = 0x0000`, so they are in the program EPROM file, not the voice EPROM file.

```
[group_0][phrase_id_0]  ; first phrase pair (2 bytes)
[group_1][phrase_id_1]  ; second phrase pair
...
[0x0A or higher]        ; terminator — any byte >= 0x0A in the group field ends the sequence
```

`speak_sequence` iterates through pairs, calling the voice EPROM dispatch for each, until it reads a group byte `>= 0x0A`.

---

## 6. Finding Call Sites — Step-by-Step Method

### Step 1: Locate speak_sequence in the program EPROM

The function at `0x18FF` is called via `CALL NEAR` (opcode `E8 lo hi`) where the signed 16-bit offset satisfies:

```
(call_addr + 3) + signed_offset  ≡  0x18FF  (mod 0x10000)
```

### Step 2: Scan the program EPROM for all matching CALL NEAR instructions

```python
for addr in range(len(prog) - 3):
    if prog[addr] == 0xE8:
        offset = struct.unpack_from('<h', prog, addr+1)[0]
        target = (addr + 3 + offset) & 0xFFFF
        if target == 0x18FF:
            call_sites.append(addr)
```

This finds all 149 call sites.

### Step 3: Find the sequence table address for each call

For each call site, scan backward up to 600 bytes for `MOV BX, imm16` (opcode `BB lo hi`). The most recently loaded immediate value before the call is the sequence table offset.

```python
for off in range(call_addr - 3, max(0, call_addr - 600), -1):
    if prog[off] == 0xBB:
        table_addr = prog[off+1] | (prog[off+2] << 8)
        break
```

**Exception — POP BX pattern:** Two call sites (at `0x3911` and `0xA1C9`) use a `PUSH BX / ... / POP BX / CALL 0x18FF` pattern. In these cases, BX is pushed onto the stack before a helper call, and popped back immediately before the speak_sequence call. The real table address comes from the `MOV BX` that precedes the `PUSH BX`, which may be much further back (or in a dispatching jump table).

### Step 4: Decode the sequence at the table address

Read pairs `(group, phrase_id)` from the program EPROM at the table offset until a byte `>= 0x0A` is encountered:

```python
def decode_sequence_at(tbl_addr, prog):
    words = []
    i = 0
    while i < 40:
        g = prog[tbl_addr + i]
        p = prog[tbl_addr + i + 1]
        if g >= 0x0A:
            break
        words.append(phrase_name(g * 100 + p))
        i += 2
    return words
```

---

## 7. Common Error: Reading from the Wrong EPROM

Earlier analysis (in a previous session) read sequence tables from the **voice EPROM** file instead of the program EPROM. Because the voice EPROM at those offsets happens to contain LPC data that starts with bytes that can accidentally parse as low group numbers and phrase IDs, plausible-looking but wrong sequences were produced.

Symptom: sequences such as `word617` (group 6, pid 17) appeared to exist. In reality, `word617` has no entry in the voice EPROM dispatch table for group 6. The actual sequences at those program EPROM addresses, read correctly, are named phrases like "Six Seventeen" (the two-word spoken form of area code 617).

**Rule:** Sequence tables → program EPROM file. LPC speech data → voice EPROM file.

---

## 8. Complete Call-Site Decode (149 Sites)

Table columns: `CALL` = address of the `CALL 0x18FF` instruction; `TABLE` = program EPROM offset of the sequence table; `SEQUENCE` = decoded phrase list.

```
CALL     TABLE    SEQUENCE
0x05BA   0x062D   Cat One Thousand [Pause 3] Version
0x2BAD   0x2BC3   All Clear
0x2BDB   0x2C16   Clear Flag S (plural)
0x2C3A   0x2C6B   Control Ready
0x2C7C   0x2C82   Manual Exit
0x2C8F   0x2C87   Timer Exit
0x2E39   0x2E4F   All Clear
0x3290   0x32D6   Autopatch Timer Reset
0x3808   0x384E   Autopatch Timer Reset
0x3911   *        O K Up  /  O K Down  (conditional — two code paths)
0x3969   0x3922   O K Down
0x39B3   0x39D4   Control Is Down
0x39CA   0x39E2   Macro Is Down
0x3A12   0x3A37   Control Down
0x3A2D   0x3A3C   Control Up
0x3A63   0x3A88   Macro Down
0x3A7E   0x3A8D   Macro Up
0x3AD5   0x3AF9   Control Is Down
0x3AEC   0x3B07   Macro Is Down
0x3B3A   0x3B72   Control Down
0x3B60   0x3B77   Control Up
0x3B9E   0x3BCC   Macro Down
0x3BBF   0x3BD1   Macro Up
0x3CC1   0x3CE8   Receiver Connect
0x4752   0x4765   Keypad Test [Pause 3]
0x4799   0x47AC   Keypad Test [Pause 3]
0x4C5F   0x4C76   File
0x4C6C   0x4C79   Load
0x4D64   0x4D99   File
0x4D73   0x4D9C   Data Modified
0x4D81   0x4D90   File I D Is
0x4DE0   0x4E58   Enter Control
0x4E23   0x4E5D   Data Inputs O K
0x515D   0x5167   S B One Switch
0x51AD   0x51B7   S B Two Switch
0x54EF   0x560E   Start Test Now
0x5567   0x560E   Start Test Now
0x5673   0x5766   Start Message
0x5963   0x596D   Frequency Load Error
0x5C99   0x5C9D   Four Thirty
0x5CFD   0x5D33   C T C S S
0x5D54   0x5D7D   Power
0x5D8C   0x5DA9   Receiver
0x5DB8   0x5DD5   Transmitter
0x5DE4   0x5E01   D C Power
0x5E69   0x5EA1   Frequency Load
0x6062   0x606C   Frequency Load Error
0x62A2   0x62BC   Transmit
0x62EE   0x630E   Transmit
0x6307   0x62B5   H F Control
0x6336   0x6349   H F Down
0x640D   0x6430   S Q Off
0x6429   0x6437   S Q On
0x66BC   0x673A   Preset Position
0x66D1   0x673A   Preset Position
0x6710   0x673F   Mega Hertz
0x68BE   0x68C7   H F Data Connect Error
0x6C45   0x6C68   S Q Off
0x6C61   0x6C6F   S Q On
0x6D5F   0x6E02   V F O B Frequency Is
0x6D6D   0x6E0F   One Forty
0x6D7B   0x6E14   Four Forty
0x6D89   0x6E19   Fifty
0x6D97   0x6E1C   Twenty
0x6DE5   0x6E1F   Mega Hertz
0x7257   0x7265   Reset Data Load Completed [Pause 4]
0x7285   0x7293   [Pause 4] Alternate Data Load [Pause 4]
0x7962   0x7969   Cat One Thousand
0x7A36   0x7A9B   Autopatch [Pause 4]
0x7A86   0x7AA0   [Pause 4] Warning Autopatch Number Lockout
0x7A95   0x7AAB   [Pause 4] Warning Nine One One Number Lockout
0x7AD3   0x7AF3   Autopatch Connect [Pause 4]
0x7BE4   0x7C2B   Autopatch Call Two
0x7BF2   0x7C32   Completed At
0x7C45   0x7C4A   Autopatch Time Out
0x7C5F   0x7C64   Autopatch Release
0x7DA7   0x7DE0   Speed Call [Pause 1] Two
0x7E61   0x7E9A   Speed Call [Pause 1] Two
0x7F1B   0x7F54   Speed Call [Pause 1] Two
0x7FD9   0x8012   Speed Call [Pause 1] Two
0x806F   0x8080   Telephone Line In Service
0x8532   0x85EC   Call Four
0x856E   0x85F1   Repeater Reset
0x8597   0x85F6   Call Connect
0x8907   0x8921   Cat One Thousand Control
0x8963   0x897F   Cat One Thousand Control
0x8AC0   0x8CCF   Manual Exit
0x8B13   0x8CCF   Manual Exit
0x8C47   0x8CD4   Control O K
0x8CC1   0x8D01   Keypad Error [Pause 3] No Data
0x8D94   0x8E07   Macro Control Is
0x8DAB   0x8E0E   Macro Data Is
0x8DF1   0x8E15   Position
0x8DFE   0x8E18   Is Clear
0x8ED4   0x8EE5   Cancel Macro Position
0x8F83   0x8F94   Cancel Position
0x90C0   0x912A   Week End
0x9103   0x912F   Program Data Is
0x9141   0x9157   Position
0x914E   0x915A   Is Clear
0x919B   0x91AC   Cancel Clock Control Position
0x9219   0x9235   Program File
0x9226   0x923A   O K
0x931C   0x9375   D V R Program Set For
0x9334   0x9375   D V R Program Set For
0x933B   0x936A   D V M Fifty Eight
0x93E6   0x93EF   Telephone Set For Ten P P S
0x941E   0x9450   H F Radio Set For
0x9441   0x9464   F T Seven Sixty Seven
0x953E   0x957C   Position
0x954B   0x9584   Is
0x9566   0x957C   Position
0x9573   0x957F   Is Clear
0x97D3   0x97DC   No Message
0x9909   0x991F   Frequency Position
0x9916   0x9924   Is Clear
0x994B   0x9950   No C T C S S
0x9A73   0x9A89   Position
0x9A80   0x9A8C   Is Clear
0x9D05   0x9D26   Preset Code
0x9D0A   0x9D26   Preset Code
0xA18A   **       Area code dispatch path A (see Section 9)
0xA1C9   **       Area code dispatch path B (see Section 9)
0xA4E3   0xA50C   Telephone Number Is
0xA4FB   0xA513   [Pause 3] I D As
0xA520   0xA536   Position
0xA52D   0xA539   Is Clear
0xA57B   0xA5A4   Telephone Number Is
0xA593   0xA5AB   [Pause 3] I D As
0xA5B8   0xA5CE   Position
0xA5C5   0xA5D1   Is Clear
0xA613   0xA63C   Telephone Number Is
0xA62B   0xA643   [Pause 3] I D As
0xA650   0xA666   Position
0xA65D   0xA669   Is Clear
0xA7B0   0xA7CA   Lockout Number Is
0xA7D5   0xA7EB   Lockout Position
0xA7E2   0xA7F0   Is Clear
0xA8AC   0xA8C5   Area Code Number Is
0xA8D2   0xA8E8   Area Code Position
0xA8DF   0xA8EF   Is Clear
0xA98F   0xA9A9   Preset Number Is
0xA9B4   0xA9BD   Preset Number Is Clear
0xAA86   0xAAAF   Telephone Number Is
0xAA9E   0xAAB6   [Pause 3] I D As
0xAAC3   0xAAD9   Position
0xAAD0   0xAADC   Is Clear
0xADB2   0xADC8   Position
0xADBF   0xADCB   Is Clear
```

`*` = table address is dynamic (resolved at runtime — see Sections 9 and 10).
Phrases in brackets `[ ]` are pauses/sounds rather than spoken words.

---

## 9. Area Code Announcement Dispatch (0xA090–0xA1D5)

The autopatch subsystem announces the area code of an incoming or outgoing call. A dispatch table at `0xA090` maps area codes to two-word spoken sequences and then branches to one of two execution paths.

### 9.1 Sequence tables

Each table is two bytes: `[group][phrase_id]` for "Six", followed by `[group][phrase_id]` for the units word, then a terminator.

| Area code | Table address | Sequence |
|-----------|--------------|---------|
| 601 | `0xA229` | Six Zero One |
| 602 | `0xA230` | Six Zero Two |
| 603 | `0xA237` | Six Zero Three |
| 604 | `0xA23E` | Six Zero Four |
| 605 | `0xA245` | Six Zero Five |
| 606 | `0xA24C` | Six Zero Six |
| 607 | `0xA253` | Six Zero Seven |
| 608 | `0xA25A` | Six Zero Eight |
| 609 | `0xA261` | Six Zero Nine |
| 610 | `0xA268` | Six Ten |
| 611 | `0xA26D` | Six Eleven |
| 612 | `0xA272` | Six Twelve |
| 613 | `0xA277` | Six Thirteen |
| 614 | `0xA27C` | Six Fourteen |
| 615 | `0xA281` | Six Fifteen |
| 616 | `0xA286` | Six Sixteen |
| 617 | `0xA28B` | Six Seventeen |
| 618 | `0xA290` | Six Eighteen |
| 619 | `0xA295` | Six Nineteen |

### 9.2 Two execution paths

The dispatch table branches to either `0xA17B` or `0xA1BA` depending on the area code (the distinction's meaning is not yet determined — possibly toll vs. non-toll, local vs. long-distance, or primary vs. alternate trunk).

**Path A (`0xA17B`, calls reach speak_sequence at `0xA18A`):**
Area codes 601, 602, 607–612, 615–619 load BX and jump to `0xA17B`.
`0xA17B` does: `PUSH BX` → helper calls → `CALL 0x18B2` (speak_phrase AX=0x0827 = word827) → `POP BX` → `CALL 0x18FF`.

**Path B (`0xA1BA`, calls reach speak_sequence at `0xA1C9`):**
Area codes 603–606, 610, 613–614 load BX and jump to `0xA1BA`.
`0xA1BA` does: `PUSH BX` → helper calls → `CALL 0x18B2` → `POP BX` → `CALL 0x18FF`.

In both paths, `PUSH BX / ... / POP BX` is the mechanism that preserves the table address across intervening calls. The `MOV BX, 0x18FF` found 85 bytes before the actual speak_sequence call in the earlier search was the `MOV BX, 0xA295` from the dispatch table, selected by whichever code path was taken.

### 9.3 Resolution of the "word617" mystery

A previous analysis session read sequence tables from the voice EPROM file and reported that an unlabelled phrase `word617` (group 6, phrase_id 17) appeared in a sequence "Sixty word617 Kilo". This was a false result: the voice EPROM at the offsets in question contains LPC speech data, not sequence tables. When the same offsets are read from the correct source (the program EPROM), the content is the area code 617 dispatch entry "Six Seventeen". No phrase `word617` exists.

---

## 10. Conditional Call Sites

### 10.1 Call at 0x3911 — O K Up / O K Down

This call site uses the `PUSH BX / POP BX` pattern. Two code paths converge at `0x3901`:

```asm
0x38F1:  MOV BX, 0x391B     ; "O K Up" table
0x38F5:  JMP SHORT 0x3901
  ...
0x38FD:  MOV BX, 0x3922     ; "O K Down" table
; fall through to:
0x3901:  PUSH BX
0x3902:  CALL 0x3331         ; helper
0x3905:  CALL 0x08C3         ; helper
0x3908:  CALL 0x48B9         ; setup
0x390D:  CALL 0x2638         ; helper (uses different BX internally)
0x3910:  POP BX              ; restore table address
0x3911:  CALL 0x18FF         ; speak_sequence
0x3914:  CALL 0x48DD         ; cleanup
0x3917:  RET
```

The third call at `0x3908` temporarily overwrites BX (MOV BX, 0xE8CF is part of its internal work), which is why a naive backward scan finds `0xE8CF` rather than the real table address.

Sequences:
- Table `0x391B`: O K Up
- Table `0x3922`: O K Down

---

## 11. Self-ID Sequences (Unresolved Call Mechanism)

Two sequence tables exist in the program EPROM containing self-identification phrases, but no `MOV BX + CALL NEAR 0x18FF` pair was found that uses them directly. The byte patterns `BB F1 76` and `BB 1C 79` (which would encode `MOV BX, 0x76F1` and `MOV BX, 0x791C`) do not appear anywhere in the program EPROM.

### 11.1 prog[0x76F1] — "Cat One Thousand Repeater"

```
0x76F1:  02 4B   ; Cat    (group=2, pid=0x4B=75)
0x76F3:  00 01   ; One    (group=0, pid=1)
0x76F5:  08 23   ; Thousand (group=8, pid=0x23=35)
0x76F7:  07 2E   ; Repeater (group=7, pid=0x2E=46)
0x76F9:  03 02   ; word302 (group=3, pid=2 — not in group 3 table; returns Pause 1)
0x76FB:  4B ...  ; terminator
```

Note: `word302` (group 3, phrase_id 2) has no entry in the group 3 dispatch table (smallest pid in group 3 is 10). A call to speak_phrase for word302 would return the error sentinel `0x81F1`, which is the same address as Pause 1. The repeater would therefore say "CAT ONE THOUSAND REPEATER [pause]".

### 11.2 prog[0x791C] — "Cat One Thousand Repeater The Time Is"

```
0x791C:  02 4B   ; Cat
0x791E:  00 01   ; One
0x7920:  08 23   ; Thousand
0x7922:  07 2E   ; Repeater
0x7924:  08 1E   ; The (short-E variant, word830)
0x7926:  08 26   ; Time (word838)
0x7928:  04 52   ; Is (word482)
0x792A:  01 00   ; word100 (group 1, pid 0 — group 1 not in dispatch; returns error)
0x792C:  01 03   ; word103 (group 1, pid 3 — same)
0x792E:  04 07   ; word407
0x7930:  ...     ; (continues with further time-digit entries)
```

The group-1 phrases (word100, word103) are not in the voice EPROM's dispatch table — they would return the error sentinel if called through the normal speak_phrase path. These may be placeholder slots for time digits that are inserted dynamically (e.g., the hour and minute are computed and appended to the sequence at runtime).

### 11.3 Code at 0x76C7–0x76EF

The code immediately surrounding these tables includes a function that loads BX with RAM-range addresses (`0x3638`, `0x3678`) and performs byte-copying operations. The analysis session notes indicate that 7 bytes are copied from `CS:0x76F9` to `DS:0x3678` — the copied data begins at the `word302` entry in the self-ID table, consistent with a mechanism that patches the callsign or tail portion of the sequence at runtime.

The actual call mechanism for these self-ID sequences remains unresolved. Possible explanations:
- BX is computed via arithmetic (e.g., a base address plus an offset) rather than loaded as a literal immediate.
- The sequences are not called via the standard `speak_sequence` path at all, but via a separate routine that reads from a RAM buffer built from the copied data.
- The call occurs through an indirect branch not captured by the linear `MOV BX` backward scan.

---

## 12. Notable Sequences — Feature Inventory

### Startup and Identification
| Sequence | Call address |
|----------|-------------|
| Cat One Thousand [Pause 3] Version | `0x05BA` |
| Cat One Thousand | `0x7962` |
| Cat One Thousand Control | `0x8907`, `0x8963` |

### Autopatch (telephone interconnect)
| Sequence | Call address |
|----------|-------------|
| Autopatch Timer Reset | `0x3290`, `0x3808` |
| Autopatch [Pause 4] | `0x7A36` |
| Autopatch Connect [Pause 4] | `0x7AD3` |
| Autopatch Call Two | `0x7BE4` |
| Autopatch Time Out | `0x7C45` |
| Autopatch Release | `0x7C5F` |
| [Pause 4] Warning Autopatch Number Lockout | `0x7A86` |
| [Pause 4] Warning Nine One One Number Lockout | `0x7A95` |
| Telephone Line In Service | `0x806F` |
| Telephone Number Is | `0xA4E3`, `0xA57B`, `0xA613`, `0xAA86` |
| Area Code Number Is | `0xA8AC` |
| Lockout Number Is | `0xA7B0` |
| Preset Number Is | `0xA98F` |
| Area codes 601–619 (spoken) | `0xA18A`, `0xA1C9` |
| Telephone Set For Ten P P S | `0x93E6` |

### Controls and Linking
| Sequence | Call address |
|----------|-------------|
| Manual Exit | `0x2C7C`, `0x8AC0`, `0x8B13` |
| Timer Exit | `0x2C8F` |
| Control Ready | `0x2C3A` |
| Control Is Down / Up | `0x39B3`, `0x3AD5` / `0x3A2D`, `0x3B60` |
| Control Down / Up | `0x3A12`, `0x3B3A` / `0x3A2D`, `0x3B60` |
| Macro Is Down / Up | `0x39CA`, `0x3AEC` / (implicit) |
| Macro Down / Up | `0x3A63`, `0x3B9E` / `0x3A7E`, `0x3BBF` |
| H F Control | `0x6307` |
| H F Down | `0x6336` |
| H F Data Connect Error | `0x68BE` |
| S Q On / Off | `0x6429`, `0x6C61` / `0x640D`, `0x6C45` |
| Receiver Connect | `0x3CC1` |
| Control O K | `0x8C47` |

### File and Data Operations
| Sequence | Call address |
|----------|-------------|
| File | `0x4C5F`, `0x4D64` |
| Load | `0x4C6C` |
| Data Modified | `0x4D73` |
| File I D Is | `0x4D81` |
| Enter Control | `0x4DE0` |
| Data Inputs O K | `0x4E23` |
| Reset Data Load Completed | `0x7257` |
| Alternate Data Load | `0x7285` |
| Frequency Load | `0x5E69` |
| Frequency Load Error | `0x5963`, `0x6062` |
| Program Data Is | `0x9103` |
| Program File | `0x9219` |
| O K | `0x9226` |
| Clear Flag S (plural) | `0x2BDB` |
| All Clear | `0x2BAD`, `0x2E39` |

### DVR and External Equipment
| Sequence | Call address |
|----------|-------------|
| D V R Program Set For | `0x931C`, `0x9334` |
| D V M Fifty Eight | `0x933B` |
| H F Radio Set For | `0x941E` |
| F T Seven Sixty Seven | `0x9441` |
| V F O B Frequency Is | `0x6D5F` |
| One Forty / Four Forty / Fifty / Twenty / Four Thirty | `0x6D6D`–`0x6D97`, `0x5C99` |
| Mega Hertz | `0x6710`, `0x6DE5` |

"FT Seven Sixty Seven" refers to the Yaesu FT-767 HF transceiver. The sequences in this group announce radio mode or frequency settings via the CAT interface.
"DVR" is Digital Voice Recorder. "DVM" is likely Digital Voltmeter or a related instrument.

### Diagnostics and Keypad
| Sequence | Call address |
|----------|-------------|
| Keypad Test [Pause 3] | `0x4752`, `0x4799` |
| Keypad Error [Pause 3] No Data | `0x8CC1` |
| S B One / Two Switch | `0x515D`, `0x51AD` |
| Start Test Now | `0x54EF`, `0x5567` |
| Start Message | `0x5673` |
| Power / Receiver / Transmitter / D C Power | `0x5D54`–`0x5DE4` |
| C T C S S | `0x5CFD` |
| No C T C S S | `0x994B` |
| Repeater Reset | `0x856E` |
| Call Connect / Call Four | `0x8597`, `0x8532` |
| O K Up / O K Down | `0x3911` |

### Speed Dial
| Sequence | Call address |
|----------|-------------|
| Speed Call [Pause 1] Two | `0x7DA7`, `0x7E61`, `0x7F1B`, `0x7FD9` |
| Completed At | `0x7BF2` |

### Scheduling and Clock
| Sequence | Call address |
|----------|-------------|
| Week End | `0x90C0` |
| Cancel Clock Control Position | `0x919B` |
| D V R Program Set For | `0x931C`, `0x9334` |

### Macros and Positions
| Sequence | Call address |
|----------|-------------|
| Macro Control Is | `0x8D94` |
| Macro Data Is | `0x8DAB` |
| Cancel Macro Position | `0x8ED4` |
| Cancel Position | `0x8F83` |
| Position / Is Clear | various `0x8D`–`0xAD` range |

### Preset and Tone
| Sequence | Call address |
|----------|-------------|
| Preset Position | `0x66BC`, `0x66D1` |
| Preset Code | `0x9D05`, `0x9D0A` |
| Preset Number Is | `0xA98F` |

---

## 13. Undocumented Words

Nine phrase numbers exist in the voice EPROM dispatch tables with no entry in official CAT-1000 documentation. Their spoken content has been identified by listening to the synthesized LPC audio:

| Word | Group | pid | LPC address | Label | Notes |
|------|-------|-----|------------|-------|-------|
| 299 | 2 | 99 | -- | Comm | Undocumented; identified by ear |
| 377 | 3 | 77 | -- | File | Undocumented; identified by ear |
| 393 | 3 | 93 | -- | Fall | Undocumented; identified by ear |
| 457 | 4 | 57 | -- | Heat | Undocumented; identified by ear |
| 486 | 4 | 86 | `0x5DAB` | Index | Follows "Inches" (word485). Never found in any speak_sequence table. |
| 703 | 7 | 3 | -- | Percent | Undocumented; identified by ear |
| 704 | 7 | 4 | -- | Pressure | Undocumented; identified by ear |
| 852 | 8 | 52 | `0xAC1D` | Today's | Follows "Type" (word851, pid=51), precedes "U" (word870, pid=70). No sequence context found. |
| 910 | 9 | 10 | `0xBE59` | Windchill | Group 9 pid=10, between "Wrong" (pid=9) and "X" (pid=20). |

All 9 words are included in `cat-1000_phrases.csv` with their identified labels. The `notes` field in that CSV is set to `"undocumented"` for these 9 rows.

---

## 14. LPC Clip Extraction

### 14.1 Export script

`cat-1000_lpc_export.py` scans the voice EPROM for TMS5220 STOP frames to determine clip boundaries and exports raw `.lpc` files.

```bash
# Re-generate cat-1000_clips.csv (auto-looks up names from cat-1000_phrases.csv)
python3 cat-1000_lpc_export.py --dump-csv > cat-1000_clips.csv

# Export all clips as raw .lpc files (default MSB-first bit order for synthesis)
python3 cat-1000_lpc_export.py cat-1000_clips.csv -o cat-1000_lpc_clips/
```

### 14.2 Name lookup behaviour

The `--dump-csv` mode now auto-reads `cat-1000_phrases.csv` from the script directory and uses real names where available. Words with no entry in `cat-1000_phrases.csv` receive a placeholder name `word{N}`. This means re-running `--dump-csv` produces a usable CSV without manual editing for all 481 named phrases; all words have been assigned labels in `cat-1000_phrases.csv`, including the 9 undocumented words (see Section 13).

### 14.3 Known overlapping clips

Some phrases share byte ranges in the voice EPROM. Each clip is still a single contiguous byte range; they simply start at different offsets within a shared tail.

| Words | Start addresses | Shared tail end |
|-------|----------------|----------------|
| Pause 1–4 (960–963) | `0x81F1`, `0x81FA`, `0x8203`, `0x820D` | `0x8259` / `0x8296` |
| Chime 1–3 (964–966) | `0xBC0E`, `0xBC29`, `0xBC44` | `0xBD48` |

### 14.4 Edge case: 0x81F1 dual role

`0x81F1` is both:
- The error sentinel returned by the voice EPROM dispatch when a phrase is not found.
- The real LPC data address for Pause 1 (word 960, group 9, phrase_id 60).

Do not filter `0x81F1` from the dispatch table entries — it is a genuine phrase address.

### 14.5 LPC data boundary

```
LPC_DATA_START = 0x0642   # first byte of speech data in voice EPROM
LPC_DATA_END   = 0xC4C1   # exclusive end (first byte of 0x00 fill)
```

Scanning past `LPC_DATA_END` causes the scanner to traverse the `0x00` fill region. All-zero bytes decode as SILENCE frames indefinitely, eventually producing a false STOP frame far into the TSP save/restore routines (`0xFD00+`). The scan limit must be capped at `LPC_DATA_END`.

Note: `0xC42C` is the start address of the last phrase (word982, "Good Evening"). `0xC4C1` is its exclusive end and the first byte of the `0x00` fill.

---

## 15. TMS5220 Frame Format Reference

All values are LSB-first within each byte. Frame types are determined by the 4-bit energy field (bits 0–3 of the first nibble):

| Energy | Frame type | Total bits | Notes |
|--------|-----------|-----------|-------|
| `0x0` | SILENCE | 4 | No speech, no parameters |
| `0xF` | STOP | 4 | End of phrase |
| other + repeat=1 | REPEAT | 11 | 4 (energy) + 1 (repeat) + 6 (pitch) |
| other + pitch=0 | UNVOICED | 29 | + K1–K4: 5+5+4+4 bits |
| other + pitch>0 | VOICED | 50 | + K1–K10: 5+5+4+4+4+4+4+3+3+3 bits |

Pitch table index 63 maps to pitch value 0 (unvoiced), even though the index is non-zero.

End byte of a clip = `ceil(bits_consumed / 8)` from the clip start address (exclusive end).

---

*Document generated from analysis of `cat-1000-V304A_program_27C512.BIN` and `cat-1000-voice_27SF512.BIN` (firmware V3.04A, 1998-03-01).*

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.

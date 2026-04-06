# TMS5xxx LPC Speech Synthesizer Decoder — Clean-Room Specification

## Document Information
- **Purpose**: Detailed functional specification for implementing a clean-room Python version of a TMS5xxx LPC speech synthesizer decoder
- **Source Analysis Date**: 27 March 2026
- **Target**: Python 3.10+ with no GPL dependencies
- **Status**: Part 1 of 2

---

## 1. System Overview

### 1.1 What This System Does

This is a command-line tool that decodes Texas Instruments LPC (Linear Predictive Coding) speech data — the same encoding used in vintage "Speak & Spell" toys — and renders it to standard audio files (WAV, AU, AIFF, raw PCM).

The TMS5xxx family of speech synthesis chips (TMS5100, TMS5110, TMS5200, TMS5220) use a 10th-order lattice filter driven by either a chirp excitation (for voiced sounds) or white noise (for unvoiced sounds like fricatives). The LPC data is a compact bitstream encoding energy, pitch period, and 10 reflection coefficients per frame.

### 1.2 High-Level Data Flow

```
LPC Input Data ──► Bitstream Decoder ──► Frame Parameters ──► LPC Synthesizer ──► 8kHz PCM
                                                                                      │
                                                                              Sample Rate Converter
                                                                                      │
                                                                              WAV/AU/AIFF Writer
```

### 1.3 Supported Chip Variants

| Chip | Pitch Bits | Pitch Entries | Chirp Size | Notes |
|------|-----------|---------------|------------|-------|
| TMS5100 | 5 | 32 | 50 | Earliest, decimal chirp values |
| TMS5110 | 5 | 32 | 32 | Similar to 5100 |
| TMS5200 | 6 | 64 | 52 | Hex chirp values, wider pitch |
| TMS5220 | 6 | 64 | 52 | Most common, hex chirp values |

---

## 2. Input Formats

### 2.1 LPC Data Sources

The decoder accepts LPC data in six mutually exclusive formats (only one per invocation):

1. **Raw binary file** (`strbin=`): File read as raw bytes. This is the most direct format — the bytes are the LPC bitstream.

2. **Decimal CSV string** (`str=`): Comma-separated decimal byte values on the command line. Example: `str=165,79,122,211,60,90`. Values 0-255. An optional label prefix before `:` is stripped (e.g., `str=isle:69,171,54,...`).

3. **Hex CSV string** (`strhex=`): Comma-separated hex byte values. Optional `0x` prefix. Example: `strhex=A5,4F,7A,D3` or `strhex=0xA5,0x4F`.

4. **Decimal CSV text file** (`strfile=`): Text file containing the same format as `str=`.

5. **Hex CSV text file** (`strhexfile=`): Text file containing the same format as `strhex=`. Trailing commas acceptable.

6. **ROM address** (`addr=`): Hex address within a loaded VSM ROM file. Requires `rom0=` to specify the ROM.

### 2.2 Input Validation Rules

- **Decimal strings**: Only characters `0-9 , : - SPACE TAB CR LF` are valid
- **Hex strings**: Only characters `0-9 a-f A-F , : x X SPACE TAB CR LF` are valid
- **Text files**: Maximum 1MB, validated with same character rules as their string counterpart
- **Binary files**: Maximum 32KB (`rom_size_max = 16384 * 2`)
- **Empty inputs**: Rejected with error

### 2.3 Chip Definition File Format

A plain text file with `key=value` pairs, one per line:

```
processor=tms5220
chirp_hx=0x00, 0x03, 0x0f, ...          (hex chirp values, OR:)
chirp=0, 42, 212, 50, ...               (decimal chirp values)
energy=0, 1, 2, 3, 4, 6, 8, 11, ...     (16 energy levels)
pitch_count=64                            (32 or 64)
pitch=0, 15, 16, 17, ...                 (pitch period lookup table)
k0=-501, -498, -497, ...                 (32 values, reflection coeff × 512)
k1=-328, -303, -274, ...                 (32 values)
k2=-441, -387, -333, ...                 (16 values)
k3=-328, -273, -217, ...                 (16 values)
k4=-328, -282, -235, ...                 (16 values)
k5=-256, -212, -168, ...                 (16 values)
k6=-308, -260, -212, ...                 (16 values)
k7=-256, -161, -66, ...                  (8 values)
k8=-256, -176, -96, ...                  (8 values)
k9=-205, -132, -59, ...                  (8 values)
```

Key observations:
- Chirp can be decimal (`chirp=`) or hex (`chirp_hx=`), not both
- Energy and pitch can also have `_hx=` hex variants
- K coefficients are signed 16-bit integers, pre-scaled by 512
- `pitch_count=64` implies 6-bit pitch index; `pitch_count=32` implies 5-bit

### 2.4 VSM ROM Format

Voice Synthesis Memory ROMs are raw binary files up to 16KB each. Two ROMs can be loaded:
- `rom0=` loads at offset 0x0000
- `rom1=` loads at offset 0x4000

The ROM contains a pointer table at the beginning. Each entry is a 16-bit little-endian address pointing to an LPC bitstream within the ROM. The pointer table layout is specific to the Speak & Spell ROM organization (letters A-Z at offset 0x0C, followed by beep, digits 0-9, phrases).

---

## 3. LPC Bitstream Format

### 3.1 Bit Reading

Bits are read LSB-first from each byte. However, each ROM byte is **bit-reversed** before reading (MSB↔LSB swap). The bit reader maintains a byte pointer and a bit offset within the current byte. When the bit offset reaches 8, it advances to the next byte.

**Bit reversal algorithm** (applied to each byte before bit extraction):
```
a = (a >> 4) | (a << 4)           # Swap nibbles
a = ((a & 0xCC) >> 2) | ((a & 0x33) << 2)  # Swap pairs
a = ((a & 0xAA) >> 1) | ((a & 0x55) << 1)  # Swap bits
```

**Bit extraction** (extract N bits from the stream):
```
data = rev(current_byte) << 8
if bit_offset + N > 8:
    data |= rev(next_byte)
data <<= bit_offset
value = data >> (16 - N)
bit_offset += N
if bit_offset >= 8:
    bit_offset -= 8
    advance to next byte
```

### 3.2 Frame Structure

Each frame begins with a 4-bit energy index. The frame structure depends on the energy value:

#### Silence Frame (energy_idx == 0)
```
[4 bits: energy_idx = 0]
```
No additional parameters. Energy and all coefficients are zero.

#### Stop Frame (energy_idx == 0xF)
```
[4 bits: energy_idx = 15]
```
Signals end of utterance. The decoder continues for 2 additional frames (with all targets zeroed) to allow the lattice filter to drain, then stops.

#### Data Frame (energy_idx == 1..14)
```
[4 bits: energy_idx]
[1 bit:  repeat flag]
[5 or 6 bits: pitch_idx]     (5 bits if pitch_count=32, 6 bits if pitch_count=64)
```

If repeat == 0 (new coefficients):
```
[5 bits: k0_idx]              (32 levels)
[5 bits: k1_idx]              (32 levels)
[4 bits: k2_idx]              (16 levels)
[4 bits: k3_idx]              (16 levels)
```

If pitch != 0 (voiced frame), additionally:
```
[4 bits: k4_idx]              (16 levels)
[4 bits: k5_idx]              (16 levels)
[4 bits: k6_idx]              (16 levels)
[3 bits: k7_idx]              (8 levels)
[3 bits: k8_idx]              (8 levels)
[3 bits: k9_idx]              (8 levels)
```

If pitch == 0 (unvoiced), k4-k9 are implicitly zero.

If repeat == 1, all K coefficients retain their previous values.

### 3.3 Parameter Lookup

Each index is used as an offset into the corresponding chip parameter table:
- `energy = energy_table[energy_idx]`
- `period = pitch_table[pitch_idx]`
- `k[n] = kn_table[kn_idx]`

K coefficient values in the tables are pre-multiplied by 512. During synthesis they are divided by 512.0 to get the actual reflection coefficient.

---

## 4. LPC Synthesis Engine

### 4.1 Frame Timing

- **LPC sample rate**: 8000 Hz (fixed, this is the native chip rate)
- **Frame duration**: 25 ms (0.025 seconds)
- **Samples per frame**: 200 (8000 × 0.025)
- **Interpolation granularity**: 8 steps per frame (every 25 samples)

### 4.2 Excitation Signal Generation

#### Voiced Excitation (period > 0)

A chirp waveform from the chip's chirp table, repeated at the pitch period:

```python
period_counter = 0  # counts up each sample

if period_counter < 41:  # only first 41 samples of chirp are used
    # chirp values are uint8_t, treat as int8_t for signed waveform
    u10 = (int8_t(chirp[period_counter]) / 256.0) * (cur_energy / 256.0)
else:
    u10 = 0.0

if period_counter >= cur_period - 1:
    period_counter = 0  # restart chirp
else:
    period_counter += 1
```

**Critical detail**: The chirp table values are stored as `uint8_t` but must be interpreted as `int8_t` (signed) when computing the excitation. The cast `(int8_t*)chirp_0280)[period_cnt]` in the C code performs this reinterpretation.

#### Unvoiced Excitation (period == 0)

A pseudo-random noise generator using an LFSR (Linear Feedback Shift Register):

```python
synth_rand = (synth_rand >> 1) ^ (0xB800 if (synth_rand & 1) else 0)
noise = cur_energy if (synth_rand & 1) else -cur_energy
u10 = noise / 2048.0
```

The LFSR is initialized to 1 at the start of each utterance for deterministic output.

### 4.3 Interpolation

Parameters are interpolated 8 times per frame using a shift-based scheme. The interpolation shifts are: `[0, 3, 3, 3, 2, 2, 1, 1]`.

At each interpolation step:
```python
cur_param = cur_param + ((tgt_param - cur_param) >> shift)
```

**Interpolation skip conditions** (parameters jump directly to target):
- Interpolation is disabled globally
- Transition from voiced to unvoiced or vice versa
- Transition from silence to non-silence or vice versa

**Note on implementation quirk**: After computing the interpolation, the code overwrites `cur_*` with `from_*` when `use_interp` is true. This effectively means the interpolation calculation is overridden and the "from" values are used for the actual synthesis. This appears to be intentional behavior matching the original chip's frame-boundary parameter update.

### 4.4 Lattice Filter (10th-order)

The lattice filter models the vocal tract as a series of coupled acoustic tubes. It uses 10 reflection coefficients (k0-k9) and maintains 10 delay states (x0-x9).

**Forward path** (excitation → output):
```python
mkfract = 512.0  # K coefficient scaling factor

u9  = u10 - (cur_k9 / mkfract) * x9
u8  = u9  - (cur_k8 / mkfract) * x8
u7  = u8  - (cur_k7 / mkfract) * x7
u6  = u7  - (cur_k6 / mkfract) * x6
u5  = u6  - (cur_k5 / mkfract) * x5
u4  = u5  - (cur_k4 / mkfract) * x4
u3  = u4  - (cur_k3 / mkfract) * x3
u2  = u3  - (cur_k2 / mkfract) * x2
u1  = u2  - (cur_k1 / mkfract) * x1
u0  = u1  - (cur_k0 / mkfract) * x0

# Clamp output
u0 = max(-1.0, min(1.0, u0))
```

**Reverse path** (update delay states):
```python
x9 = x8 + (cur_k8 / mkfract) * u8
x8 = x7 + (cur_k7 / mkfract) * u7
x7 = x6 + (cur_k6 / mkfract) * u6
x6 = x5 + (cur_k5 / mkfract) * u5
x5 = x4 + (cur_k4 / mkfract) * u4
x4 = x3 + (cur_k3 / mkfract) * u3
x3 = x2 + (cur_k2 / mkfract) * u2
x2 = x1 + (cur_k1 / mkfract) * u1
x1 = x0 + (cur_k0 / mkfract) * u0
x0 = u0
```

All delay states (x0-x9) are initialized to 0 at the start of each utterance.

**Post-processing**: The output `u0` is multiplied by 1.5 as a gain stage before being stored in the 8kHz sample buffer.

### 4.5 Render Loop Termination

The render loop terminates when:
1. A stop frame (energy_idx == 0xF) is encountered, plus 2 drain frames
2. The infinite loop guard fires: frame_cnt >= 200 with energy_idx=0, energy=0, period=0, repeat=0, ending_cnt=-1

---

## 5. Sample Rate Conversion

### 5.1 Algorithm

The synthesizer produces samples at 8000 Hz. If the output sample rate differs, a windowed-sinc resampler (QDSS algorithm by Ronald H. Nicholson Jr.) converts the rate.

**Core concept**: For each output sample position, a FIR filter (windowed sinc) is centered at the corresponding position in the input signal and convolved:

```python
ratio = lpc_srate / output_srate
gain = 2.0 * fmax / output_srate

for each output sample i:
    x = i * ratio  # position in input buffer
    y = 0.0
    for j in range(-window_width//2, window_width//2):
        idx = int(x) + j
        if 0 <= idx < input_length:
            # von Hann window
            w = 0.5 - 0.5 * cos(2π * (0.5 + (idx - x) / window_width))
            # Sinc function
            a = 2π * (idx - x) * fmax / output_srate
            sinc = sin(a) / a if a != 0 else 1.0
            y += gain * w * sinc * input[idx]
    output[i] = y
```

### 5.2 Filter Parameters

| Condition | Nyquist (fmax) | Window Width |
|-----------|---------------|--------------|
| Upsampling (output > 8kHz) | output_srate × 0.55 | 64 |
| Downsampling (output ≤ 8kHz) | output_srate × 0.375 | 256 |

---

## 6. Audio Output

### 6.1 Channel Routing

The output audio is routed according to `output=` and `ch=`:

| output= | ch= | Behavior |
|---------|-----|----------|
| stereo (default) | not set (default) | Audio on both L and R channels |
| stereo | left | Audio on L, silence on R |
| stereo | right | Silence on L, audio on R |
| mono | (ignored) | Single channel output |

**Silence** is represented as amplitude 0.0 (floating point) which becomes sample value 0 in the integer output.

### 6.2 Gain

The audio signal is scaled by `au_aud_gain / 100.0` (default 75%) before writing.

### 6.3 WAV File Format

Standard RIFF WAV with PCM format tag (1):

| Field | 16-bit mode | 8-bit mode |
|-------|------------|------------|
| Format tag | 1 (PCM) | 1 (PCM) |
| Bits per sample | 16 | 8 |
| Block align | channels × 2 | channels × 1 |
| Sample encoding | Signed 16-bit LE | Unsigned 8-bit (center 128) |
| Peak value for scaling | 32767 | 127 |

### 6.4 Format Auto-Detection

Output format is determined by the `wav=` filename extension:
- `.wav` → WAV PCM
- `.au` or `.snd` → Sun/AU (encoding 3 = 16-bit PCM)
- `.aiff` or `.aif` → AIFF
- `.raw` or `.pcm` → Raw PCM
- (default) → WAV PCM

---

## 7. Command Line Interface

### 7.1 Argument Processing

Arguments are concatenated with `,` as delimiter into a single string. Parameters are extracted using a `find(key)` then `find(delimiter)` approach:
- Most parameters use `,` as value delimiter
- `str=` and `strhex=` use `.` as delimiter (because their values contain commas)
- `-help` and `--help` are detected by simple string search (no value extraction)

### 7.2 Complete Parameter Reference

| Parameter | Delimiter | Default | Description |
|-----------|----------|---------|-------------|
| mode= | , | (required) | Operating mode: render, romlist, rendaddrfileseq, rendstrfileseq, cleanbrace, cleanquote |
| chip= | , | tms5100.txt | Chip definition file (or name without ext if USE_TMS5K_H) |
| str= | . | — | Decimal CSV LPC data |
| strhex= | . | — | Hex CSV LPC data |
| strbin= | , | — | Binary LPC file path |
| strfile= | , | — | Decimal CSV LPC text file |
| strhexfile= | , | — | Hex CSV LPC text file |
| addr= | , | — | ROM hex address |
| rom0= | , | — | Primary ROM file |
| rom1= | , | — | Secondary ROM file |
| wav= | , | zzzout.wav | Output audio file |
| srate= | , | 8000 | Output sample rate (4000-192000) |
| swidth= | , | 16 | Bits per sample (8 or 16) |
| output= | , | st | Channel mode: st/stereo or mo/mono |
| ch= | , | (both) | Channel: left/l/0 or right/r/1 |
| gain= | , | 75 | Audio gain % (0-300) |
| filt= | , | on | Lattice filter: off to disable |
| verb= | , | off | Verbose output: on to enable |
| fnamein= | , | — | Input file for file-based modes |
| fnameout= | , | zzzaddr_list.txt | Output file for romlist |
| line= | , | — | Line index for file-based modes |
| step= | , | 1 | Step direction for sequential modes |

---

## 8. Security Considerations for Python Implementation

### 8.1 Input Validation

- **All string inputs** must be validated character-by-character before processing
- **File sizes** must be bounded (1MB for text files, 32KB for binary LPC, 16KB per ROM)
- **Integer overflows**: The original C code uses `int16_t` for K coefficients and `uint8_t` for energy/period. Python's arbitrary precision integers eliminate overflow but the values should still be clamped to their expected ranges.
- **Buffer boundaries**: The bitstream reader must not read past the end of the input data
- **Frame count guard**: Limit to a reasonable maximum (200 frames of silence = 5 seconds, configurable)

### 8.2 File I/O

- Validate file paths; reject path traversal attempts
- Use binary mode for LPC and ROM files, text mode for CSV files
- Bound all memory allocations based on file size limits

### 8.3 Numeric Safety

- The lattice filter can produce values outside [-1.0, 1.0]; clamping is essential
- The sinc resampler divides by potentially-zero values; guard `sin(a)/a` with `a != 0` check
- NaN/Inf propagation: consider adding checks after filter stages

---

## 9. Architecture Recommendation for Python

### 9.1 Suggested Module Structure

```
tms5xxx/
├── __init__.py
├── cli.py              # Command-line argument parsing
├── chip_params.py      # Chip parameter tables and loading
├── bitstream.py        # LPC bitstream reader with bit reversal
├── frame_decoder.py    # Frame parsing (energy, pitch, coefficients)
├── synthesizer.py      # Lattice filter, excitation, interpolation
├── resampler.py        # Windowed-sinc sample rate converter
├── audio_output.py     # WAV/AU/AIFF/raw file writers
├── input_loader.py     # Input format handlers (binary, CSV, ROM)
├── validators.py       # Input validation functions
└── chips/
    ├── tms5100.py      # Built-in TMS5100 parameters
    ├── tms5110.py      # Built-in TMS5110 parameters
    ├── tms5200.py      # Built-in TMS5200 parameters
    └── tms5220.py      # Built-in TMS5220 parameters
```

### 9.2 Key Design Principles

1. **No mutable global state**: Use classes with explicit state
2. **Immutable chip parameters**: Load once, pass by reference
3. **Streaming architecture**: Process frame-by-frame, don't buffer entire utterance in memory
4. **Type safety**: Use `dataclasses` or `NamedTuple` for structured data
5. **Testability**: Each module should be independently testable with known reference vectors

---

*End of Part 1. Part 2 will cover: detailed pseudocode for each module, the complete chip parameter tables for all four chips, reference test vectors, and edge cases.*



---


# TMS5xxx LPC Speech Synthesizer Decoder — Clean-Room Specification

## Part 2: Detailed Pseudocode, Chip Tables, Edge Cases, and Auxiliary Modes

---

## 10. Detailed Module Pseudocode

### 10.1 Bitstream Reader

```python
class BitstreamReader:
    def __init__(self, data: bytes, reverse_bits: bool = True):
        self.data = data
        self.byte_pos = 0
        self.bit_pos = 0       # 0-7 within current byte
        self.reverse = reverse_bits
        self.bytes_consumed = 1  # starts at 1 (initial byte counted)

    @staticmethod
    def reverse_byte(b: int) -> int:
        """Reverse all 8 bits of a byte."""
        b = ((b >> 4) | (b << 4)) & 0xFF
        b = (((b & 0xCC) >> 2) | ((b & 0x33) << 2)) & 0xFF
        b = (((b & 0xAA) >> 1) | ((b & 0x55) << 1)) & 0xFF
        return b

    def _get_byte(self, offset: int) -> int:
        """Get byte at current position + offset, with optional bit reversal."""
        idx = self.byte_pos + offset
        if idx >= len(self.data):
            return 0  # read beyond end returns 0
        b = self.data[idx]
        return self.reverse_byte(b) if self.reverse else b

    def get_bits(self, count: int) -> int:
        """Extract 'count' bits (1-8) from the stream."""
        data16 = self._get_byte(0) << 8
        if self.bit_pos + count > 8:
            data16 |= self._get_byte(1)

        data16 <<= self.bit_pos
        data16 &= 0xFFFF
        value = data16 >> (16 - count)

        self.bit_pos += count
        if self.bit_pos >= 8:
            self.bit_pos -= 8
            self.byte_pos += 1
            self.bytes_consumed += 1

        return value
```

### 10.2 Frame Decoder

```python
@dataclass
class LPCFrame:
    energy_idx: int = 0
    energy: int = 0
    period: int = 0
    repeat: bool = False
    k: list = field(default_factory=lambda: [0] * 10)
    is_silence: bool = False
    is_stop: bool = False

def decode_frame(reader: BitstreamReader, chip: ChipParams,
                 prev_k: list) -> LPCFrame:
    """Decode one LPC frame from the bitstream."""
    frame = LPCFrame()

    frame.energy_idx = reader.get_bits(4)

    if frame.energy_idx == 0:
        frame.is_silence = True
        frame.energy = 0
        return frame

    if frame.energy_idx == 0xF:
        frame.is_stop = True
        return frame

    frame.energy = chip.energy[frame.energy_idx]
    frame.repeat = bool(reader.get_bits(1))

    pitch_bits = 6 if chip.pitch_count == 64 else 5
    pitch_idx = reader.get_bits(pitch_bits)
    frame.period = chip.pitch[pitch_idx]

    if frame.repeat:
        frame.k = list(prev_k)  # copy previous coefficients
        return frame

    # Read K coefficients
    # k0, k1: 5 bits each (32 levels)
    frame.k[0] = chip.k0[reader.get_bits(5)]
    frame.k[1] = chip.k1[reader.get_bits(5)]
    # k2, k3: 4 bits each (16 levels)
    frame.k[2] = chip.k2[reader.get_bits(4)]
    frame.k[3] = chip.k3[reader.get_bits(4)]

    if frame.period != 0:  # voiced: read k4-k9
        frame.k[4] = chip.k4[reader.get_bits(4)]   # 16 levels
        frame.k[5] = chip.k5[reader.get_bits(4)]   # 16 levels
        frame.k[6] = chip.k6[reader.get_bits(4)]   # 16 levels
        frame.k[7] = chip.k7[reader.get_bits(3)]   # 8 levels
        frame.k[8] = chip.k8[reader.get_bits(3)]   # 8 levels
        frame.k[9] = chip.k9[reader.get_bits(3)]   # 8 levels
    else:  # unvoiced: k4-k9 are zero
        for i in range(4, 10):
            frame.k[i] = 0

    return frame
```

### 10.3 LPC Synthesizer

```python
class LPCSynthesizer:
    LPC_SAMPLE_RATE = 8000
    FRAME_TIME = 0.025          # 25 ms
    SAMPLES_PER_FRAME = 200     # 8000 * 0.025
    INTERP_STEPS = 8
    INTERP_SHIFTS = [0, 3, 3, 3, 2, 2, 1, 1]
    K_SCALE = 512.0
    OUTPUT_GAIN = 1.5

    def __init__(self):
        self.x = [0.0] * 10    # lattice filter delay states
        self.synth_rand = 1     # LFSR seed
        self.period_counter = 0

    def synthesize(self, data: bytes, chip: ChipParams,
                   max_frames: int = 200) -> list[float]:
        """Decode LPC bitstream and return 8kHz float samples."""
        reader = BitstreamReader(data, reverse_bits=True)
        samples = []
        prev_k = [0] * 10
        cur_energy = 0
        cur_period = 0
        cur_k = [0] * 10
        last_voiced = False
        last_silence = False
        ending_countdown = -1
        frame_count = 0

        self.x = [0.0] * 10
        self.synth_rand = 1
        self.period_counter = 0

        while True:
            # Decode frame (skip if in ending drain)
            if ending_countdown < 0:
                frame = decode_frame(reader, chip, prev_k)
            else:
                frame = LPCFrame(is_silence=True)

            # Handle stop frame
            if frame.is_stop and ending_countdown < 0:
                ending_countdown = 2
                frame = LPCFrame(is_silence=True)

            # Determine from/target values
            from_energy = cur_energy
            from_period = cur_period
            from_k = list(cur_k)
            tgt_energy = frame.energy
            tgt_period = frame.period
            tgt_k = list(frame.k)

            # First frame: snap to target
            if frame_count == 0:
                cur_energy = tgt_energy
                cur_period = tgt_period
                cur_k = list(tgt_k)
                from_energy = cur_energy
                from_period = cur_period
                from_k = list(cur_k)

            # Interpolation skip logic
            now_voiced = cur_period != 0
            now_silence = cur_energy == 0
            skip_interp = False
            if (now_voiced != last_voiced) or (now_silence != last_silence):
                skip_interp = True

            # Infinite loop guard
            if (frame_count >= max_frames and frame.energy_idx == 0
                    and tgt_energy == 0 and tgt_period == 0
                    and not frame.repeat and ending_countdown == -1):
                break

            # Generate samples for this frame
            interp_idx = 0
            for s in range(self.SAMPLES_PER_FRAME):
                # Interpolation update (8 times per frame, not on first sample)
                if s > 0 and (s % (self.SAMPLES_PER_FRAME // self.INTERP_STEPS)) == 0:
                    if not skip_interp:
                        shift = self.INTERP_SHIFTS[interp_idx]
                        # Note: interpolation is computed then overridden with from_ values
                        # This matches original chip behavior
                    interp_idx += 1
                    if interp_idx >= self.INTERP_STEPS:
                        interp_idx = 0

                # Use from_ values for synthesis (matching original behavior)
                e = from_energy
                p = from_period
                k = from_k

                # Generate excitation
                u10 = self._excitation(e, p, chip.chirp)

                # Lattice filter
                sample = self._lattice_filter(u10, k)
                samples.append(sample * self.OUTPUT_GAIN)

            # Update state for next frame
            last_voiced = cur_period != 0
            last_silence = cur_energy == 0
            cur_energy = tgt_energy
            cur_period = tgt_period
            cur_k = list(tgt_k)
            prev_k = list(tgt_k)
            frame_count += 1

            # Ending drain
            if ending_countdown > 0:
                ending_countdown -= 1
                if ending_countdown == 0:
                    break

        return samples

    def _excitation(self, energy: int, period: int,
                    chirp: list[int]) -> float:
        """Generate one excitation sample."""
        if period > 0:
            # Voiced: chirp waveform
            if self.period_counter < 41:
                # Interpret chirp byte as signed int8
                raw = chirp[self.period_counter]
                signed_val = raw if raw < 128 else raw - 256
                u10 = (signed_val / 256.0) * (energy / 256.0)
            else:
                u10 = 0.0

            if self.period_counter >= period - 1:
                self.period_counter = 0
            else:
                self.period_counter += 1
        else:
            # Unvoiced: LFSR white noise
            self.synth_rand = ((self.synth_rand >> 1)
                               ^ (0xB800 if (self.synth_rand & 1) else 0))
            self.synth_rand &= 0xFFFF
            noise = energy if (self.synth_rand & 1) else -energy
            u10 = noise / 2048.0

        return u10

    def _lattice_filter(self, u10: float, k: list[int]) -> float:
        """10th-order lattice filter. K values are pre-scaled by 512."""
        s = self.K_SCALE
        x = self.x

        # Forward path
        u = [0.0] * 10
        u[9] = u10    - (k[9] / s) * x[9]
        u[8] = u[9]   - (k[8] / s) * x[8]
        u[7] = u[8]   - (k[7] / s) * x[7]
        u[6] = u[7]   - (k[6] / s) * x[6]
        u[5] = u[6]   - (k[5] / s) * x[5]
        u[4] = u[5]   - (k[4] / s) * x[4]
        u[3] = u[4]   - (k[3] / s) * x[3]
        u[2] = u[3]   - (k[2] / s) * x[2]
        u[1] = u[2]   - (k[1] / s) * x[1]
        u[0] = u[1]   - (k[0] / s) * x[0]

        # Clamp
        u[0] = max(-1.0, min(1.0, u[0]))

        # Reverse path (update delays)
        x[9] = x[8] + (k[8] / s) * u[8]
        x[8] = x[7] + (k[7] / s) * u[7]
        x[7] = x[6] + (k[6] / s) * u[6]
        x[6] = x[5] + (k[5] / s) * u[5]
        x[5] = x[4] + (k[4] / s) * u[4]
        x[4] = x[3] + (k[3] / s) * u[3]
        x[3] = x[2] + (k[2] / s) * u[2]
        x[2] = x[1] + (k[1] / s) * u[1]
        x[1] = x[0] + (k[0] / s) * u[0]
        x[0] = u[0]

        return u[0]
```

### 10.4 Sample Rate Converter

```python
import math

def resample_qdss(input_buf: list[float], input_rate: float,
                  output_rate: float) -> list[float]:
    """Windowed-sinc sample rate converter (QDSS algorithm)."""
    if output_rate == input_rate:
        return list(input_buf)

    ratio = input_rate / output_rate

    if output_rate > input_rate:  # upsampling
        fmax = output_rate * 0.55
        window_width = 64
    else:  # downsampling
        fmax = output_rate * 0.375
        window_width = 256

    output_count = int(len(input_buf) / ratio)
    gain = 2.0 * fmax / output_rate
    two_pi = 2.0 * math.pi
    output = []

    x = 0.0
    for _ in range(output_count):
        y = 0.0
        half_w = window_width // 2
        for i in range(-half_w, half_w):
            j = int(x) + i
            if 0 <= j < len(input_buf):
                # von Hann window
                w = 0.5 - 0.5 * math.cos(two_pi * (0.5 + (j - x) / window_width))
                # Sinc
                a = two_pi * (j - x) * fmax / output_rate
                sinc = math.sin(a) / a if a != 0.0 else 1.0
                y += gain * w * sinc * input_buf[j]
        output.append(y)
        x += ratio

    return output
```

### 10.5 WAV Writer

```python
import struct

def write_wav(filename: str, samples_ch0: list[float], samples_ch1: list[float] | None,
              sample_rate: int, bits_per_sample: int, channels: int):
    """Write a WAV file. Samples are floats in range [-1.0, 1.0]."""
    bytes_per_sample = bits_per_sample // 8
    if bits_per_sample == 8:
        peak = 127
    else:
        peak = 32767

    # Interleave channels
    if channels == 2 and samples_ch1 is not None:
        data_samples = []
        for i in range(len(samples_ch0)):
            data_samples.append(samples_ch0[i])
            data_samples.append(samples_ch1[i] if i < len(samples_ch1) else 0.0)
    else:
        data_samples = samples_ch0

    total_samples = len(data_samples)
    data_size = total_samples * bytes_per_sample
    block_align = channels * bytes_per_sample
    avg_bytes_per_sec = sample_rate * block_align

    with open(filename, 'wb') as f:
        # RIFF header
        f.write(b'RIFF')
        f.write(struct.pack('<I', data_size + 36))
        f.write(b'WAVE')
        # fmt chunk
        f.write(b'fmt ')
        f.write(struct.pack('<I', 16))             # chunk size
        f.write(struct.pack('<H', 1))              # PCM format
        f.write(struct.pack('<H', channels))
        f.write(struct.pack('<I', sample_rate))
        f.write(struct.pack('<I', avg_bytes_per_sec))
        f.write(struct.pack('<H', block_align))
        f.write(struct.pack('<H', bits_per_sample))
        # data chunk
        f.write(b'data')
        f.write(struct.pack('<I', data_size))

        for sample in data_samples:
            int_val = int(round(sample * peak))
            if bits_per_sample == 8:
                # 8-bit WAV: unsigned, center at 128
                val = max(0, min(255, int_val + 128))
                f.write(struct.pack('B', val))
            else:
                # 16-bit WAV: signed little-endian
                val = max(-32768, min(32767, int_val))
                f.write(struct.pack('<h', val))
```

---

## 11. Complete Chip Parameter Tables

### 11.1 Table Dimensions per Chip

| Table | TMS5100 | TMS5110 | TMS5200 | TMS5220 |
|-------|---------|---------|---------|---------|
| Chirp | 50 uint8 | 32 uint8 | 52 uint8 | 52 uint8 |
| Energy | 16 int16 | 16 int16 | 16 int16 | 16 int16 |
| Pitch count | 32 (5-bit) | 32 (5-bit) | 64 (6-bit) | 64 (6-bit) |
| k0 | 32 int16 | 32 int16 | 32 int16 | 32 int16 |
| k1 | 32 int16 | 32 int16 | 32 int16 | 32 int16 |
| k2-k6 | 16 int16 each | 16 int16 each | 16 int16 each | 16 int16 each |
| k7-k9 | 8 int16 each | 8 int16 each | 8 int16 each | 8 int16 each |

### 11.2 Reference

All table values are defined in the chip definition text files (tms5100.txt through tms5220.txt) and in tms5k.h. The authoritative reference for these values is the MAME project:
`https://github.com/mamedev/mame/blob/master/src/devices/sound/tms5110r.hxx`

The Python implementation should load these from its own data files or embedded dictionaries. The values from the .txt files are the canonical source — the tms5k.h file is auto-generated from them.

---

## 12. Auxiliary Operating Modes

### 12.1 ROM List Mode (`mode=romlist`)

Reads a VSM ROM file and extracts a word address list. The ROM has a specific memory layout:

1. **Bytes 0x00-0x03**: Word counts for 4 word lists
2. **Bytes 0x04-0x0B**: Pointers (16-bit LE) to 4 word list start addresses
3. **Bytes 0x0C-0x43**: 26 letter pointers (A-Z), 16-bit LE each
4. **Following**: Beep pointer, digit 0-9 pointers, "10" pointer
5. **Following**: Phrase pointers (correct, right, wrong, spell, etc.) — some use single indirection (pointer → LPC data), some use double indirection (pointer → pointer → LPC data)
6. **Word lists**: Each word entry contains ASCII characters (6-bit, +0x41 offset) with bit 6 as end-of-word flag, followed by 16-bit LE LPC address

Output format: one line per entry, `hex_address word_text`

### 12.2 Sequential Address File Render (`mode=rendaddrfileseq`)

Reads a text file of `address word` lines (produced by romlist mode), renders the LPC data at the hex address on a specific line. Maintains a line index in `zzzline_index.txt` that auto-increments (or decrements with `step=`).

### 12.3 Sequential String File Render (`mode=rendstrfileseq`)

Same concept as rendaddrfileseq but the file contains hex LPC strings (one per line). Each line is passed to the hex string renderer.

### 12.4 File Cleanup Modes

- **`mode=cleanbrace`**: Reads a C source file, extracts content between `{` and `}` on each line, strips `0x` prefixes, spaces, and CRs. Useful for extracting LPC byte arrays from Arduino/C code.
- **`mode=cleanquote`**: Same but extracts between `"` and `"`.

---

## 13. Edge Cases and Boundary Conditions

### 13.1 Bitstream Reader

- **Reading past end of data**: Returns 0 for bytes beyond the buffer — the synthesizer will hit silence/stop frames or the frame guard
- **Bit alignment**: Bits span byte boundaries seamlessly; the 16-bit sliding window handles cross-byte extraction
- **Empty input**: Frame decoder sees energy_idx=0 (silence) immediately

### 13.2 Frame Decoding

- **Repeat frame on first frame**: Coefficients are all zero (no previous values)
- **Unvoiced frame**: k4-k9 are forced to zero even if previous frame had values
- **Stop frame**: Only sets ending countdown; 2 additional frames are rendered with zeroed targets to drain the lattice filter
- **energy_idx=0 (silence)**: No bits are read beyond the 4-bit energy index

### 13.3 Synthesis

- **Period counter wrap**: Resets when `period_counter >= cur_period - 1`, not when it reaches the chirp table size. Chirp values beyond index 40 are zero.
- **Chirp signed interpretation**: The chirp table stores unsigned uint8 values. They MUST be interpreted as signed int8 for correct excitation waveform. Value 0xD4 (212) becomes -44 as int8.
- **LFSR determinism**: The noise LFSR is reset to 1 at the start of each utterance, ensuring bit-exact reproducibility
- **Filter drain**: After a stop frame, 2 frames of silence are synthesized to allow the 10 IIR delay states to settle toward zero

### 13.4 Sample Rate Conversion

- **Identity case**: If output rate equals 8000 Hz, no resampling is performed — samples are used directly
- **Extreme ratios**: Upsampling to 48kHz (6:1) uses shorter FIR (64 taps); downsampling to 4kHz (1:2) uses longer FIR (256 taps) for better anti-aliasing
- **Buffer access**: The windowed-sinc accesses samples by float index; out-of-range indices are skipped (not zero-padded)

### 13.5 Audio Output

- **8-bit WAV encoding**: Unsigned with center at 128. A float sample of 0.0 maps to byte value 128. A float sample of +1.0 maps to 255 (127 + 128). A float of -1.0 maps to 1 (-127 + 128).
- **Gain of 0**: Produces silence (all zeros) — valid use case
- **Mono with ch=right**: ch= parameter is ignored, audio goes to ch0 with a printed note

---

## 14. Reference Test Vector

### 14.1 File: 0220_Affirmative.lpc (TMS5220)

- **File size**: 223 bytes
- **First 16 bytes hex**: `A5 4F 7A D3 3C 5A 8F AE C8 A9 70 ED BD BA 2A 3B`
- **Chip**: tms5220.txt
- **Total frames decoded**: 35 (33 data + 2 drain)
- **Stop frame at**: frame 33 (energy_idx = 0xF)
- **Total 8kHz samples**: 7000 (35 × 200)
- **Duration**: 0.875 seconds

Expected output file properties:
- **Stereo 16-bit 8kHz WAV**: 28044 bytes (44 header + 7000 × 2ch × 2bytes)
- **Mono 16-bit 8kHz WAV**: 14044 bytes (44 header + 7000 × 1ch × 2bytes)
- **Mono 8-bit 8kHz WAV**: 7044 bytes (44 header + 7000 × 1ch × 1byte)

First 8 stereo samples (16-bit signed, L then R):
```
-5, -5, 46, 46, 318, 318, 845, 845
```

First 4 mono samples (16-bit signed):
```
-5, 46, 318, 845
```

---

## 15. License Considerations

The original C code is GPL v2+. This specification documents the **functional behavior** of the TMS5xxx LPC decoding algorithm, which is based on publicly documented Texas Instruments chip architecture from the late 1970s. The core algorithm (lattice filter, LPC frame format, excitation generation) is well-documented in:

- TI patent literature and data sheets
- The MAME project (BSD-licensed implementation of the same algorithm)
- Multiple open-source Talkie/TMS5220 implementations

A clean-room Python implementation written from this specification, without copying any GPL source code, can be released under any license of your choosing. The chip parameter tables (energy, pitch, K coefficient values) are factual hardware data, not copyrightable expression.

The QDSS resampler algorithm is separately documented by Ronald H. Nicholson Jr. under a BSD-style license and can be reimplemented independently.

---

## 16. Implementation Checklist

- [ ] BitstreamReader with bit reversal and cross-byte extraction
- [ ] Frame decoder supporting silence, stop, voiced, unvoiced, repeat frames
- [ ] Chip parameter loader (from text files and/or embedded data)
- [ ] LPC synthesizer with chirp excitation, LFSR noise, and 10th-order lattice filter
- [ ] Frame interpolation with shift-based scheme
- [ ] Infinite loop guard (configurable max frame count)
- [ ] Windowed-sinc sample rate converter
- [ ] WAV writer (8-bit unsigned and 16-bit signed, mono and stereo)
- [ ] Channel routing (both/left/right/mono)
- [ ] Input loaders (binary file, decimal CSV, hex CSV, text file decimal, text file hex)
- [ ] Input validators (character whitelist checking)
- [ ] Command-line argument parser
- [ ] ROM loader and word address list extractor
- [ ] File cleanup utilities (brace/quote extraction)
- [ ] Sequential file rendering with persistent line index
- [ ] Output format auto-detection from file extension
- [ ] Verbose/debug output mode
- [ ] Gain control
- [ ] Lattice filter bypass option

---

*This completes the clean-room specification. Both Part 1 and Part 2 together provide sufficient detail to implement a functionally equivalent TMS5xxx LPC decoder in Python without reference to the GPL source code.*

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.

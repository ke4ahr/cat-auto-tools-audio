# wav_normalize.py

Peak-normalize a directory of WAV files to just below digital full-scale.

## Usage

```
python3 wav_normalize.py --input <dir> --output <dir> [--headroom <lsb>]
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--input` | yes | -- | Source directory containing `.wav` files |
| `--output` | yes | -- | Destination directory (created if absent) |
| `--headroom` | no | `67` | Peak target in LSB below 32767 (default: 32700) |

## How It Works

For each `.wav` file in `--input`:

1. Read raw 16-bit signed PCM samples.
2. Find the peak absolute sample value.
3. Compute gain: `target / peak` (default target = 32700).
4. Apply gain to every sample, clamp to `[-32768, 32767]`.
5. Write the normalized file to `--output` with identical WAV headers.
6. Print the filename and gain factor applied.

Files with a peak of zero (silent) are copied as-is and reported as `1.00x`.
Files where the computed gain exceeds 100x are skipped with a warning (near-silent noise floor).

## Output

One line per file:

```
0001_One.wav                              1.25x
0002_Two.wav                              0.83x
0003_Three.wav                            SKIPPED (near-silent, gain >100x)
```

## Notes

- Input files must be 16-bit signed PCM WAV (mono or stereo).
- WAV header parameters (channels, sample rate, sample width) are preserved unchanged.
- No external dependencies -- stdlib only (`wave`, `struct`, `argparse`, `pathlib`).

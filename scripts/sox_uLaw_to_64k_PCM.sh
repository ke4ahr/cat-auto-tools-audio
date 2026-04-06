#!/bin/sh
echo "sox input_ulaw.wav -b 16 -r 8000 -c 1 output_pcm.wav

    -b 16: Sets the output bit depth to 16-bit (recommended for PCM).
    -r 8000 -c 1: Ensures the rate and channel remain compliant.
"

sox input_ulaw.wav -b 16 -r 8000 -c 1 output_pcm.wav

#!/bin/sh
echo "
sox input.wav -r 8000 -c 1 -e u-law output_ulaw.wav

    -r 8000: Sets the sample rate to 8 kHz.
    -c 1: Sets the channel to mono (1).
    -e u-law: Encodes the audio as u-law.
"
sox input.wav -r 8000 -c 1 -e u-law output_ulaw.wav

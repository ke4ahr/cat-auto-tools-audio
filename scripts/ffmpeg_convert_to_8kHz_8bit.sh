#!/bin/sh
echo "ffmpeg -i input.wav -acodec pcm_u8 -ar 8000 -ac 1 output.wav"
ffmpeg -i input.wav -acodec pcm_u8 -ar 8000 -ac 1 output.wav

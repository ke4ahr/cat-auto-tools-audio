#!/bin/bash
echo "mkdir output"
echo "for file in *.wav; do sox \"$file\" -r 8000 -c 1 -e u-law \"./output/$file\"; done"

mkdir output
for file in *.wav; do sox "$file" -r 8000 -c 1 -e u-law "./output/$file"; done

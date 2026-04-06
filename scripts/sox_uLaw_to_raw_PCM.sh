#!/bin/sh
echo "sox -r 8000 -c 1 -e u-law input.ulw -t raw -e signed-integer -b 16 output.raw"
sox -r 8000 -c 1 -e u-law input.ulw -t raw -e signed-integer -b 16 output.raw

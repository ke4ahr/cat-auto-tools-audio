#!/bin/sh
echo "sox -r 8000 -c 1 -e signed-integer -b 8 input.raw -t raw -e u-law output.ulw"
sox -r 8000 -c 1 -e signed-integer -b 8 input.raw -t raw -e u-law output.ulw

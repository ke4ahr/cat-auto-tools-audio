#!/bin/bash
# Copyright (C) 2026 Kris Kirby, KE4AHR
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# cleanup_docs.sh -- Remove LaTeX and pdflatex intermediate files.
#
# Deletes: *.aux *.log *.out *.toc *_compile.log
# Locations: docs/ and docs/diagrams/
# Preserves: *.tex (source), *.pdf (output), *.dot (source), *.svg (output)
#
# Usage:
#   bash cleanup_docs.sh           # dry-run: show what would be deleted
#   bash cleanup_docs.sh --apply   # actually delete

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCS="$SCRIPT_DIR/docs"
DIAGRAMS="$DOCS/diagrams"

DRY_RUN=1
if [ "$1" = "--apply" ]; then
    DRY_RUN=0
fi

if [ "$DRY_RUN" = 1 ]; then
    echo "Dry-run mode -- use --apply to delete files."
fi

PATTERNS="*.aux *.log *.out *.toc"
DELETED=0

for DIR in "$DOCS" "$DIAGRAMS"; do
    for PAT in $PATTERNS; do
        for F in "$DIR"/$PAT; do
            [ -f "$F" ] || continue
            if [ "$DRY_RUN" = 1 ]; then
                echo "  would delete: $F"
            else
                rm "$F"
                echo "  deleted: $F"
            fi
            DELETED=$((DELETED + 1))
        done
    done
done

if [ "$DRY_RUN" = 1 ]; then
    echo "Would delete $DELETED file(s). Run with --apply to proceed."
else
    echo "Deleted $DELETED file(s)."
fi

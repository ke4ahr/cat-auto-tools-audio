#!/bin/bash
# Copyright (C) 2026 Kris Kirby, KE4AHR
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# INSTALL_PyTI.sh -- Install PyTI_LPC_CMD for use with this project.
#
# Behavior depends on whether a Python virtual environment is active:
#
#   venv active ($VIRTUAL_ENV set):
#       Clones the repo into PyTI_LPC_CMD/ then runs:
#           pip install -e PyTI_LPC_CMD/
#       This makes "import pyti_lpc_cmd" work inside the venv without
#       any sys.path manipulation.
#
#   no venv:
#       Clones the repo into PyTI_LPC_CMD/ (subdirectory of this project).
#       cat-1000_lpc_export.py and extract_310dx.py find it automatically
#       via the sys.path search added to each script.
#
# Usage:
#   bash INSTALL_PyTI.sh          # install / first-time setup
#   bash INSTALL_PyTI.sh --update # pull latest and re-install into venv

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_URL="https://github.com/ke4ahr/PyTI_LPC_CMD"
DEST="$SCRIPT_DIR/PyTI_LPC_CMD"

# Detect active venv
if [ -n "$VIRTUAL_ENV" ]; then
    USE_VENV=1
    PIP="$VIRTUAL_ENV/bin/pip"
    echo "Active venv: $VIRTUAL_ENV"
else
    USE_VENV=0
fi

# --update: pull latest, re-install into venv if active
if [ "$1" = "--update" ]; then
    if [ ! -d "$DEST/.git" ]; then
        echo "error: $DEST is not a git repository; run without --update to clone first"
        exit 1
    fi
    echo "Updating PyTI_LPC_CMD..."
    git -C "$DEST" pull
    if [ "$USE_VENV" = 1 ]; then
        echo "Re-installing into venv..."
        "$PIP" install -e "$DEST"
    fi
    echo "Done."
    exit 0
fi

# Clone if not already present
if [ -d "$DEST" ]; then
    echo "PyTI_LPC_CMD already present at $DEST"
    echo "To update: bash INSTALL_PyTI.sh --update"
else
    echo "Cloning $REPO_URL into $DEST ..."
    git clone "$REPO_URL" "$DEST"
    echo "Cloned."
fi

# Install into venv if active
if [ "$USE_VENV" = 1 ]; then
    echo "Installing PyTI_LPC_CMD into venv ($VIRTUAL_ENV)..."
    "$PIP" install -e "$DEST"
    echo "Done. 'import pyti_lpc_cmd' is available inside the venv."
else
    echo "Done. No active venv -- scripts will locate PyTI_LPC_CMD via sys.path."
    echo "To use a venv: activate it first, then re-run this script."
fi

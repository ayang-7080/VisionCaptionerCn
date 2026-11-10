#!/bin/bash
set -e

# 1. Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# 2. Check the project interpreter without relying on activation paths
if [ ! -x "venv/bin/python" ]; then
    echo "Virtual environment not found in $SCRIPT_DIR/venv"
    echo "Please ensure you have created the venv (see the README for your OS)."
    exit 1
fi

echo "Launching GUI..."

# 3. Start the python GUI script
exec "venv/bin/python" main.py "$@"

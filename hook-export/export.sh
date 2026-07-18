#!/bin/bash
# Wrapper script to run the parallel python export script
exec python3 "$(dirname "$0")/export.py" "$@"

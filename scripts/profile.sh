#!/usr/bin/env bash
# Profile a running Streamlit app from outside — zero code changes, no app dependency.
#
# Prereq: `uv tool install py-spy` (one-time, global).
#
# Usage:
#   scripts/profile.sh top              # live top-like view
#   scripts/profile.sh record [seconds] # flame graph; defaults to 30s, then opens flame.svg
#
# Streamlit normally runs as: <python> -m streamlit run main.py
# We pick the streamlit-script-runner subprocess (highest-RSS python child).

set -euo pipefail

mode="${1:-top}"
duration="${2:-30}"

pid=$(pgrep -f "streamlit run" | head -n1 || true)
if [[ -z "$pid" ]]; then
    echo "No 'streamlit run' process found. Start the app first."
    exit 1
fi
echo "Attaching to streamlit pid=$pid"

case "$mode" in
    top)
        py-spy top --pid "$pid" --subprocesses
        ;;
    record)
        out="flame-$(date +%Y%m%d-%H%M%S).svg"
        echo "Recording for ${duration}s → $out"
        py-spy record -o "$out" --pid "$pid" --subprocesses --rate 200 --duration "$duration"
        echo "Done. Opening $out…"
        open "$out" 2>/dev/null || xdg-open "$out" 2>/dev/null || echo "Open $out manually."
        ;;
    *)
        echo "Usage: $0 {top|record [seconds]}"
        exit 1
        ;;
esac

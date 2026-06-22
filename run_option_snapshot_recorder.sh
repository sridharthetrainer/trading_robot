#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/Desktop/trading_robot}"
PYTHON="$BOT_DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$BOT_DIR/venv/bin/python3"
fi
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

cd "$BOT_DIR"
exec "$PYTHON" option_chain_recorder.py \
    --loop \
    --interval-sec "${OPTION_CHAIN_SNAPSHOT_INTERVAL_SEC:-300}" \
    --underlyings "${SNAPSHOT_OPTION_UNDERLYINGS:-NIFTY,BANKNIFTY,FINNIFTY,SENSEX}"

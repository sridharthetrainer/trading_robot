#!/usr/bin/env bash
# Compatibility wrapper. The maintained fresh-install path is INSTALL.sh.
set -euo pipefail
cd "$(dirname "$0")"
exec bash ./INSTALL.sh "$@"

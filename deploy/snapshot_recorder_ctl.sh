#!/usr/bin/env bash
# One-command control for the standalone snapshot recorder (+ its watchdog)
# and the R001 forward-report timer. Unit sources: deploy/systemd/.
#
#   deploy/snapshot_recorder_ctl.sh enable           # install + start recorder and watchdog
#   deploy/snapshot_recorder_ctl.sh disable          # stop + disable recorder and watchdog
#   deploy/snapshot_recorder_ctl.sh status
#   deploy/snapshot_recorder_ctl.sh forward-enable   # weekdays 16:00 R001 forward report
#   deploy/snapshot_recorder_ctl.sh forward-disable
#   deploy/snapshot_recorder_ctl.sh brief-enable     # weekdays 08:50 morning research brief
#   deploy/snapshot_recorder_ctl.sh brief-disable
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
install_units() {
  mkdir -p "$UNIT_DIR" "$REPO/standalone_recorder" "$REPO/cpr_vwap_ema_nifty/logs"
  cp "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.timer "$UNIT_DIR"/
  systemctl --user daemon-reload
}
case "${1:-status}" in
  enable)          install_units; systemctl --user enable --now option-snapshot-recorder.service option-snapshot-watchdog.timer ;;
  disable)         systemctl --user disable --now option-snapshot-recorder.service option-snapshot-watchdog.timer ;;
  forward-enable)  install_units; systemctl --user enable --now cpr-forward-report.timer ;;
  forward-disable) systemctl --user disable --now cpr-forward-report.timer ;;
  brief-enable)    install_units; systemctl --user enable --now research-brief.timer ;;
  brief-disable)   systemctl --user disable --now research-brief.timer ;;
  status)
    systemctl --user --no-pager status option-snapshot-recorder.service | head -5 || true
    systemctl --user list-timers --no-pager option-snapshot-watchdog.timer cpr-forward-report.timer || true
    "$REPO/.venv/bin/python3" "$REPO/standalone_snapshot_recorder.py" --status ;;
  *) echo "usage: $0 enable|disable|status|forward-enable|forward-disable|brief-enable|brief-disable"; exit 2 ;;
esac

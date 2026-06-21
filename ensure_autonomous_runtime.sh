#!/usr/bin/env bash
# ensure_autonomous_runtime.sh
# Install/start the full autonomous runtime with user systemd units plus cron
# fallback.  No sudo is required; if linger cannot be enabled, @reboot cron still
# starts the runtime when the user session is created.
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_SYSTEMD_DIR="${HOME}/.config/systemd/user"
PYTHON="${BOT_DIR}/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="${BOT_DIR}/venv/bin/python3"
fi

mkdir -p "$USER_SYSTEMD_DIR"

install_cron_fallback() {
    tmp="$(mktemp)"
    crontab -l 2>/dev/null | grep -v "${BOT_DIR}/bot.sh start" | grep -v "${BOT_DIR}/bot_watchdog.sh" > "$tmp" || true
    {
        cat "$tmp"
        echo "@reboot ${BOT_DIR}/bot.sh start >/dev/null 2>&1"
        echo "* * * * * ${BOT_DIR}/bot_watchdog.sh >/dev/null 2>&1"
    } | crontab -
    rm -f "$tmp"
}

system_runtime_ready=true
for svc in trading-bot.service trading-bot-watchdog.service manual-tracker.service auto-deploy.service; do
    if ! systemctl is-enabled "$svc" >/dev/null 2>&1; then
        system_runtime_ready=false
    fi
done

if [ "$system_runtime_ready" = true ]; then
    # A root/system install already owns the runtime.  Do not create duplicate
    # user services; keep cron fallback and use bot.sh to fill any gaps.
    systemctl --user disable --now trading-bot.service manual-tracker.service trading-bot-watchdog.service auto-deploy.service daily-pipeline.timer post-market-ml.timer >/dev/null 2>&1 || true
    rm -f \
        "${USER_SYSTEMD_DIR}/trading-bot.service" \
        "${USER_SYSTEMD_DIR}/manual-tracker.service" \
        "${USER_SYSTEMD_DIR}/trading-bot-watchdog.service" \
        "${USER_SYSTEMD_DIR}/auto-deploy.service" \
        "${USER_SYSTEMD_DIR}/daily-pipeline.service" \
        "${USER_SYSTEMD_DIR}/daily-pipeline.timer" \
        "${USER_SYSTEMD_DIR}/post-market-ml.service" \
        "${USER_SYSTEMD_DIR}/post-market-ml.timer"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    systemctl --user reset-failed trading-bot.service manual-tracker.service trading-bot-watchdog.service auto-deploy.service >/dev/null 2>&1 || true
    install_cron_fallback
    chmod +x "${BOT_DIR}/bot.sh" "${BOT_DIR}/bot_watchdog.sh" "${BOT_DIR}/run_option_snapshot_recorder.sh"
    "${BOT_DIR}/bot.sh" start
    "${BOT_DIR}/bot.sh" status
    exit 0
fi

write_unit() {
    local name="$1"
    local body="$2"
    printf "%s\n" "$body" > "${USER_SYSTEMD_DIR}/${name}"
}

write_unit "trading-bot.service" "[Unit]
Description=Trading Robot Main Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
EnvironmentFile=-${BOT_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStartPre=-/usr/bin/pkill -f python.*main_autonomous.py
ExecStart=${PYTHON} ${BOT_DIR}/main_autonomous.py
Restart=always
RestartSec=10
TimeoutStartSec=120

[Install]
WantedBy=default.target"

write_unit "manual-tracker.service" "[Unit]
Description=Trading Robot Manual Trade Tracker
After=network-online.target trading-bot.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
EnvironmentFile=-${BOT_DIR}/.env
Environment=PYTHONUNBUFFERED=1
Environment=MANUAL_AUTO_PROTECT=true
Environment=MANUAL_POLL_INTERVAL_SEC=30
Environment=MANUAL_UPDATE_INTERVAL_SEC=900
ExecStartPre=-/usr/bin/pkill -f python.*manual_trade_tracker.py
ExecStart=${PYTHON} ${BOT_DIR}/manual_trade_tracker.py
Restart=always
RestartSec=30

[Install]
WantedBy=default.target"

write_unit "trading-bot-watchdog.service" "[Unit]
Description=Trading Robot Smart Watchdog
After=trading-bot.service

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
EnvironmentFile=-${BOT_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStartPre=-/usr/bin/pkill -f python.*watchdog.py
ExecStart=${PYTHON} ${BOT_DIR}/watchdog.py
Restart=always
RestartSec=30

[Install]
WantedBy=default.target"

write_unit "auto-deploy.service" "[Unit]
Description=Trading Robot Auto Deploy Watcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
EnvironmentFile=-${BOT_DIR}/.env
Environment=PYTHONUNBUFFERED=1
ExecStartPre=-/usr/bin/pkill -f python.*auto_deploy_watcher.py
ExecStart=${PYTHON} ${BOT_DIR}/auto_deploy_watcher.py
Restart=always
RestartSec=30

[Install]
WantedBy=default.target"

write_unit "daily-pipeline.service" "[Unit]
Description=Trading Robot Daily Data Pipeline

[Service]
Type=oneshot
WorkingDirectory=${BOT_DIR}
EnvironmentFile=-${BOT_DIR}/.env
ExecStart=${PYTHON} ${BOT_DIR}/daily_pipeline.py --telegram"

write_unit "daily-pipeline.timer" "[Unit]
Description=Run daily data pipeline after market close

[Timer]
OnCalendar=Mon..Fri 15:45:00 Asia/Kolkata
AccuracySec=300
Persistent=true

[Install]
WantedBy=timers.target"

write_unit "post-market-ml.service" "[Unit]
Description=Trading Robot Post Market ML

[Service]
Type=oneshot
WorkingDirectory=${BOT_DIR}
EnvironmentFile=-${BOT_DIR}/.env
ExecStart=${PYTHON} ${BOT_DIR}/post_market_ml.py --days 15 --candle-days 15"

write_unit "post-market-ml.timer" "[Unit]
Description=Run post-market ML after market close

[Timer]
OnCalendar=Mon..Fri 16:30:00 Asia/Kolkata
AccuracySec=300
Persistent=true

[Install]
WantedBy=timers.target"

systemctl --user daemon-reload || true
systemctl --user enable --now trading-bot.service manual-tracker.service trading-bot-watchdog.service auto-deploy.service || true
systemctl --user enable --now daily-pipeline.timer post-market-ml.timer || true

# Linger lets user services start at boot before graphical login. It may be
# blocked by policy; cron fallback below still covers normal user-session boots.
loginctl enable-linger "$USER" 2>/dev/null || true

install_cron_fallback

chmod +x "${BOT_DIR}/bot.sh" "${BOT_DIR}/bot_watchdog.sh" "${BOT_DIR}/run_option_snapshot_recorder.sh"

"${BOT_DIR}/bot.sh" start
"${BOT_DIR}/bot.sh" status

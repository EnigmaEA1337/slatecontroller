#!/bin/sh
# ram-mitigation.sh — daily restart of memory-leaking daemons.
#
# The Slate 7 Pro (1 GB RAM, OpenWrt 21.02 + GL.iNet 4.x) has been
# observed to leak ~30-40 MB/day across tailscaled + AdGuardHome. After
# ~3-4 days uninterrupted the device OOMs and freezes — physical reboot
# required. This script pre-empts that by restarting both daemons once
# per day at a quiet hour (controlled by the cron entry installed by the
# controller's deploy_agent).
#
# Design constraints :
#   - Idempotent : safe to invoke twice in a row
#   - Fail-soft : a missing service just gets a log line, no error exit
#   - Doesn't kill its own cron context : cron runs detached
#   - logger -t slate-ctrl-ram makes lines greppable in `logread`
#
# Manual invocation : `/etc/slate-controller/scripts/ram-mitigation.sh`
# Cron entry        : installed by controller in /etc/crontabs/root
# Log destination   : /tmp/slate-ctrl-ram.log (tmpfs — wipes on reboot,
#                     which is fine because logread keeps syslog history)

log() {
  # Dual sink : syslog (logread) for persistence across `logread -e`,
  # plus stdout for the tmpfs log file when invoked via cron redirect.
  logger -t slate-ctrl-ram "$*" 2>/dev/null
  echo "$(date '+%Y-%m-%dT%H:%M:%S') $*"
}

before_free=$(awk '/MemAvailable/ {print $2/1024 " MB"}' /proc/meminfo 2>/dev/null)
log "start (MemAvailable=$before_free)"

# --- AdGuardHome -----------------------------------------------------
# ~187 MB RSS baseline with 5+ blocklists, grows ~5-10 MB/day from
# cache + stats accumulation.
if [ -x /etc/init.d/adguardhome ]; then
  if /etc/init.d/adguardhome enabled 2>/dev/null; then
    log "restarting adguardhome"
    if /etc/init.d/adguardhome restart 2>/dev/null; then
      log "adguardhome restart OK"
    else
      log "adguardhome restart FAILED"
    fi
    # Give AdGuard time to re-bind :3053 before tailscale touches the
    # network layer. Without this, DNS resolution can hang for ~10s.
    sleep 5
  else
    log "adguardhome service disabled, skipping"
  fi
else
  log "adguardhome init script absent, skipping"
fi

# --- Tailscale -------------------------------------------------------
# tailscaled is the heavier leaker — ~82 MB RSS baseline, +20-30 MB/day.
# Restart drops MagicDNS + active connections briefly (~5-10s of
# downtime) ; controller's URL resolver falls back to LAN during that
# window so admin access doesn't break.
if [ -x /etc/init.d/tailscale ]; then
  if /etc/init.d/tailscale enabled 2>/dev/null; then
    log "restarting tailscale"
    if /etc/init.d/tailscale restart 2>/dev/null; then
      log "tailscale restart OK"
    else
      log "tailscale restart FAILED"
    fi
  else
    log "tailscale service disabled, skipping"
  fi
else
  log "tailscale init script absent, skipping"
fi

after_free=$(awk '/MemAvailable/ {print $2/1024 " MB"}' /proc/meminfo 2>/dev/null)
log "done (MemAvailable=$after_free)"

#!/bin/sh
# wifi-drift-watchdog.sh — local cron-driven reconciler.
#
# Why it exists : the MTK driver + GL.iNet stock LCD interactions
# (travel-router upstream toggles, mode changes via the touch panel,
# Repeater Mode add/remove) silently drop our managed wifi-iface VAPs
# even though UCI still says ``disabled=0``. The controller only
# discovers the drift on the next operator-driven re-apply. This script
# closes that loop locally on the Slate : every couple of minutes it
# compares the canonical UCI state with the live ``ip link`` flags, and
# when they disagree it kicks ``slate-ctrl apply-only wifi`` to put
# things back where the operator intended.
#
# Drift definitions :
#   - UCI ``disabled=0`` AND netdev IFF_UP cleared  → SSID should be UP
#     but isn't → reconcile.
#   - UCI ``disabled=1`` AND netdev IFF_UP set      → driver kept the
#     beacon armed after a config disable → reconcile (mwctl no_bcn=1
#     gets re-applied by the handler).
#
# Lock-aware : if ``/etc/slate-controller/.apply.lock`` exists, an apply
# is already in flight — we skip this tick to avoid concurrent fw3/uci
# operations that would cancel each other.

set -eu
export PATH=/usr/sbin:/sbin:/usr/bin:/bin:$PATH

LOCK_FILE=/etc/slate-controller/.apply.lock
STATE_DIR=/etc/slate-controller
LOG_FILE=$STATE_DIR/wifi-watchdog.log

# Cap the log file at 64 KB so we don't fill /etc/ on a long uptime —
# truncate (keep the tail) before each write.
_log() {
  if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 65536 ] ; then
    tail -c 32768 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
  fi
  printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$1" >> "$LOG_FILE"
}

# Bail out if an apply is in flight — concurrent ip-link toggles would
# race the handler.
if [ -e "$LOCK_FILE" ] ; then
  exit 0
fi

# Walk every managed wifi-iface section and check UCI-vs-link agreement.
# `SC_WL_*` = our per-(slug, band) slots ; `wlanmld5g`/`wlanmld6g` =
# MLO per-band links ; `mld0` is the MLO group (no netdev, skip).
drift_seen=""
for sec in $(uci -q show wireless 2>/dev/null \
             | grep "=wifi-iface\$" \
             | sed 's/^wireless\.\([^=]*\)=.*$/\1/') ; do
  case "$sec" in
    SC_WL_*|wlanmld5g|wlanmld6g) ;;
    *) continue ;;
  esac
  disabled=$(uci -q get "wireless.$sec.disabled" 2>/dev/null || echo "0")
  ifn=$(uci -q get "wireless.$sec.ifname" 2>/dev/null || echo "")
  [ -z "$ifn" ] && continue
  flags=$(cat "/sys/class/net/$ifn/flags" 2>/dev/null || echo "")
  [ -z "$flags" ] && continue
  # 0x1 = IFF_UP.
  is_up=$(( flags & 1 ))
  if [ "$disabled" = "0" ] && [ "$is_up" = "0" ] ; then
    drift_seen="$drift_seen $sec/$ifn[want=UP,got=DOWN]"
  elif [ "$disabled" = "1" ] && [ "$is_up" = "1" ] ; then
    drift_seen="$drift_seen $sec/$ifn[want=DOWN,got=UP]"
  fi
done

if [ -z "$drift_seen" ] ; then
  # All-clean tick — silent (logging every 2 min would balloon the file).
  exit 0
fi

_log "drift:$drift_seen — running apply-only wifi"
if /usr/local/bin/slate-ctrl apply-only wifi >> "$LOG_FILE" 2>&1 ; then
  _log "reconcile rc=0"
else
  _log "reconcile rc=$?"
fi

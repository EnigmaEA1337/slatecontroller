#!/bin/sh
# wifi-drift-watchdog.sh — local cron-driven reconciler.
#
# Why it exists : the MTK driver + GL.iNet stock LCD interactions
# (travel-router upstream toggles, mode changes via the touch panel,
# Repeater Mode add/remove) silently drop our managed wifi-iface VAPs
# even though UCI still says ``disabled=0`` — and, more commonly, keep
# beaconing zombie SSIDs on the air after ``disabled=1`` because the
# stock ``mwctl <iface> set no_bcn 1`` flip is a no-op on non-MLO VAPs
# on this firmware (returns rc=0 but the chip keeps the beacon armed).
# The controller only discovers the drift on the next operator-driven
# re-apply. This script closes the loop locally on the Slate.
#
# Detection (driver-level, via iwinfo + uci) :
#   - iwinfo "Mode: Master" + ssid set → driver is currently beaconing
#     that VAP, regardless of /sys/class/net/<ifname>/flags. That's the
#     ground truth ; netdev IFF_UP can be cleared and beacons can still
#     fly.
#   - UCI ``disabled`` field is the desired state.
#   - Drift case A (zombie beacon) : UCI says disabled=1 but iwinfo
#     reports Mode=Master → KILL with ``iw dev <ifname> del`` (the only
#     thing that actually shuts the beacon down on this driver, until
#     the next ``wifi`` reload recreates the VAP at boot).
#   - Drift case B (dropped VAP) : UCI says disabled=0 but iwinfo
#     reports the iface as missing (or non-Master) → run
#     ``slate-ctrl apply-only wifi`` to re-provision.
#
# Lock-aware : if ``/etc/slate-controller/.apply.lock`` exists, an apply
# is already in flight — we skip this tick to avoid concurrent fw3/uci
# operations that would cancel each other. STALE LOCK PROTECTION : if
# the lock is older than 10 min, consider it abandoned and reclaim it
# (an apply hanging that long means slate-ctrl crashed before unlocking
# and we'd otherwise sit silent forever — observed on the live Slate
# after a 2026-06-09 LCD-driven apply that died mid-flight).

set -eu
export PATH=/usr/sbin:/sbin:/usr/bin:/bin:$PATH

LOCK_FILE=/etc/slate-controller/.apply.lock
STATE_DIR=/etc/slate-controller
LOG_FILE=$STATE_DIR/wifi-watchdog.log

_log() {
  if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 65536 ] ; then
    tail -c 32768 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
  fi
  printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$1" >> "$LOG_FILE"
}

# Reclaim a stale lock (> 10 min = 600 s). An apply that took longer
# than that is either crashed or stuck — in both cases the safer move
# is to clear the lock and reconcile, because the alternative is silent
# drift forever.
if [ -e "$LOCK_FILE" ] ; then
  lock_age=$(( $(date +%s) - $(date -r "$LOCK_FILE" +%s 2>/dev/null || echo 0) ))
  if [ "$lock_age" -gt 600 ] ; then
    _log "stale lock (age=${lock_age}s) — reclaiming"
    rm -f "$LOCK_FILE"
  else
    exit 0
  fi
fi

# Build a quick map of "ifname → 1" for every iface currently beaconing
# in Master mode according to iwinfo. iwinfo output looks like :
#   ra1       ESSID: "TRON_LEGACY"
#             Access Point: ...
#             Mode: Master  Channel: 8 ...
# Each iface BLOCK starts with an iface-leading line that already
# contains the ESSID inline (same line, not the next). "Mode: Master"
# arrives a few lines later. We track per-block state and flush at the
# next iface header (and once more at END).
beaconing=$(iwinfo 2>/dev/null | awk '
  /^[a-z][a-z0-9]*[[:space:]]+ESSID:/ {
    if (iface != "" && is_master) print iface
    iface = $1; is_master = 0; next
  }
  /Mode: Master/ { is_master = 1 }
  END { if (iface != "" && is_master) print iface }
' | sort -u)

# Build the desired-state map from UCI : for each managed wifi-iface
# section, ``ifname`` + ``disabled``. SC_WL_* = our per-(slug, band)
# slots ; wlanmld5g/6g = MLO per-band links ; mld0 = MLO group (no
# netdev — skip).
drift_zombie=""    # disabled=1 but iface is beaconing → iw dev del
drift_dropped=""   # disabled=0 but iface absent/non-Master → reapply

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
  is_beaconing=0
  if printf '%s\n' "$beaconing" | grep -qx "$ifn" ; then
    is_beaconing=1
  fi
  if [ "$disabled" = "1" ] && [ "$is_beaconing" = "1" ] ; then
    drift_zombie="$drift_zombie $sec/$ifn"
  elif [ "$disabled" = "0" ] && [ "$is_beaconing" = "0" ] ; then
    drift_dropped="$drift_dropped $sec/$ifn"
  fi
done

# Nothing to do — silent (logging an all-clean every 2 min would balloon
# the file in a hurry).
if [ -z "$drift_zombie" ] && [ -z "$drift_dropped" ] ; then
  exit 0
fi

# Zombie beacons : kill them directly. ``iw dev <ifname> del`` is the
# only thing that actually stops the chip from beaconing on this firmware
# (mwctl no_bcn is silently ignored on non-MLO VAPs). The next ``wifi``
# reload at reboot will recreate the VAP from UCI in the correct state
# (disabled=1 honored properly on cold boot).
if [ -n "$drift_zombie" ] ; then
  _log "zombie-beacon:$drift_zombie — running iw dev del"
  for pair in $drift_zombie ; do
    ifn=${pair#*/}
    if iw dev "$ifn" del >> "$LOG_FILE" 2>&1 ; then
      _log "  killed $ifn"
    else
      _log "  iw del $ifn rc=$?"
    fi
  done
fi

# Dropped VAPs : something tore down what UCI says should be up. Run
# the full apply path so the handler re-creates the VAP cleanly (right
# slot, right network, right hostapd).
if [ -n "$drift_dropped" ] ; then
  _log "dropped-vap:$drift_dropped — running apply-only wifi"
  if /usr/local/bin/slate-ctrl apply-only wifi >> "$LOG_FILE" 2>&1 ; then
    _log "reconcile rc=0"
  else
    _log "reconcile rc=$?"
  fi
fi

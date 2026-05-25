#!/bin/sh
# cycle-profile.sh — invoked by the reset button on a short press (< 3s).
#
# UX = "select-then-commit" :
#   - Each press advances a cursor + shows a menu frame on the touch
#     panel (highlighting the slot the cursor points at).
#   - Pressing again within COMMIT_DELAY seconds CANCELS the pending
#     commit and advances the cursor one more slot.
#   - When the cursor stops moving for COMMIT_DELAY seconds, the slot
#     at the cursor is executed (slate-ctrl apply OR cycle-action-*.sh).
#
# This file runs 100% on the Slate — no controller round-trip.
# Files involved (all under /etc/slate-controller) :
#   cycle.json              ordered list pushed by the controller
#   state/cycle_cursor      current cursor index (-1 when idle)
#   state/cycle_commit_pid  PID of the pending-commit timer, if any
#   menus/cycle_<N>.raw     pre-rendered menu frame for cursor=N

ROOT="/etc/slate-controller"
CYCLE_FILE="$ROOT/cycle.json"
STATE_DIR="$ROOT/state"
CURSOR_FILE="$STATE_DIR/cycle_cursor"
COMMIT_PID_FILE="$STATE_DIR/cycle_commit_pid"
MENUS_DIR="$ROOT/menus"
SCRIPTS_DIR="$ROOT/scripts"
LOG_TAG="slate-ctrl-cycle"

# Seconds the user has to keep pressing before we commit. 3s feels
# natural — quick enough that idle time after a deliberate press isn't
# frustrating, long enough that double-clicks don't fire two commits.
COMMIT_DELAY=3

log() {
  logger -t "$LOG_TAG" "$*"
  echo "$(date '+%Y-%m-%dT%H:%M:%S') $*"
}

# Bail fast if no config — first install / empty cycle.
if [ ! -f "$CYCLE_FILE" ]; then
  log "no cycle config at $CYCLE_FILE — button press ignored"
  exit 0
fi

total=$(jsonfilter -i "$CYCLE_FILE" -e '@.steps[*].kind' 2>/dev/null | wc -l)
if [ "$total" -le 0 ]; then
  log "cycle is empty — button press ignored"
  exit 0
fi

mkdir -p "$STATE_DIR" "$MENUS_DIR"

# ── Screen-lock gate ─────────────────────────────────────────────
# If the user has set up a PIN on the touchscreen (ENABLE_PASSCODE=1),
# we refuse the cycle whenever the display is OFF or dimmed — that's
# the panel's "auto-locked / sleeping" state. The OEM reset behaviors
# (reset_network at 3-7s, factory_reset at 8s+) still fire from the
# parent /etc/rc.button/reset, so the button isn't dead — only the
# short-press cycle is gated.
#
# Heuristic, not perfect : the panel being awake (bl_power=0 + max
# brightness) could mean either "user just touched to wake, on PIN
# entry" or "PIN already entered, on home screen". GL.iNet doesn't
# expose the running lock state via ubus or any state file we found
# (probed live in this session). The narrow attack window — user must
# be physically present, with PIN-entry on screen, within AUTO_LOCK_TIME
# of waking — and the PIN still gating every config change in the web
# UI, makes this trade-off acceptable for V1.
GL_CONFIG=/tmp/gl_screen/active_config
BL_POWER_FILE=/sys/class/backlight/backlight/bl_power
BL_BRIGHTNESS_FILE=/sys/class/backlight/backlight/actual_brightness
BL_MAX_FILE=/sys/class/backlight/backlight/max_brightness

if [ -f "$GL_CONFIG" ]; then
  enabled=$(awk '/^ENABLE_PASSCODE/ {print $2; exit}' "$GL_CONFIG" 2>/dev/null)
  if [ "$enabled" = "1" ]; then
    bl_power=$(cat "$BL_POWER_FILE" 2>/dev/null)
    brightness=$(cat "$BL_BRIGHTNESS_FILE" 2>/dev/null)
    max_brightness=$(cat "$BL_MAX_FILE" 2>/dev/null)
    case "$bl_power" in ''|*[!0-9]*) bl_power=0 ;; esac
    case "$brightness" in ''|*[!0-9]*) brightness=0 ;; esac
    case "$max_brightness" in ''|*[!0-9]*) max_brightness=120 ;; esac
    # 50% of max is our "awake" threshold. Below that the panel is
    # transitioning towards lock or already off.
    threshold=$(( max_brightness / 2 ))
    if [ "$bl_power" != "0" ] || [ "$brightness" -lt "$threshold" ]; then
      log "screen locked (bl_power=$bl_power brightness=$brightness/$max_brightness) — short-press ignored"
      # Force cursor back to idle so the next press (post-unlock)
      # starts cleanly at slot 0. Also drop any pending commit timer
      # that might have been scheduled before the screen went to sleep.
      echo -1 > "$CURSOR_FILE"
      if [ -f "$COMMIT_PID_FILE" ]; then
        stale=$(cat "$COMMIT_PID_FILE" 2>/dev/null)
        [ -n "$stale" ] && kill "$stale" 2>/dev/null
        rm -f "$COMMIT_PID_FILE"
      fi
      exit 0
    fi
  fi
fi

# Cancel any pending commit timer. Re-pressing the button restarts the
# countdown — that's the whole point of the select-then-commit design.
if [ -f "$COMMIT_PID_FILE" ]; then
  pending=$(cat "$COMMIT_PID_FILE" 2>/dev/null)
  if [ -n "$pending" ] && kill -0 "$pending" 2>/dev/null; then
    kill "$pending" 2>/dev/null
    log "cancelled pending commit (pid=$pending)"
  fi
  rm -f "$COMMIT_PID_FILE"
fi

# Read current cursor (idle = -1) and advance it.
cursor=$(cat "$CURSOR_FILE" 2>/dev/null | head -1)
case "$cursor" in
  ''|*[!0-9-]*) cursor=-1 ;;
esac
[ "$cursor" -lt 0 ] && cursor=-1

if [ "$cursor" -lt 0 ]; then
  next=0
else
  next=$(( (cursor + 1) % total ))
fi
echo "$next" > "$CURSOR_FILE"

# Read what's at the cursor — useful for log + future "preview only the
# upcoming step name on screen" features. Indexing is 0-based.
preview_kind=$(jsonfilter -i "$CYCLE_FILE" -e "@.steps[$next].kind" 2>/dev/null)
preview_name=$(jsonfilter -i "$CYCLE_FILE" -e "@.steps[$next].name" 2>/dev/null)
log "cursor → $next ($preview_kind '$preview_name'); committing in ${COMMIT_DELAY}s"

# Paint the menu frame for this cursor position. The controller
# pre-renders one frame per cursor and pushes it during sync. If the
# expected frame is missing, fall back to silent log + still schedule
# commit — the cycle still works, the user just doesn't see the menu.
frame="$MENUS_DIR/cycle_$next.raw"
if [ -f "$frame" ]; then
  # Use the same fb takeover loop the loading screens use : kill
  # gl_screen briefly + write our raw, then let gl_screen come back.
  # We don't hold here — the commit timer below will let the user keep
  # pressing for COMMIT_DELAY seconds. After commit, slate-ctrl apply
  # will paint the loading screen and finally the new wallpaper.
  (
    /etc/init.d/gl_screen stop >/dev/null 2>&1
    killall -9 gl_screen >/dev/null 2>&1
    # Brief paint loop : ~1.5s of frames so the user sees it stick
    # against any procd respawn race. Then we exit and let the next
    # press (or the commit) handle the next paint.
    end=$(( $(date +%s) + 2 ))
    while [ "$(date +%s)" -lt "$end" ]; do
      cat "$frame" > /dev/fb0 2>/dev/null
      usleep 120000
      killall -9 gl_screen >/dev/null 2>&1
    done
    /etc/init.d/gl_screen start >/dev/null 2>&1
  ) &
  log "menu frame painted ($frame)"
else
  log "no pre-rendered frame for cursor=$next ($frame missing)"
fi

# Schedule the commit. Run in the background so the button handler
# returns quickly. PID is recorded so the next press can cancel it.
(
  sleep "$COMMIT_DELAY"
  # Read the cursor at FIRING time, not at scheduling time — the user
  # may have pressed more times since we were scheduled, and the latest
  # press's timer is the one we want. (Strictly speaking, if we got
  # here, no later press fired, otherwise we'd have been killed. But
  # this guard cheaply protects against races.)
  fire=$(cat "$CURSOR_FILE" 2>/dev/null | head -1)
  case "$fire" in
    ''|*[!0-9-]*) fire=-1 ;;
  esac
  if [ "$fire" -lt 0 ]; then
    logger -t "$LOG_TAG" "commit aborted (cursor idle)"
    rm -f "$COMMIT_PID_FILE"
    exit 0
  fi

  kind=$(jsonfilter -i "$CYCLE_FILE" -e "@.steps[$fire].kind" 2>/dev/null)
  name=$(jsonfilter -i "$CYCLE_FILE" -e "@.steps[$fire].name" 2>/dev/null)
  if [ -z "$kind" ] || [ -z "$name" ]; then
    logger -t "$LOG_TAG" "commit aborted (step $fire malformed)"
    echo -1 > "$CURSOR_FILE"
    rm -f "$COMMIT_PID_FILE"
    exit 0
  fi

  logger -t "$LOG_TAG" "committing step #$fire : $kind '$name'"
  case "$kind" in
    profile)
      # No-op if the target profile is already active. Skipping the
      # full slate-ctrl apply (which would reload firewall, restart
      # wifi, re-paint screen…) when nothing would actually change.
      # `state/active` is written by slate-ctrl itself on each apply,
      # so it's the canonical "current profile" marker.
      cur=$(cat /etc/slate-controller/state/active 2>/dev/null | head -1)
      if [ "$cur" = "$name" ]; then
        logger -t "$LOG_TAG" "profile '$name' already active — skip apply"
      elif [ -x /usr/local/bin/slate-ctrl ]; then
        /usr/local/bin/slate-ctrl apply "$name" >>/tmp/slate-ctrl-cycle.log 2>&1
      else
        logger -t "$LOG_TAG" "slate-ctrl missing — cannot apply '$name'"
      fi
      ;;
    action)
      handler="$SCRIPTS_DIR/cycle-action-$name.sh"
      if [ -x "$handler" ]; then
        "$handler" >>/tmp/slate-ctrl-cycle.log 2>&1
      else
        logger -t "$LOG_TAG" "unknown action '$name' (handler $handler missing)"
      fi
      ;;
    *)
      logger -t "$LOG_TAG" "unknown kind '$kind' — skipped"
      ;;
  esac

  # Back to idle so the next press starts at slot 0 again.
  echo -1 > "$CURSOR_FILE"
  rm -f "$COMMIT_PID_FILE"
) &
echo $! > "$COMMIT_PID_FILE"

exit 0

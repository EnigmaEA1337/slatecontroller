#!/bin/sh
# cycle-profile.sh — invoked by the reset button on a short press (< 3s).
#
# Runs 100% on the Slate :
#   1. Read /etc/slate-controller/cycle.json (pushed by the controller)
#   2. Read /etc/slate-controller/state/cycle_index (our position last time)
#   3. Advance to the next step (wrap at end)
#   4. Dispatch :
#        - kind=profile → /usr/local/bin/slate-ctrl apply <name>
#        - kind=action  → /etc/slate-controller/scripts/cycle-action-<name>.sh
#
# NO controller round-trip. The user pressing the button while travelling
# / on a flaky network / with the controller down still cycles correctly
# because everything is local.
#
# State file (state/cycle_index) is just an integer line. Created on
# first press if missing. Reset to 0 when the cycle list shrinks below
# the stored index.

ROOT="/etc/slate-controller"
CYCLE_FILE="$ROOT/cycle.json"
STATE_DIR="$ROOT/state"
INDEX_FILE="$STATE_DIR/cycle_index"
SCRIPTS_DIR="$ROOT/scripts"
LOG_TAG="slate-ctrl-cycle"

log() {
  logger -t "$LOG_TAG" "$*"
  echo "$(date '+%Y-%m-%dT%H:%M:%S') $*"
}

# Bail fast if config missing — first install of the agent + no UI edit
# yet. Log to syslog so the user can see "I pressed but nothing happened"
# explained in logread.
if [ ! -f "$CYCLE_FILE" ]; then
  log "no cycle config at $CYCLE_FILE — button press is a no-op"
  exit 0
fi

# Count the steps. jsonfilter -e '@.steps[*].kind' returns one line per
# step ; wc -l counts them. Empty list → exit cleanly.
total=$(jsonfilter -i "$CYCLE_FILE" -e '@.steps[*].kind' 2>/dev/null | wc -l)
if [ "$total" -le 0 ]; then
  log "cycle is empty — button press is a no-op"
  exit 0
fi

mkdir -p "$STATE_DIR"
last=$(cat "$INDEX_FILE" 2>/dev/null | head -1)
case "$last" in
  ''|*[!0-9]*) last=-1 ;;
esac

next=$(( (last + 1) % total ))
echo "$next" > "$INDEX_FILE"

# Pull this step's kind + name. jsonfilter array indexing is 0-based,
# so we use `next` directly as the index (no +1 offset).
kind=$(jsonfilter -i "$CYCLE_FILE" -e "@.steps[$next].kind" 2>/dev/null)
name=$(jsonfilter -i "$CYCLE_FILE" -e "@.steps[$next].name" 2>/dev/null)

if [ -z "$kind" ] || [ -z "$name" ]; then
  log "step #$next malformed (kind='$kind' name='$name') — skipping"
  exit 0
fi

log "step #$next : $kind '$name'"

case "$kind" in
  profile)
    if [ -x /usr/local/bin/slate-ctrl ]; then
      # Backgrounded so the button handler returns quick (the apply chain
      # can take ~3s with screen + handlers).
      /usr/local/bin/slate-ctrl apply "$name" >>/tmp/slate-ctrl-cycle.log 2>&1 &
      log "dispatched: slate-ctrl apply $name (bg)"
    else
      log "slate-ctrl missing — cannot apply profile '$name'"
    fi
    ;;
  action)
    handler="$SCRIPTS_DIR/cycle-action-$name.sh"
    if [ -x "$handler" ]; then
      "$handler" >>/tmp/slate-ctrl-cycle.log 2>&1 &
      log "dispatched: $handler (bg)"
    else
      log "unknown action '$name' (no handler at $handler) — skipped"
    fi
    ;;
  *)
    log "unknown step kind '$kind' — skipped"
    ;;
esac

exit 0

#!/bin/sh
# cycle-action-update.sh — the "update from controller" cycle step.
#
# V1 (today) : drops a marker file and shows a brief screen toast. We
# don't yet have a Slate→Controller pull mechanism (would require an
# auth flow we haven't built), so this slot is informational : the user
# sees "Update requested" on the panel and the controller can poll
# /etc/slate-controller/state/needs_update via SSH if it wants to react.
#
# Future iterations can fully implement this : push a controller URL +
# bearer token at deploy time, then curl `<controller>/api/agent/sync-pull`
# from here. Kept as a separate handler file so the cycle dispatcher
# doesn't need to change when we wire that up.

LOG_TAG="slate-ctrl-cycle-update"
STATE_DIR="/etc/slate-controller/state"
MARKER="$STATE_DIR/needs_update"

log() {
  logger -t "$LOG_TAG" "$*"
  echo "$(date '+%Y-%m-%dT%H:%M:%S') $*"
}

mkdir -p "$STATE_DIR"
echo "$(date '+%Y-%m-%dT%H:%M:%S')" > "$MARKER"
log "marker dropped at $MARKER — controller can detect via SSH"

# Best-effort screen toast. The handler/screen.sh path already knows how
# to do this for profile activations ; we lean on the same display
# helper if present.
if [ -x /usr/local/bin/slate-message ]; then
  /usr/local/bin/slate-message "Update requested" "Sync from controller" >/dev/null 2>&1 &
fi

exit 0

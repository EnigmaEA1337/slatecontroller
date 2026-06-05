#!/bin/sh
# slate-ctrl-touchscreen-watcher : push gl_screen state changes to the controller.
#
# Polls /etc/gl_screen/status every 2s — on-device file reads are ~µs,
# vastly cheaper than the controller doing a 60s SSH round-trip. Only
# pushes when the parsed (errors, exceed) tuple differs from the last
# observation, so an idle Slate generates zero network traffic.
#
# Falls back to a 30s heartbeat push when the state hasn't changed so
# the controller knows the link is alive (and can pick up state that
# was missed if the controller was down at the moment of an event).
#
# Run as a procd service — see /etc/init.d/slate-ctrl-touchscreen-watcher.

set -u

STATUS_FILE="/etc/gl_screen/status"
EVENT_PUSH="/usr/local/bin/slate-ctrl-event-push"

LAST_ERRORS=""
LAST_EXCEED=""
LAST_HEARTBEAT=0

# Wait for the status file to exist on first boot — gl_screen creates
# it lazily after the touchscreen wakes up.
i=0
while [ ! -f "$STATUS_FILE" ]; do
    i=$((i + 1))
    if [ "$i" -gt 120 ]; then
        logger -t slate-ctrl-touchscreen-watcher "status file never appeared"
        exit 1
    fi
    sleep 1
done

logger -t slate-ctrl-touchscreen-watcher "started, watching $STATUS_FILE"

while :; do
    ERRORS=0
    EXCEED=0
    while read -r KEY VAL; do
        case "$KEY" in
            PASSWORD_CONTINOUS_ERRORS)    ERRORS="$VAL" ;;
            UNLOCK_ATTEMPT_EXCEED_LIMIT)  EXCEED="$VAL" ;;
        esac
    done < "$STATUS_FILE"
    # Sanitize : digits only, default 0.
    case "$ERRORS" in *[!0-9]*|"") ERRORS=0 ;; esac
    case "$EXCEED" in *[!0-9]*|"") EXCEED=0 ;; esac

    NOW="$(date -u +%s)"
    CHANGED=0
    if [ "$ERRORS" != "$LAST_ERRORS" ] || [ "$EXCEED" != "$LAST_EXCEED" ]; then
        CHANGED=1
    fi
    # Heartbeat every 30s even on no-change.
    if [ "$((NOW - LAST_HEARTBEAT))" -ge 30 ]; then
        CHANGED=1
    fi

    if [ "$CHANGED" -eq 1 ]; then
        PAYLOAD="$(printf '{"continuous_errors":%d,"exceed_count":%d}' "$ERRORS" "$EXCEED")"
        "$EVENT_PUSH" touchscreen_status "$PAYLOAD" >/dev/null 2>&1 || true
        LAST_ERRORS="$ERRORS"
        LAST_EXCEED="$EXCEED"
        LAST_HEARTBEAT="$NOW"
    fi

    sleep 2
done

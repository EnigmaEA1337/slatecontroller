#!/bin/sh
# slate-ctrl-event-push : sign + POST a Slate-side event to the controller webhook.
#
# Usage :
#   slate-ctrl-event-push <type> <json-payload>
#
# Reads the per-device secret from /etc/slate-controller/secrets/webhook.secret
# and the controller URL from /etc/slate-controller/controller-url. HMAC-signs
# "<ts>.<body>" with SHA-256, POSTs to <controller>/api/webhooks/slate/event
# with the X-Slate-* headers. Silent success ; logs to syslog on failure.
#
# busybox-compatible : ash, no bash. openssl for HMAC, curl for HTTPS.

set -eu

SECRET_FILE="/etc/slate-controller/secrets/webhook.secret"
URL_FILE="/etc/slate-controller/controller-url"
SLUG_FILE="/etc/slate-controller/device-slug"
# Optional : controller's internal-CA root. Pushed by the controller when
# its HTTPS cert is signed by the internal CA. Absent = we rely on the
# stock OpenWrt trust store (publicly-trusted certs, e.g. ts.net L-E).
CA_FILE="/etc/slate-controller/secrets/controller-ca.pem"

[ -r "$SECRET_FILE" ] || { logger -t slate-ctrl-event-push "no secret file"; exit 1; }
[ -r "$URL_FILE" ]    || { logger -t slate-ctrl-event-push "no controller URL"; exit 1; }
[ -r "$SLUG_FILE" ]   || { logger -t slate-ctrl-event-push "no device slug"; exit 1; }

SECRET="$(cat "$SECRET_FILE")"
URL="$(cat "$URL_FILE")"
SLUG="$(cat "$SLUG_FILE")"

# Reject http:// — the controller is always behind TLS. We refuse to
# leak the HMAC signature over plaintext even on tailnet, where in
# theory it's encrypted at the WireGuard layer : double belt + braces.
case "$URL" in
    https://*) : ;;
    *)
        logger -t slate-ctrl-event-push "URL must be https:// (got: $URL)"
        exit 5
        ;;
esac

[ "$#" -ge 2 ] || { logger -t slate-ctrl-event-push "usage: <type> <json>"; exit 2; }
TYPE="$1"
PAYLOAD="$2"

TS="$(date -u +%s)"
# Wrap payload in the envelope shape the controller expects.
BODY="$(printf '{"type":"%s","payload":%s,"sent_at":%s}' "$TYPE" "$PAYLOAD" "$TS")"

# HMAC-SHA256(secret, "<ts>.<body>") → hex. openssl mac is the modern
# subcommand ; fall back to dgst -hmac for older builds.
SIG="$(
    printf '%s.%s' "$TS" "$BODY" | \
        openssl dgst -sha256 -hmac "$SECRET" | \
        sed 's/^.* //'
)"

# 10s timeout : webhook delivery should be near-instant on tailnet, anything
# slower is the controller being off and the poll fallback will re-sync later.
# --cacert kicks in only when the controller's internal CA was provisioned ;
# without that flag curl falls back to the system trust store, which is what
# ts.net certs need.
CURL_TLS_ARGS=""
if [ -r "$CA_FILE" ]; then
    CURL_TLS_ARGS="--cacert $CA_FILE"
fi

HTTP=$(
    curl -sS -o /dev/null -w '%{http_code}' \
        --max-time 10 \
        $CURL_TLS_ARGS \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-Slate-Slug: $SLUG" \
        -H "X-Slate-Timestamp: $TS" \
        -H "X-Slate-Signature: $SIG" \
        --data-binary "$BODY" \
        "$URL/api/webhooks/slate/event" 2>/dev/null || echo "000"
)

case "$HTTP" in
    204|200)
        # Success — silent. Uncomment for debug :
        # logger -t slate-ctrl-event-push "ok type=$TYPE http=$HTTP"
        exit 0
        ;;
    000)
        logger -t slate-ctrl-event-push "controller unreachable type=$TYPE"
        exit 3
        ;;
    *)
        logger -t slate-ctrl-event-push "controller rejected type=$TYPE http=$HTTP"
        exit 4
        ;;
esac

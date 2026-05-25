# adguard.sh — slate-controller AdGuard subsystem handler.
#
# Reads the `adguard` block of the profile JSON via stdin and:
#   1. Toggles the AdGuardHome daemon (procd enable/disable + start/stop)
#   2. Reconciles the filter-list set against the profile's declared
#      `lists[]` via AdGuard's local REST API (:3000).
#
# Reconciliation strategy = STRICT, with a name-prefix marker. Filters
# we add are tagged `[slate-ctrl] <pretty name>`. On apply:
#   - desired URL marked + enabled → no-op
#   - desired URL marked + off    → POST /control/filtering/set_url enabled=true
#   - desired URL present but
#     UNMARKED                     → POST /control/filtering/set_url with marker
#                                    name + enabled=true (= ADOPTION; subsequent
#                                    applies treat it as managed)
#   - desired URL truly absent    → POST /control/filtering/add_url
#   - existing marked URL not in
#     desired list                 → POST /control/filtering/remove_url
#   - existing UNMARKED URLs that
#     are not desired              → LEFT ALONE
# Adoption is needed because the controller (via /api/adguard/feeds/apply,
# the AdGuard UI, or factory defaults) may have populated filters before
# the agent took over. Without adoption, those filters would trigger
# HTTP 400 (URL duplicate) on add_url forever.
#
# Auth: needs ADGUARD_USER + ADGUARD_PASSWORD. Sourced from
# /etc/slate-controller/secrets/adguard.env (chmod 600, written at
# /api/agent/deploy time). If the file is missing or unreadable, the
# toggle still runs but the reconciliation degrades to a log line —
# don't fail the whole apply for that.
#
# Profile JSON shape (adguard block, enriched by the controller at sync
# time — slugs are resolved to {slug, name, url} so we don't need a
# catalog file here):
#   {
#     "enabled": true,
#     "lists": [
#       { "slug": "hagezi-tif",
#         "name": "HaGeZi Threat Intel Feeds",
#         "url":  "https://raw.githubusercontent.com/.../tif.txt" },
#       { "slug": "unknown-xyz",
#         "missing": true }                # logged + skipped
#     ]
#   }

ADGUARD_HOST="127.0.0.1"
ADGUARD_PORT="3000"
ADGUARD_SECRET_FILE="/etc/slate-controller/secrets/adguard.env"
ADGUARD_FILTER_MARKER="[slate-ctrl] "

# --- helpers --------------------------------------------------------------

# Source the credentials file. Returns 0 on success, 1 if the file is
# missing or doesn't define both vars. We use a subshell guard so that
# malformed content can't poison the caller's environment.
_adguard_load_credentials() {
  if [ ! -f "$ADGUARD_SECRET_FILE" ]; then
    return 1
  fi
  # shellcheck disable=SC1090
  . "$ADGUARD_SECRET_FILE" 2>/dev/null || return 1
  if [ -z "${ADGUARD_USER:-}" ] || [ -z "${ADGUARD_PASSWORD:-}" ]; then
    return 1
  fi
  return 0
}

# curl wrapper. Stdout = response body. Stderr = curl error. Exit code:
# 0 on HTTP 2xx, 1 on anything else (network error or 4xx/5xx).
_adguard_curl() {
  local method="$1"
  local path="$2"
  local body="${3:-}"

  local http_code
  if [ -n "$body" ]; then
    http_code=$(curl -s -o /tmp/adguard.resp -w '%{http_code}' \
      -u "${ADGUARD_USER}:${ADGUARD_PASSWORD}" \
      -X "$method" \
      -H 'Content-Type: application/json' \
      -d "$body" \
      "http://${ADGUARD_HOST}:${ADGUARD_PORT}${path}" 2>/dev/null)
  else
    http_code=$(curl -s -o /tmp/adguard.resp -w '%{http_code}' \
      -u "${ADGUARD_USER}:${ADGUARD_PASSWORD}" \
      -X "$method" \
      "http://${ADGUARD_HOST}:${ADGUARD_PORT}${path}" 2>/dev/null)
  fi

  cat /tmp/adguard.resp 2>/dev/null
  rm -f /tmp/adguard.resp 2>/dev/null

  case "$http_code" in
    2*) return 0 ;;
    *)  echo "adguard: HTTP $http_code on $method $path" >&2; return 1 ;;
  esac
}

# Wait up to ~10s for AdGuard's REST API to answer. Used after start/restart
# because the daemon takes a couple of seconds to bind :3000.
_adguard_wait_ready() {
  local i=0
  while [ $i -lt 20 ]; do
    if _adguard_curl GET /control/status >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5 2>/dev/null || sleep 1
    i=$((i + 1))
  done
  return 1
}

# Reconcile the filter list against the profile's declared lists[]. Idempotent.
# Args: none. Reads $1 = payload from caller's scope via env var.
# Returns 0 on success (or graceful skip), 1 on hard failure.
_adguard_reconcile_filters() {
  local payload="$1"

  if ! _adguard_load_credentials; then
    echo "adguard: $ADGUARD_SECRET_FILE missing or invalid — skipping filter reconciliation"
    return 0
  fi

  if ! _adguard_wait_ready; then
    echo "adguard: REST API on :${ADGUARD_PORT} not responding — skipping filter reconciliation" >&2
    return 1
  fi

  # 1. Fetch the current filter list.
  local status_json
  status_json=$(_adguard_curl GET /control/filtering/status) || return 1

  # 2. Build two indexes from the current filter list:
  #      all_urls      = every URL (one per line) — for presence checks
  #      managed_lines = "<url>|<enabled>" for filters tagged with our marker
  #                      — for the cleanup pass (we only remove our own)
  #    The pipe delimiter is safe: it can't appear in an http(s) URL nor in
  #    the "true"/"false" strings AdGuard returns.
  local existing_count all_urls managed_lines
  existing_count=$(echo "$status_json" | jsonfilter -e '@.filters[*].url' 2>/dev/null | wc -l)
  all_urls=""
  managed_lines=""
  local i=0
  while [ "$i" -lt "$existing_count" ]; do
    local ex_name ex_url ex_enabled
    ex_name=$(echo "$status_json" | jsonfilter -e "@.filters[$i].name" 2>/dev/null)
    ex_url=$(echo "$status_json"  | jsonfilter -e "@.filters[$i].url" 2>/dev/null)
    ex_enabled=$(echo "$status_json" | jsonfilter -e "@.filters[$i].enabled" 2>/dev/null)
    i=$((i + 1))
    if [ -z "$all_urls" ]; then
      all_urls="$ex_url"
    else
      all_urls="${all_urls}
${ex_url}"
    fi
    case "$ex_name" in
      "${ADGUARD_FILTER_MARKER}"*)
        if [ -z "$managed_lines" ]; then
          managed_lines="${ex_url}|${ex_enabled}"
        else
          managed_lines="${managed_lines}
${ex_url}|${ex_enabled}"
        fi
        ;;
    esac
  done

  # 3. Walk the desired list. For each entry, decide between:
  #    a) Already in the right state (managed + enabled) → no-op
  #    b) Managed but disabled → set_url enabled=true
  #    c) Present but NOT marked (= added by the operator before we knew about it,
  #       or pre-existing AdGuard default) → set_url with marker name + enabled=true
  #       (adoption — tags it as ours so the cleanup pass treats it as managed)
  #    d) Absent → add_url with marker name
  local desired_count desired_urls
  desired_count=$(echo "$payload" | jsonfilter -e '@.lists[*]' 2>/dev/null | wc -l)
  desired_urls=""
  local added=0 enabled=0 adopted=0 already_ok=0 missing=0 errors=0

  local j=0
  while [ "$j" -lt "$desired_count" ]; do
    local d_slug d_name d_url d_missing
    d_slug=$(echo "$payload"    | jsonfilter -e "@.lists[$j].slug"    2>/dev/null)
    d_name=$(echo "$payload"    | jsonfilter -e "@.lists[$j].name"    2>/dev/null)
    d_url=$(echo "$payload"     | jsonfilter -e "@.lists[$j].url"     2>/dev/null)
    d_missing=$(echo "$payload" | jsonfilter -e "@.lists[$j].missing" 2>/dev/null)
    j=$((j + 1))

    if [ "$d_missing" = "true" ] || [ -z "$d_url" ]; then
      echo "adguard: slug '$d_slug' missing from controller catalog — skip"
      missing=$((missing + 1))
      continue
    fi

    if [ -z "$desired_urls" ]; then
      desired_urls="$d_url"
    else
      desired_urls="${desired_urls}
${d_url}"
    fi

    local pretty body
    pretty="${ADGUARD_FILTER_MARKER}${d_name}"

    # Case a: present, marked, enabled → done.
    if printf '%s\n' "$managed_lines" | grep -Fxq "${d_url}|true"; then
      already_ok=$((already_ok + 1))
      continue
    fi

    # Case b: present, marked, disabled → enable only (name already tagged).
    if printf '%s\n' "$managed_lines" | grep -Fxq "${d_url}|false"; then
      body=$(printf '{"url":"%s","whitelist":false,"data":{"url":"%s","enabled":true}}' \
        "$d_url" "$d_url")
      if _adguard_curl POST /control/filtering/set_url "$body" >/dev/null; then
        echo "adguard: enabled existing filter '$d_name'"
        enabled=$((enabled + 1))
      else
        echo "adguard: ERROR enabling '$d_name'" >&2
        errors=$((errors + 1))
      fi
      continue
    fi

    # Case c: present but unmarked → ADOPT. set_url with name=marker + enabled=true.
    if printf '%s\n' "$all_urls" | grep -Fxq "$d_url"; then
      body=$(printf '{"url":"%s","whitelist":false,"data":{"url":"%s","name":"%s","enabled":true}}' \
        "$d_url" "$d_url" "$pretty")
      if _adguard_curl POST /control/filtering/set_url "$body" >/dev/null; then
        echo "adguard: adopted existing filter as '$pretty'"
        adopted=$((adopted + 1))
      else
        echo "adguard: ERROR adopting '$d_name' ($d_url)" >&2
        errors=$((errors + 1))
      fi
      continue
    fi

    # Case d: truly absent → add with marker.
    body=$(printf '{"url":"%s","name":"%s","whitelist":false}' "$d_url" "$pretty")
    if _adguard_curl POST /control/filtering/add_url "$body" >/dev/null; then
      echo "adguard: added filter '$pretty'"
      added=$((added + 1))
    else
      echo "adguard: ERROR adding '$d_name' ($d_url)" >&2
      errors=$((errors + 1))
    fi
  done

  # 4. Remove managed filters that are no longer desired.
  #    Avoid `while | read` (subshell traps the counter on busybox). Iterate
  #    via newline-separated IFS so the loop runs in the current shell.
  local removed=0
  if [ -n "$managed_lines" ]; then
    local OLD_IFS m_line m_url
    OLD_IFS="$IFS"
    IFS='
'
    for m_line in $managed_lines; do
      [ -z "$m_line" ] && continue
      m_url=${m_line%%|*}
      # Skip if still in the desired list (full-line match, fixed string).
      if printf '%s\n' "$desired_urls" | grep -Fxq "$m_url"; then
        continue
      fi
      local body
      body=$(printf '{"url":"%s","whitelist":false}' "$m_url")
      if _adguard_curl POST /control/filtering/remove_url "$body" >/dev/null; then
        echo "adguard: removed stale managed filter $m_url"
        removed=$((removed + 1))
      else
        echo "adguard: ERROR removing $m_url" >&2
        errors=$((errors + 1))
      fi
    done
    IFS="$OLD_IFS"
  fi

  echo "adguard: filters reconciled — added=$added adopted=$adopted enabled=$enabled kept=$already_ok removed=$removed missing=$missing errors=$errors"

  [ "$errors" -gt 0 ] && return 1
  return 0
}

# --- entrypoint ------------------------------------------------------------

adguard_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local enabled
  enabled=$(echo "$payload" | jsonfilter -e '@.enabled' 2>/dev/null)

  if [ -z "$enabled" ]; then
    echo "adguard: 'enabled' field absent, nothing to do"
    return 0
  fi

  # Sanity check the init script exists.
  if [ ! -x /etc/init.d/adguardhome ]; then
    echo "adguard: /etc/init.d/adguardhome missing — AdGuardHome not installed?" >&2
    return 1
  fi

  local toggle_rc=0
  if [ "$enabled" = "true" ]; then
    # Persist (uci) + enable at boot + start now. Each step is idempotent
    # so re-applying the same profile is cheap.
    uci set adguardhome.config.enabled='1' 2>/dev/null
    uci commit adguardhome 2>/dev/null
    /etc/init.d/adguardhome enable 2>&1 >/dev/null
    if /etc/init.d/adguardhome status 2>/dev/null | grep -q running; then
      echo "adguard: already running"
    else
      /etc/init.d/adguardhome start 2>&1 || {
        echo "adguard: start failed" >&2
        return 1
      }
      echo "adguard: started + enabled at boot"
    fi
  elif [ "$enabled" = "false" ]; then
    uci set adguardhome.config.enabled='0' 2>/dev/null
    uci commit adguardhome 2>/dev/null
    /etc/init.d/adguardhome stop 2>&1 >/dev/null || true
    /etc/init.d/adguardhome disable 2>&1 >/dev/null || true
    echo "adguard: stopped + disabled at boot"
    # Disabling the daemon → REST API will be down, can't reconcile.
    # That's the operator's intent; exit clean.
    return 0
  else
    echo "adguard: 'enabled' must be true|false, got '$enabled'" >&2
    return 1
  fi

  # Filter reconciliation runs only when enabled=true. The daemon may
  # still be starting up; _adguard_wait_ready handles the wait.
  if ! _adguard_reconcile_filters "$payload"; then
    toggle_rc=1
  fi

  return $toggle_rc
}

# wifi.sh — slate-controller Wi-Fi subsystem handler.
#
# Reads the `wifi.ssids` block of the profile JSON via stdin and toggles
# each SSID's uci `disabled` flag accordingly, then issues a single
# `wifi reload` at the end so all changes apply atomically.
#
# Profile JSON shape (wifi block, enriched by the controller at sync time
# so we don't need a separate catalog file):
#   {
#     "ssids": [
#       { "slug": "missionpro",
#         "name": "MissionPro",      # broadcast name — used to find uci section
#         "band": "5GHz",
#         "security": "WPA3-SAE",
#         "network_slug": "lan",
#         "enabled": true },
#       { "slug": "parents",
#         "name": "Parents",
#         "enabled": false }
#     ]
#   }
#
# Mapping `name` → uci section:
#   We grep `uci show wireless` for `.ssid='<name>'$` and extract the
#   section reference (e.g. `wireless.@wifi-iface[3]` or `wireless.cfg062c0c`).
#   If multiple sections match (e.g. one per radio in MLO), all are toggled.

wifi_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local changes=0
  local errors=0

  # Iterate over the ssids array. jsonfilter prints one match per line.
  # We need name + enabled per element, so we extract them in lock-step
  # via two parallel queries.
  local count
  count=$(echo "$payload" | jsonfilter -e '@.ssids[*].slug' 2>/dev/null | wc -l)
  if [ -z "$count" ] || [ "$count" -eq 0 ]; then
    echo "wifi: no ssids referenced"
    return 0
  fi

  local i=0
  while [ "$i" -lt "$count" ]; do
    local slug name enabled
    slug=$(echo "$payload"    | jsonfilter -e "@.ssids[$i].slug"    2>/dev/null)
    name=$(echo "$payload"    | jsonfilter -e "@.ssids[$i].name"    2>/dev/null)
    enabled=$(echo "$payload" | jsonfilter -e "@.ssids[$i].enabled" 2>/dev/null)
    i=$((i + 1))

    if [ -z "$name" ]; then
      echo "wifi: skip $slug (no resolved name — likely missing from catalog)"
      continue
    fi

    # Find the uci section(s) that broadcast this SSID. Match the WHOLE
    # value to avoid prefix collisions (`MissionPro` vs `MissionPro_Guest`).
    local sections
    sections=$(uci show wireless 2>/dev/null \
      | grep "\.ssid='${name}'\$" \
      | sed -e "s/\.ssid='${name}'\$//")

    if [ -z "$sections" ]; then
      echo "wifi: no uci section for SSID '$name' ($slug) — skip"
      continue
    fi

    # disabled = NOT enabled.
    local disabled_val
    if [ "$enabled" = "true" ]; then
      disabled_val=0
    else
      disabled_val=1
    fi

    local sec
    for sec in $sections; do
      if uci set "${sec}.disabled=${disabled_val}" 2>/dev/null; then
        echo "wifi: $name ($slug) on $sec → disabled=$disabled_val"
        changes=$((changes + 1))
      else
        echo "wifi: uci set failed on $sec" >&2
        errors=$((errors + 1))
      fi
    done
  done

  if [ "$changes" -eq 0 ] && [ "$errors" -eq 0 ]; then
    echo "wifi: nothing changed"
    return 0
  fi

  # Commit + reload exactly once at the end. `wifi reload` is faster than
  # `wifi` (full re-init) and doesn't drop already-connected clients on
  # SSIDs we didn't touch.
  if [ "$changes" -gt 0 ]; then
    uci commit wireless 2>&1 || {
      echo "wifi: uci commit failed" >&2
      return 1
    }
    wifi reload 2>&1 || {
      echo "wifi: 'wifi reload' failed" >&2
      return 1
    }
    echo "wifi: $changes section(s) updated, reload done"
  fi

  [ "$errors" -gt 0 ] && return 1
  return 0
}

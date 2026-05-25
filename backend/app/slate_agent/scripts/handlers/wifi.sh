# wifi.sh — slate-controller Wi-Fi subsystem handler.
#
# Reads the `wifi.ssids` block of the profile JSON via stdin and either :
#   - Toggles the `disabled` flag of an existing wifi-iface section
#     matching the SSID by broadcast name (default path)
#   - CREATES a new wifi-iface section named SC_WL_<slug> when no
#     existing one broadcasts that name — using band → radio device,
#     security → encryption, and PSK from /etc/slate-controller/secrets/wifi.env
#
# Issues a single `wifi reload` at the end so all changes apply atomically.
#
# Profile JSON shape (wifi block, enriched by the controller at sync time
# so we don't need a separate catalog file):
#   {
#     "ssids": [
#       { "slug": "neuralcore",
#         "name": "NEURAL_LINK_01",      # broadcast name
#         "band": "5GHz",                # 2GHz | 5GHz | 6GHz | MLO
#         "security": "WPA3-SAE",        # WPA3-SAE | WPA2-PSK | open
#         "network_slug": "lan",         # must be an existing UCI iface
#         "client_isolation": false,
#         "enabled": true },
#       ...
#     ]
#   }
#
# Naming convention for sections we CREATE :
#   SC_WL_<slug>   uppercase, alphanumeric+underscore, ≤ 32 chars.
#   Same idea as SC_FR_ for firewall rules — single source of truth,
#   greppable via `uci show wireless | grep SC_WL_`.
#
# Mapping `name` → uci section (toggle path) :
#   We grep `uci show wireless` for `.ssid='<name>'$` and extract the
#   section reference. If multiple match (e.g. one per radio in MLO),
#   all are toggled.

# Per-radio device on Slate 7 Pro (mt7990 chipset). Probed live :
#   MT7990_1_1 → 2.4 GHz
#   MT7990_1_2 → 5   GHz
#   MT7990_1_3 → 6   GHz
# Hardcoded for now — when we'll support more devices we'll discover
# at runtime via `uci show wireless | grep =wifi-device`.
_wifi_radio_for_band() {
  case "$1" in
    2GHz|2g|24g|2.4GHz) echo "MT7990_1_1" ;;
    5GHz|5g)            echo "MT7990_1_2" ;;
    6GHz|6g)            echo "MT7990_1_3" ;;
    *)                  echo "" ;;
  esac
}

# Security → uci encryption value mapping.
#   WPA3-SAE        → sae
#   WPA3-SAE-MIXED  → sae-mixed (WPA2/3 transition)
#   WPA2-PSK        → psk2+ccmp
#   open            → none
_wifi_encryption_for_security() {
  case "$1" in
    WPA3-SAE)        echo "sae" ;;
    WPA3-SAE-MIXED)  echo "sae-mixed" ;;
    WPA2-PSK)        echo "psk2+ccmp" ;;
    open|none)       echo "none" ;;
    *)               echo "" ;;
  esac
}

# Convert a slug to the shell-variable name used in wifi.env.
# Must mirror `_slug_to_env` in `app/slate_agent/deploy.py`.
#   neuralcore → NEURALCORE
#   wg-CH-ZA-1 → WG_CH_ZA_1
_wifi_slug_to_env() {
  echo "$1" | tr 'a-z-' 'A-Z_' | sed 's/[^A-Z0-9_]/_/g'
}

_wifi_create_section() {
  # $1 slug  $2 name  $3 band  $4 security  $5 network  $6 client_isolation  $7 enabled
  local slug="$1" name="$2" band="$3" security="$4" network="$5"
  local isolate="$6" enabled="$7"
  local section="SC_WL_$(_wifi_slug_to_env "$slug")"

  # MLO needs one wifi-iface per radio glued together — not yet handled.
  # Refuse cleanly so the user knows what to do.
  if [ "$band" = "MLO" ]; then
    echo "wifi: SSID '$name' band=MLO not supported by CREATE yet — set band to 2GHz/5GHz/6GHz in the catalog" >&2
    return 1
  fi

  local device
  device=$(_wifi_radio_for_band "$band")
  if [ -z "$device" ]; then
    echo "wifi: SSID '$name' unknown band '$band' — skip" >&2
    return 1
  fi

  local encryption
  encryption=$(_wifi_encryption_for_security "$security")
  if [ -z "$encryption" ]; then
    echo "wifi: SSID '$name' unknown security '$security' — skip" >&2
    return 1
  fi

  # Source PSKs from the secrets file. Missing file = no PSKs available
  # (handler then refuses to create any non-open SSID).
  local secrets_file="/etc/slate-controller/secrets/wifi.env"
  [ -f "$secrets_file" ] && . "$secrets_file"

  local env_name psk
  env_name=$(_wifi_slug_to_env "$slug")
  eval "psk=\${WIFI_${env_name}_PSK:-}"

  if [ "$encryption" != "none" ] && [ -z "$psk" ]; then
    echo "wifi: SSID '$name' needs a PSK but WIFI_${env_name}_PSK is unset in $secrets_file — re-run /api/agent/deploy after setting it in the WiFi catalog" >&2
    return 1
  fi

  # Verify the target network actually exists in UCI — otherwise the
  # wifi-iface won't bind anywhere.
  if ! uci -q get "network.$network" >/dev/null 2>&1; then
    echo "wifi: SSID '$name' target network 'network.$network' missing — skip" >&2
    return 1
  fi

  # Verify the radio device exists too (some firmware variants ship
  # with fewer radios on disabled hw).
  if ! uci -q get "wireless.$device" >/dev/null 2>&1; then
    echo "wifi: SSID '$name' radio '$device' missing — skip" >&2
    return 1
  fi

  local disabled_val
  if [ "$enabled" = "true" ]; then
    disabled_val=0
  else
    disabled_val=1
  fi

  # Upsert. Re-running with the same params is a no-op (idempotent).
  uci -q get "wireless.$section" >/dev/null 2>&1 \
    || uci set "wireless.$section=wifi-iface"
  uci set "wireless.$section.device=$device"
  uci set "wireless.$section.network=$network"
  uci set "wireless.$section.mode=ap"
  uci set "wireless.$section.ssid=$name"
  uci set "wireless.$section.encryption=$encryption"
  if [ "$encryption" != "none" ]; then
    uci set "wireless.$section.key=$psk"
  fi
  uci set "wireless.$section.disabled=$disabled_val"
  # client_isolation = block client↔client traffic on the same AP.
  # OpenWrt uses `option isolate '1'` for this on ap mode ifaces.
  if [ "$isolate" = "true" ]; then
    uci set "wireless.$section.isolate=1"
  else
    uci -q delete "wireless.$section.isolate" 2>/dev/null
  fi
  echo "wifi: created SSID '$name' on $device/$network ($section, encryption=$encryption, disabled=$disabled_val)"
  return 0
}

wifi_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local changes=0
  local errors=0

  local count
  count=$(echo "$payload" | jsonfilter -e '@.ssids[*].slug' 2>/dev/null | wc -l)
  if [ -z "$count" ] || [ "$count" -eq 0 ]; then
    echo "wifi: no ssids referenced"
    return 0
  fi

  local i=0
  while [ "$i" -lt "$count" ]; do
    local slug name enabled band security network isolate
    slug=$(echo "$payload"     | jsonfilter -e "@.ssids[$i].slug"             2>/dev/null)
    name=$(echo "$payload"     | jsonfilter -e "@.ssids[$i].name"             2>/dev/null)
    enabled=$(echo "$payload"  | jsonfilter -e "@.ssids[$i].enabled"          2>/dev/null)
    band=$(echo "$payload"     | jsonfilter -e "@.ssids[$i].band"             2>/dev/null)
    security=$(echo "$payload" | jsonfilter -e "@.ssids[$i].security"         2>/dev/null)
    network=$(echo "$payload"  | jsonfilter -e "@.ssids[$i].network_slug"     2>/dev/null)
    isolate=$(echo "$payload"  | jsonfilter -e "@.ssids[$i].client_isolation" 2>/dev/null)
    i=$((i + 1))

    if [ -z "$name" ]; then
      echo "wifi: skip $slug (no resolved name — likely missing from catalog)"
      continue
    fi

    # Try to find an existing section broadcasting this name first.
    # Match the WHOLE value to avoid prefix collisions
    # (`MissionPro` vs `MissionPro_Guest`).
    local sections
    sections=$(uci show wireless 2>/dev/null \
      | grep "\.ssid='${name}'\$" \
      | sed -e "s/\.ssid='${name}'\$//")

    if [ -z "$sections" ]; then
      # CREATE path. Needs the full record from the profile JSON
      # (band, security, network) — fall back to skip with a clear log
      # if anything's missing.
      if [ -z "$band" ] || [ -z "$security" ] || [ -z "$network" ]; then
        echo "wifi: '$name' ($slug) has no uci section AND profile JSON lacks band/security/network for CREATE — skip"
        continue
      fi
      if _wifi_create_section "$slug" "$name" "$band" "$security" "$network" "$isolate" "$enabled"; then
        changes=$((changes + 1))
      else
        errors=$((errors + 1))
      fi
      continue
    fi

    # TOGGLE path. disabled = NOT enabled.
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

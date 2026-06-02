# radio.sh — slate-controller per-band radio (layer-1) handler.
#
# Reads the `radio.bands` block of the profile JSON via stdin and updates
# the matching `wireless.MT7990_1_<N>` wifi-device sections.
#
# Payload shape :
#   { "bands": [
#       { "band": "2", "channel": "auto", "htmode": "HT40",
#         "txpower": "100", "country": "FR" },
#       { "band": "5", "channel": "48",   "htmode": "EHT160", ... },
#       { "band": "6", "channel": "auto", "htmode": "EHT320", ... }
#     ] }
#
# Changes to channel / htmode / country trigger a `wifi reload` ; they
# do NOT require a full reboot. The wifi.sh handler still emits the
# "REBOOT required" signal only for LAYOUT changes on wifi-iface
# sections (new SSID, MLO toggle, etc.).

_RADIO_DEVICE_FOR_BAND() {
  case "$1" in
    2|2g|24|2GHz|2.4GHz) echo "MT7990_1_1" ;;
    5|5g|5GHz)           echo "MT7990_1_2" ;;
    6|6g|6GHz)           echo "MT7990_1_3" ;;
    *)                   echo "" ;;
  esac
}

radio_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local count
  count=$(echo "$payload" | jsonfilter -e '@.bands[*].band' 2>/dev/null | wc -l)
  [ -z "$count" ] && count=0
  if [ "$count" -eq 0 ]; then
    return 0
  fi

  local errors=0 changes=0 i=0
  while [ "$i" -lt "$count" ]; do
    local band device
    band=$(echo "$payload" | jsonfilter -e "@.bands[$i].band" 2>/dev/null)
    device=$(_RADIO_DEVICE_FOR_BAND "$band")
    i=$((i + 1))
    if [ -z "$device" ]; then
      echo "radio: unknown band '$band' — skip" >&2
      errors=$((errors + 1))
      continue
    fi
    if ! uci -q get "wireless.$device" >/dev/null 2>&1; then
      echo "radio: wifi-device wireless.$device missing — skip" >&2
      errors=$((errors + 1))
      continue
    fi

    local channel htmode txpower country
    channel=$(echo "$payload" | jsonfilter -e "@.bands[$((i-1))].channel" 2>/dev/null)
    htmode=$(echo "$payload"  | jsonfilter -e "@.bands[$((i-1))].htmode"  2>/dev/null)
    txpower=$(echo "$payload" | jsonfilter -e "@.bands[$((i-1))].txpower" 2>/dev/null)
    country=$(echo "$payload" | jsonfilter -e "@.bands[$((i-1))].country" 2>/dev/null)

    local cur_chan cur_htmode cur_tx cur_country
    cur_chan=$(uci -q get "wireless.$device.channel")
    cur_htmode=$(uci -q get "wireless.$device.htmode")
    cur_tx=$(uci -q get "wireless.$device.txpower")
    cur_country=$(uci -q get "wireless.$device.country")

    if [ -n "$channel" ] && [ "$cur_chan" != "$channel" ]; then
      uci set "wireless.$device.channel=$channel"
      changes=$((changes + 1))
    fi
    if [ -n "$htmode" ] && [ "$cur_htmode" != "$htmode" ]; then
      uci set "wireless.$device.htmode=$htmode"
      changes=$((changes + 1))
    fi
    if [ -n "$txpower" ] && [ "$cur_tx" != "$txpower" ]; then
      uci set "wireless.$device.txpower=$txpower"
      changes=$((changes + 1))
    fi
    if [ -n "$country" ] && [ "$cur_country" != "$country" ]; then
      uci set "wireless.$device.country=$country"
      changes=$((changes + 1))
    fi
    echo "radio: band $band ($device) channel=$channel htmode=$htmode txpower=$txpower country=$country"
  done

  if [ "$changes" -gt 0 ]; then
    uci commit wireless 2>&1 || { echo "radio: uci commit failed" >&2; return 1; }
    # `wifi reload` re-arms hostapd with the new device settings without
    # tearing down per-VAP state. Channel / htmode / country are picked
    # up at this stage.
    wifi reload 2>&1 \
      || echo "radio: wifi reload returned non-zero (may be benign on MTK)" >&2
    echo "radio: applied $changes change(s)"
  else
    echo "radio: no changes to apply"
  fi

  [ "$errors" -gt 0 ] && return 1
  return 0
}

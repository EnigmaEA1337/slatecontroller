# firewall.sh — slate-controller firewall subsystem handler.
#
# Reads the `firewall` block of the profile JSON via stdin and applies a
# best-effort set of UCI tweaks for each option. Some options need packages
# that aren't shipped with the stock Slate firmware (xt_geoip for the
# country whitelist, ipset advanced rules) — those are logged as skipped
# instead of silently failing.
#
# Profile JSON shape (firewall block):
#   {
#     "lockdown":           bool,  // strict default-deny + leak rules on
#     "geoip_whitelist":    ["FR", "CH", ...],  // ISO country codes
#     "block_telemetry":    bool,  // delegated to AdGuard blocklists today
#     "block_all_outbound": bool   // disable lan→wan forwarding entirely
#   }
#
# What lockdown DOES today (with stock packages):
#   - Enable every *_drop_leaked_dns / *_drop_leaked_adgdns rule that
#     exists in the firewall config (same set as the anti-bypass action).
#   - Set zone wan's `forward` policy to REJECT (default-deny WAN traffic).
#
# What lockdown CAN'T do without extra packages:
#   - geoip_whitelist : needs `iptables-mod-geoip` + GeoLite2 DB
#     (~12 MB, not stock). Skipped with warning.
#   - block_telemetry : telemetry domains live in AdGuard's filter lists
#     (HaGeZi tracker, etc.) — not the firewall's responsibility. Skipped
#     with note to the operator.

firewall_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local lockdown geoip_count block_telemetry block_all_outbound
  lockdown=$(echo "$payload" | jsonfilter -e '@.lockdown' 2>/dev/null)
  geoip_count=$(echo "$payload" | jsonfilter -e '@.geoip_whitelist[*]' 2>/dev/null | wc -l)
  block_telemetry=$(echo "$payload" | jsonfilter -e '@.block_telemetry' 2>/dev/null)
  block_all_outbound=$(echo "$payload" | jsonfilter -e '@.block_all_outbound' 2>/dev/null)

  local changes=0
  local errors=0
  local wan_forward_target

  # --- lockdown : default-deny wan forward + activate leak rules --------
  if [ "$lockdown" = "true" ]; then
    wan_forward_target="REJECT"
    echo "firewall: lockdown ON — set wan forward=REJECT + activate leak rules"
    for slug in lan_drop_leaked_dns lan_drop_leak_adgdns \
                guest_drop_leaked_dns guest_drop_leak_adgdns \
                wgserver_drop_leaked_dns wgserver_drop_leaked_adgdns \
                ovpnserver_drop_leaked_dns ovpnserver_drop_leaked_adgdns; do
      if uci -q get firewall.$slug >/dev/null 2>&1; then
        uci set firewall.$slug.enabled='1' 2>/dev/null && changes=$((changes + 1))
      fi
    done
  elif [ "$lockdown" = "false" ]; then
    # Profile says explicitly NO lockdown : set wan forward back to ACCEPT
    # (typical default). Leak rules are left as-is — operator may want them
    # on permanently via the anti-bypass page.
    wan_forward_target="ACCEPT"
    echo "firewall: lockdown OFF — set wan forward=ACCEPT"
  fi

  if [ -n "$wan_forward_target" ]; then
    # Find the wan zone section name (usually @zone[1], but not guaranteed).
    local wan_zone
    wan_zone=$(uci show firewall 2>/dev/null | grep -E "\.name='wan'" \
      | head -n1 | sed -e "s/\.name='wan'//")
    if [ -n "$wan_zone" ]; then
      if uci set "${wan_zone}.forward=${wan_forward_target}" 2>/dev/null; then
        changes=$((changes + 1))
      else
        echo "firewall: ERROR setting wan zone forward" >&2
        errors=$((errors + 1))
      fi
    else
      echo "firewall: no wan zone found in uci show firewall" >&2
    fi
  fi

  # --- block_all_outbound : disable lan→wan forwarding entirely ---------
  if [ "$block_all_outbound" = "true" ]; then
    # Find the lan→wan forwarding section
    local fwd_section
    fwd_section=$(uci show firewall 2>/dev/null \
      | grep "\.src='lan'" | grep -B0 "@forwarding" \
      | head -n1 | sed -e 's/\.src=.*$//')
    # Fallback: iterate all forwardings and match src=lan dest=wan
    if [ -z "$fwd_section" ]; then
      local i=0
      while [ $i -lt 20 ]; do
        local src dest
        src=$(uci -q get "firewall.@forwarding[$i].src" 2>/dev/null)
        dest=$(uci -q get "firewall.@forwarding[$i].dest" 2>/dev/null)
        if [ "$src" = "lan" ] && [ "$dest" = "wan" ]; then
          fwd_section="firewall.@forwarding[$i]"
          break
        fi
        [ -z "$src" ] && break
        i=$((i + 1))
      done
    fi
    if [ -n "$fwd_section" ]; then
      if uci set "${fwd_section}.enabled=0" 2>/dev/null; then
        echo "firewall: block_all_outbound — disabled lan→wan forwarding"
        changes=$((changes + 1))
      else
        echo "firewall: ERROR disabling lan→wan forwarding" >&2
        errors=$((errors + 1))
      fi
    else
      echo "firewall: no lan→wan forwarding section found, skip block_all_outbound" >&2
    fi
  elif [ "$block_all_outbound" = "false" ]; then
    # Ensure the standard lan→wan forwarding is enabled
    local i=0
    while [ $i -lt 20 ]; do
      local src dest
      src=$(uci -q get "firewall.@forwarding[$i].src" 2>/dev/null)
      dest=$(uci -q get "firewall.@forwarding[$i].dest" 2>/dev/null)
      if [ "$src" = "lan" ] && [ "$dest" = "wan" ]; then
        uci set "firewall.@forwarding[$i].enabled=1" 2>/dev/null && changes=$((changes + 1))
        break
      fi
      [ -z "$src" ] && break
      i=$((i + 1))
    done
  fi

  # --- geoip_whitelist : needs xt_geoip, log if missing -----------------
  if [ "$geoip_count" -gt 0 ]; then
    local countries
    countries=$(echo "$payload" | jsonfilter -e '@.geoip_whitelist[*]' 2>/dev/null \
      | tr '\n' ',' | sed 's/,$//')
    if iptables -m geoip --help >/dev/null 2>&1; then
      echo "firewall: geoip_whitelist=[$countries] — xt_geoip present but per-country rule build is V2 (skipped)"
    else
      echo "firewall: geoip_whitelist=[$countries] requested but xt_geoip not installed (opkg install iptables-mod-geoip + GeoLite2 DB needed)"
    fi
  fi

  # --- block_telemetry : delegate to AdGuard ----------------------------
  if [ "$block_telemetry" = "true" ]; then
    echo "firewall: block_telemetry requested — telemetry blocking lives in AdGuard's filter lists (hagezi-tracker-radio, hagezi-pro, etc.). Configure those instead of relying on firewall rules."
  fi

  # --- commit + reload --------------------------------------------------
  if [ "$changes" -gt 0 ]; then
    if ! uci commit firewall 2>&1; then
      echo "firewall: uci commit failed" >&2
      return 1
    fi
    # Reload synchronously is risky (drops active connections including the
    # SSH session we're running through). Fire-and-forget detach instead,
    # 1s delay so we return cleanly first.
    (sleep 1 && /etc/init.d/firewall reload >/dev/null 2>&1) &
    echo "firewall: $changes uci change(s), reload scheduled (1s background)"
  else
    echo "firewall: no changes"
  fi

  [ "$errors" -gt 0 ] && return 1
  return 0
}

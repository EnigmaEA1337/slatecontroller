# vpn.sh — slate-controller VPN subsystem handler.
#
# Activates / deactivates an upstream VPN client according to the profile.
# Stock GL.iNet firmware provides separate UCI sections for WireGuard and
# OpenVPN clients; the handler picks the right one based on `type` and
# matches `client` against the section's `name` option.
#
# Profile JSON shape (vpn block):
#   {
#     "type":        "wireguard" | "openvpn" | "none",
#     "client":      "corporate-paris",   // name of the registered client
#     "kill_switch": bool                  // GL.iNet "block when down"
#   }
#
# Mapping → GL.iNet UCI sections (firmware 4.x) :
#   - WireGuard clients : `wireguard.<name>` of type `proxy`
#                         + `wireguard.proxy.main_server='<name>'` to activate
#   - OpenVPN  clients  : `openvpn.@openvpn[N].config_name` + `enabled`
#   - kill-switch       : `gl-firewall.@kill_switch[0].enable` (varies)
#
# Caveats :
#   - The exact UCI option names move between firmwares. We probe a couple
#     of patterns and log what's actually found, rather than hardcoding a
#     single rigid path.
#   - `type=none` stops every running VPN tunnel (wireguard + openvpn),
#     which on lockdown profiles is exactly what's wanted.

vpn_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local vpn_type client kill_switch
  vpn_type=$(echo "$payload" | jsonfilter -e '@.type' 2>/dev/null)
  client=$(echo "$payload" | jsonfilter -e '@.client' 2>/dev/null)
  kill_switch=$(echo "$payload" | jsonfilter -e '@.kill_switch' 2>/dev/null)

  if [ -z "$vpn_type" ]; then
    echo "vpn: type absent, nothing to do"
    return 0
  fi

  case "$vpn_type" in
    none)
      _vpn_disable_all
      ;;
    wireguard)
      _vpn_apply_wireguard "$client"
      ;;
    openvpn)
      _vpn_apply_openvpn "$client"
      ;;
    *)
      echo "vpn: unsupported type=$vpn_type (expected wireguard|openvpn|none)" >&2
      return 1
      ;;
  esac

  # Kill-switch is orthogonal to the client choice — flip it independently
  # if the profile says so. The exact UCI path depends on firmware version,
  # so try the two known variants.
  if [ "$kill_switch" = "true" ]; then
    _vpn_set_kill_switch 1
  elif [ "$kill_switch" = "false" ]; then
    _vpn_set_kill_switch 0
  fi
}

_vpn_disable_all() {
  echo "vpn: disabling every active tunnel (type=none)"
  local stopped=0
  if [ -x /etc/init.d/wireguard ]; then
    /etc/init.d/wireguard stop >/dev/null 2>&1 && stopped=$((stopped + 1))
  fi
  if [ -x /etc/init.d/openvpn ]; then
    /etc/init.d/openvpn stop >/dev/null 2>&1 && stopped=$((stopped + 1))
  fi
  # Some GL.iNet firmwares use `glwireguard` / `glopenvpn` init names
  if [ -x /etc/init.d/gl_wgclient ]; then
    /etc/init.d/gl_wgclient stop >/dev/null 2>&1 && stopped=$((stopped + 1))
  fi
  if [ -x /etc/init.d/glwgsclient ]; then
    /etc/init.d/glwgsclient stop >/dev/null 2>&1 && stopped=$((stopped + 1))
  fi
  echo "vpn: $stopped daemon(s) stopped"
  return 0
}

_vpn_apply_wireguard() {
  local target="$1"
  if [ -z "$target" ]; then
    echo "vpn: type=wireguard but no client name provided — skip" >&2
    return 1
  fi

  # Find the wireguard client section whose `name` matches our target.
  # GL.iNet stores those under `wireguard.<peer>` of type=`peers` (4.7+)
  # or `wireguard.<name>=proxy` (older). We search by the `name` option.
  local section
  section=$(uci show wireguard 2>/dev/null \
    | grep "\.name='${target}'\$" \
    | head -n1 \
    | sed -e "s/\.name='${target}'\$//")

  if [ -z "$section" ]; then
    echo "vpn: WireGuard client '$target' not found in uci show wireguard" >&2
    echo "vpn: available clients:" >&2
    uci show wireguard 2>/dev/null | grep "\.name=" | sed 's/^/  /' >&2
    return 1
  fi

  # Activate that section as the main_server. The exact option name moved
  # between firmwares — try both common variants.
  local activated=0
  if uci -q get wireguard.proxy >/dev/null 2>&1; then
    # 4.x firmware: main_server option
    if uci set wireguard.proxy.main_server="${target}" 2>/dev/null; then
      activated=1
    fi
  fi
  uci set "${section}.enabled=1" 2>/dev/null && activated=1

  if [ "$activated" -ne 1 ]; then
    echo "vpn: failed to mark '$target' as active in uci" >&2
    return 1
  fi

  uci commit wireguard 2>&1 || {
    echo "vpn: uci commit wireguard failed" >&2
    return 1
  }
  # Restart the wireguard daemon to pick up the new active client.
  if [ -x /etc/init.d/wireguard ]; then
    /etc/init.d/wireguard restart >/dev/null 2>&1 && \
      echo "vpn: WireGuard client '$target' activated"
  elif [ -x /etc/init.d/gl_wgclient ]; then
    /etc/init.d/gl_wgclient restart >/dev/null 2>&1 && \
      echo "vpn: WireGuard client '$target' activated (via gl_wgclient)"
  else
    echo "vpn: no wireguard init script — uci changed but daemon not restarted" >&2
    return 1
  fi
  return 0
}

_vpn_apply_openvpn() {
  local target="$1"
  if [ -z "$target" ]; then
    echo "vpn: type=openvpn but no client name provided — skip" >&2
    return 1
  fi

  # OpenVPN clients are usually `openvpn.@openvpn[N]` sections with a
  # `config_name` or `name` option. Iterate.
  local i=0 found=""
  while [ $i -lt 20 ]; do
    local name cname
    name=$(uci -q get "openvpn.@openvpn[$i].name" 2>/dev/null)
    cname=$(uci -q get "openvpn.@openvpn[$i].config_name" 2>/dev/null)
    if [ "$name" = "$target" ] || [ "$cname" = "$target" ]; then
      found="openvpn.@openvpn[$i]"
      break
    fi
    [ -z "$name" ] && [ -z "$cname" ] && break  # end of sections
    i=$((i + 1))
  done

  if [ -z "$found" ]; then
    echo "vpn: OpenVPN client '$target' not found in uci show openvpn" >&2
    return 1
  fi

  uci set "${found}.enabled=1" 2>/dev/null || {
    echo "vpn: failed to enable '$found'" >&2
    return 1
  }
  uci commit openvpn 2>&1 || {
    echo "vpn: uci commit openvpn failed" >&2
    return 1
  }
  /etc/init.d/openvpn restart >/dev/null 2>&1 && \
    echo "vpn: OpenVPN client '$target' activated ($found)"
  return 0
}

_vpn_set_kill_switch() {
  local flag="$1"
  # Probe common UCI paths. GL.iNet 4.x uses `gl-firewall.@kill_switch[0]`,
  # older firmwares used `glconfig.general.policy_routing_kill_switch`.
  local set_count=0
  if uci -q get gl-firewall.@kill_switch[0] >/dev/null 2>&1; then
    uci set gl-firewall.@kill_switch[0].enable="$flag" 2>/dev/null && set_count=$((set_count + 1))
    uci commit gl-firewall 2>/dev/null
  fi
  if uci -q get glconfig.general.policy_routing_kill_switch >/dev/null 2>&1 \
     || [ "$flag" = "0" ]; then
    uci set glconfig.general.policy_routing_kill_switch="$flag" 2>/dev/null && set_count=$((set_count + 1))
    uci commit glconfig 2>/dev/null
  fi
  if [ "$set_count" -gt 0 ]; then
    echo "vpn: kill_switch=$flag set ($set_count uci path(s))"
  else
    echo "vpn: kill_switch=$flag requested but no compatible UCI option found on this firmware" >&2
  fi
}

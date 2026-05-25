# dns.sh — slate-controller DNS subsystem handler.
#
# Reads the `dns` block of the profile JSON via stdin and applies:
#   - `servers[]` → WAN DNS servers (uci network.wan.dns) + peerdns=0
#                   (or peerdns=1 if list is empty, restoring ISP-provided)
#   - `forced`    → gl-dns.@dns[0].force_dns (1 = force clients to use
#                   our DNS, 0 = allow client-side DNS)
#
# Profile JSON shape (dns block):
#   {
#     "servers": ["10.2.0.1", "9.9.9.9"],
#     "forced":  true
#   }
#
# Reloads network + dnsmasq at the end so the change takes effect without
# bouncing the wan interface unnecessarily.

dns_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local servers forced
  # Space-separated list of servers — uci network.wan.dns expects this format.
  servers=$(echo "$payload" | jsonfilter -e '@.servers[*]' 2>/dev/null | tr '\n' ' ' | sed -e 's/ $//')
  forced=$(echo "$payload" | jsonfilter -e '@.forced' 2>/dev/null)

  local changes=0

  # 1. WAN DNS servers.
  if [ -n "$servers" ]; then
    # Pin our own list, ignore ISP-provided.
    if uci set network.wan.dns="$servers" 2>/dev/null \
       && uci set network.wan.peerdns=0 2>/dev/null; then
      echo "dns: WAN dns=[$servers], peerdns=0"
      changes=$((changes + 1))
    else
      echo "dns: ERROR setting network.wan.dns" >&2
      return 1
    fi
  else
    # Empty list = restore ISP DNS (peerdns=1, drop the dns option entirely).
    if uci set network.wan.peerdns=1 2>/dev/null; then
      uci delete network.wan.dns 2>/dev/null || true
      echo "dns: peerdns=1 (ISP-provided)"
      changes=$((changes + 1))
    fi
  fi

  # 2. Force-DNS toggle. GL.iNet has a dedicated uci section.
  if [ "$forced" = "true" ]; then
    if uci set gl-dns.@dns[0].force_dns=1 2>/dev/null; then
      echo "dns: force_dns=1 (clients pinned)"
      changes=$((changes + 1))
    fi
  elif [ "$forced" = "false" ]; then
    if uci set gl-dns.@dns[0].force_dns=0 2>/dev/null; then
      echo "dns: force_dns=0 (clients free)"
      changes=$((changes + 1))
    fi
  fi
  # forced field absent → leave the toggle as-is, no change.

  if [ "$changes" -eq 0 ]; then
    echo "dns: nothing to apply"
    return 0
  fi

  # 3. Commit + reload. We reload network (re-evaluates resolv.conf.auto)
  # and dnsmasq (re-reads upstream DNS list) instead of restart so active
  # leases survive.
  uci commit network 2>&1 || {
    echo "dns: uci commit network failed" >&2
    return 1
  }
  uci commit gl-dns 2>&1 || {
    # gl-dns may not have any uncommitted changes — that's fine.
    :
  }

  /etc/init.d/network reload 2>&1 || {
    echo "dns: network reload WARN (continuing)" >&2
  }
  /etc/init.d/dnsmasq reload 2>&1 || {
    echo "dns: dnsmasq reload WARN (continuing)" >&2
  }

  echo "dns: applied ($changes change(s))"
  return 0
}

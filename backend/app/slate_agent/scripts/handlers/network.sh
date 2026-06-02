# network.sh — slate-controller Network subsystem handler.
#
# Reads the `networks` block of the profile JSON via stdin and reconciles
# the Slate's UCI state for every network in the controller's catalog :
# bridge devices, logical interfaces, DHCP pools, firewall zones, per-
# service input rules, and inter-zone forwardings.
#
# Profile JSON shape (network block, enriched by the controller at sync
# time so the handler is self-sufficient). Key is singular `network` so
# the slate-ctrl dispatcher matches it (subsystem name == JSON key).
#
#   {
#     "network": {
#       "items": [
#         { "slug": "blackice",
#         "display_name": "BLACKICE (HUBONE)",
#         "bridge_name": "br-blackice",
#         "subnet_cidr": "10.204.5.0/24",
#         "gateway_ip": "10.204.5.1",
#         "dhcp_enabled": true,
#         "vlan_tag": null,
#         "ipv6_enabled": true,
#         "ipv6_subnet_cidr": "fd5a:6c14:e23b:10::/64",
#         "intra_bridge_isolation": true,  # not yet enforced — see below
#         "reach_internet": true,
#         "reachable_networks": [],        # peer slugs (besides wan)
#         "services_access": true,         # DHCP / DNS / ICMP
#         "admin_ui_access": false,        # LuCI 80/443
#         "ssh_access": false              # dropbear 22
#       },
#       ...
#     ]
#   } }
#
# ── Safety contract ──────────────────────────────────────────────────
#
# Every section we write carries ``option slate_ctrl_managed '1'``.
# The orphan purger at the end matches strictly on that marker, so we
# NEVER touch sections we don't own — GL.iNet's lan/wan/guest/wgserver/
# ovpnserver stay sacred even if a catalog slug accidentally collides
# (in which case the pre-existing section is left alone and our upsert
# is skipped with a warning).
#
# ── Naming convention ───────────────────────────────────────────────
#
# Per network with slug=<S> (lowercase) and SLUG=<S> uppercased :
#
#   network.<S>_dev               device  →  bridge `br-<S>`
#   network.<S>                   interface (static / DHCPv6 PD)
#   dhcp.<S>                      dhcp pool
#   firewall.<S>                  zone (input=REJECT by default)
#   firewall.SC_FR_NET_<SLUG>_DHCP   rule udp 67-68         (services)
#   firewall.SC_FR_NET_<SLUG>_DNS    rule tcp/udp 53        (services)
#   firewall.SC_FR_NET_<SLUG>_ICMP   rule icmp echo-request (services)
#   firewall.SC_FR_NET_<SLUG>_LUCI   rule tcp 80,443        (admin_ui)
#   firewall.SC_FR_NET_<SLUG>_SSH    rule tcp 22            (ssh)
#   firewall.SC_FR_FWD_<SLUG>_TO_WAN   forwarding           (reach_internet)
#   firewall.SC_FR_FWD_<SLUG>_TO_<P>   forwarding           (reachable_networks)
#
# Section IDs must be in [A-Z0-9_]. Slugs go through `_slug_to_section`
# (replaces `-` with `_`, uppercases for the firewall side).
#
# ── intra_bridge_isolation (NOT YET ENFORCED) ────────────────────────
#
# This flag is preserved on the catalog row + passed through the JSON
# but the handler doesn't act on it yet. Real implementation needs
# either bridge port_isolation (kernel-level, requires per-port flag)
# or ebtables (extra package on the Slate). Both are non-trivial and
# orthogonal to the rest of the network state. Left as a v2 task.

_NET_MARK_KEY="slate_ctrl_managed"
_NET_MARK_VAL="1"

# /etc/init.d/ scripts we'll touch to apply changes. Cached so the
# reload phase doesn't re-scan every time.
_NET_INIT_NETWORK="/etc/init.d/network"
_NET_INIT_DNSMASQ="/etc/init.d/dnsmasq"
_NET_INIT_FIREWALL="/etc/init.d/firewall"

# Convert a kebab/lowercase slug to a UCI-safe section suffix
# (uppercase, underscores). "wg-server" → "WG_SERVER".
_net_slug_to_section() {
  echo "$1" | tr 'a-z-' 'A-Z_' | sed 's/[^A-Z0-9_]/_/g'
}

# Convert a /N prefix length to a dotted netmask. /24 → 255.255.255.0
_net_prefix_to_netmask() {
  local prefix="$1"
  local i mask=0
  local octets="0 0 0 0"
  local n=$prefix
  local out=""
  for i in 1 2 3 4; do
    if [ "$n" -ge 8 ]; then
      out="${out:+$out.}255"
      n=$((n - 8))
    elif [ "$n" -gt 0 ]; then
      local val
      val=$(( (256 - (1 << (8 - n))) ))
      out="${out:+$out.}$val"
      n=0
    else
      out="${out:+$out.}0"
    fi
  done
  echo "$out"
}

# Mark a section as ours so the orphan purger recognises it.
_net_mark_managed() {
  local sec="$1"  # e.g. firewall.blackice
  uci set "${sec}.${_NET_MARK_KEY}=${_NET_MARK_VAL}"
}

# Returns 0 if a UCI section is OURS (carries the marker), 1 otherwise.
_net_is_managed() {
  local sec="$1"
  local v
  v=$(uci -q get "${sec}.${_NET_MARK_KEY}" 2>/dev/null)
  [ "$v" = "$_NET_MARK_VAL" ]
}

# Refuse to upsert a section if it already exists and is NOT ours.
# Used to coexist with GL.iNet defaults : a catalog slug colliding
# with `lan` / `wan` / `guest` etc. is left alone, with a warning.
_net_can_own() {
  local sec="$1"
  if uci -q get "$sec" >/dev/null 2>&1; then
    _net_is_managed "$sec" && return 0
    return 1
  fi
  # Section doesn't exist yet — fair game, we'll create it.
  return 0
}

# Replace ALL `list <opt>` entries on a section with one fresh entry.
# Used for `network.<slug>.network=<...>` lists in zones / forwardings.
_net_set_list_single() {
  local sec="$1" opt="$2" value="$3"
  uci -q delete "${sec}.${opt}" 2>/dev/null
  uci add_list "${sec}.${opt}=${value}"
}


# ── Upsert helpers, one per UCI section flavour ──────────────────────

_net_upsert_bridge_device() {
  local slug="$1" bridge_name="$2"
  local sec="network.${slug}_dev"
  if ! _net_can_own "$sec"; then
    echo "network: refusing to overwrite non-managed $sec — skip bridge for '$slug'" >&2
    return 1
  fi
  uci -q get "$sec" >/dev/null 2>&1 || uci set "${sec}=device"
  uci set "${sec}.name=${bridge_name}"
  uci set "${sec}.type=bridge"
  # bridge_empty=1 : force netifd to instantiate the bridge even with
  # zero member ports. Without it, an empty bridge yields a NO_DEVICE
  # error and the interface never comes up — so the gateway IP + DHCP
  # pool can't bind until a wifi VAP joins. With it, the bridge exists
  # immediately (DOWN/NO-CARRIER), IP assigned, dnsmasq ready ; wifi
  # ifaces attach to it later via `option network`.
  uci set "${sec}.bridge_empty=1"
  _net_mark_managed "$sec"
  return 0
}

_net_upsert_interface() {
  local slug="$1" bridge_name="$2" cidr="$3" gateway="$4" ipv6_enabled="$5" ipv6_cidr="$6"
  local sec="network.${slug}"
  if ! _net_can_own "$sec"; then
    echo "network: refusing to overwrite non-managed $sec — skip interface for '$slug'" >&2
    return 1
  fi

  # Derive the netmask from the CIDR prefix length.
  local prefix
  prefix="${cidr#*/}"
  local netmask
  netmask=$(_net_prefix_to_netmask "$prefix")

  uci -q get "$sec" >/dev/null 2>&1 || uci set "${sec}=interface"
  uci set "${sec}.proto=static"
  uci set "${sec}.device=${bridge_name}"
  uci set "${sec}.ipaddr=${gateway}"
  uci set "${sec}.netmask=${netmask}"

  # IPv6 — static prefix when given, else SLAAC + Prefix Delegation
  # from WAN (ip6assign). The handler keeps both code paths cleanly
  # separated so toggling ipv6_enabled OFF actually un-configures v6.
  if [ "$ipv6_enabled" = "true" ]; then
    if [ -n "$ipv6_cidr" ]; then
      uci set "${sec}.ip6addr=${ipv6_cidr%%/*}1/${ipv6_cidr#*/}"
      uci -q delete "${sec}.ip6assign" 2>/dev/null
    else
      uci set "${sec}.ip6assign=60"
      uci -q delete "${sec}.ip6addr" 2>/dev/null
    fi
  else
    uci -q delete "${sec}.ip6assign" 2>/dev/null
    uci -q delete "${sec}.ip6addr" 2>/dev/null
  fi

  _net_mark_managed "$sec"
  return 0
}

_net_upsert_dhcp() {
  local slug="$1" dhcp_enabled="$2"
  local sec="dhcp.${slug}"
  if ! _net_can_own "$sec"; then
    echo "network: refusing to overwrite non-managed $sec — skip dhcp for '$slug'" >&2
    return 1
  fi
  uci -q get "$sec" >/dev/null 2>&1 || uci set "${sec}=dhcp"
  uci set "${sec}.interface=${slug}"
  uci set "${sec}.start=100"
  uci set "${sec}.limit=150"
  uci set "${sec}.leasetime=12h"
  if [ "$dhcp_enabled" = "true" ]; then
    uci -q delete "${sec}.ignore" 2>/dev/null
  else
    uci set "${sec}.ignore=1"
  fi
  _net_mark_managed "$sec"
  return 0
}

_net_upsert_firewall_zone() {
  local slug="$1"
  local sec="firewall.${slug}"
  if ! _net_can_own "$sec"; then
    echo "network: refusing to overwrite non-managed $sec — skip zone for '$slug'" >&2
    return 1
  fi
  uci -q get "$sec" >/dev/null 2>&1 || uci set "${sec}=zone"
  uci set "${sec}.name=${slug}"
  _net_set_list_single "$sec" "network" "$slug"
  # Strict default-deny on input — opened back up per-service by the
  # SC_FR_NET_<SLUG>_* rules generated below. output = ACCEPT so
  # clients can talk to the WAN once forwarding is allowed. forward
  # = REJECT means intra-zone forwarding is denied by default ; a
  # ``config forwarding`` ties this zone to others where needed.
  uci set "${sec}.input=REJECT"
  uci set "${sec}.output=ACCEPT"
  uci set "${sec}.forward=REJECT"
  _net_mark_managed "$sec"
  return 0
}

_net_upsert_rule() {
  # $1 = uppercase slug   $2 = service tag (DHCP/DNS/ICMP/LUCI/SSH)
  # $3 = zone (lowercase slug)   $4 = proto    $5 = dest_port (or "")
  # $6 = extra opt (e.g. icmp_type)
  local slug_up="$1" tag="$2" zone="$3" proto="$4" ports="$5" extra="$6"
  local sec="firewall.SC_FR_NET_${slug_up}_${tag}"
  uci -q get "$sec" >/dev/null 2>&1 || uci set "${sec}=rule"
  uci set "${sec}.name=SC_FR_NET_${slug_up}_${tag}"
  uci set "${sec}.enabled=1"
  uci set "${sec}.src=${zone}"
  uci set "${sec}.proto=${proto}"
  if [ -n "$ports" ]; then
    uci set "${sec}.dest_port=${ports}"
  else
    uci -q delete "${sec}.dest_port" 2>/dev/null
  fi
  if [ -n "$extra" ]; then
    # Used today for ICMP : "icmp_type echo-request" passed as a single
    # key-value pair via $6. Split on the first space.
    local k="${extra%% *}" v="${extra#* }"
    uci set "${sec}.${k}=${v}"
  fi
  uci set "${sec}.target=ACCEPT"
  _net_mark_managed "$sec"
}

_net_upsert_services_rules() {
  local slug="$1" slug_up="$2" services="$3" admin_ui="$4" ssh="$5"

  # Essential services. Without DHCP the clients can't get an IP at
  # all → "services_access=false" is mostly theoretical (you'd need to
  # hand-configure static IPs on every client). Still honored.
  if [ "$services" = "true" ]; then
    _net_upsert_rule "$slug_up" "DHCP" "$slug" "udp"     "67-68" ""
    _net_upsert_rule "$slug_up" "DNS"  "$slug" "tcp udp" "53"    ""
    _net_upsert_rule "$slug_up" "ICMP" "$slug" "icmp"    ""      "icmp_type echo-request"
  else
    # Toggle off : delete the rules if they exist (purge-style on
    # this trio specifically so the user can flip without restart).
    uci -q delete "firewall.SC_FR_NET_${slug_up}_DHCP" 2>/dev/null
    uci -q delete "firewall.SC_FR_NET_${slug_up}_DNS"  2>/dev/null
    uci -q delete "firewall.SC_FR_NET_${slug_up}_ICMP" 2>/dev/null
  fi

  if [ "$admin_ui" = "true" ]; then
    _net_upsert_rule "$slug_up" "LUCI" "$slug" "tcp" "80 443" ""
  else
    uci -q delete "firewall.SC_FR_NET_${slug_up}_LUCI" 2>/dev/null
  fi

  if [ "$ssh" = "true" ]; then
    _net_upsert_rule "$slug_up" "SSH" "$slug" "tcp" "22" ""
  else
    uci -q delete "firewall.SC_FR_NET_${slug_up}_SSH" 2>/dev/null
  fi
}

_net_upsert_forwarding() {
  local src_up="$1" dst_up="$2" src="$3" dst="$4"
  local sec="firewall.SC_FR_FWD_${src_up}_TO_${dst_up}"
  uci -q get "$sec" >/dev/null 2>&1 || uci set "${sec}=forwarding"
  uci set "${sec}.src=${src}"
  uci set "${sec}.dest=${dst}"
  _net_mark_managed "$sec"
}

_net_apply_forwardings() {
  local slug="$1" slug_up="$2" reach_inet="$3" peers_csv="$4"

  # Internet egress.
  if [ "$reach_inet" = "true" ]; then
    _net_upsert_forwarding "$slug_up" "WAN" "$slug" "wan"
  else
    uci -q delete "firewall.SC_FR_FWD_${slug_up}_TO_WAN" 2>/dev/null
  fi

  # Peer networks. The csv comes pre-trimmed from the JSON.
  local oldIFS="$IFS"
  IFS=','
  for peer in $peers_csv; do
    [ -z "$peer" ] && continue
    local peer_up
    peer_up=$(_net_slug_to_section "$peer")
    _net_upsert_forwarding "$slug_up" "$peer_up" "$slug" "$peer"
  done
  IFS="$oldIFS"
}


# ── Orphan purge ─────────────────────────────────────────────────────
#
# After we've upserted every catalog network, delete anything carrying
# our marker that isn't in the current set. This is what cleans up
# networks the user deleted from the controller catalog.
#
# We DON'T iterate the whole UCI config from scratch ; we only look at
# the namespaces we own (network/dhcp/firewall) and only at sections
# carrying our marker.

_net_orphan_purge_config() {
  # $1 = config name (network|dhcp|firewall)
  # $2 = newline-separated list of section names we just wrote (kept)
  local cfg="$1" kept="$2"
  local sec
  uci show "$cfg" 2>/dev/null \
    | grep "\\.${_NET_MARK_KEY}='${_NET_MARK_VAL}'\$" \
    | sed -e "s/\\.${_NET_MARK_KEY}=.*//" \
    | while read -r sec; do
        [ -z "$sec" ] && continue
        local short="${sec#${cfg}.}"
        # Skip if this section is in the kept-set.
        if ! echo "$kept" | grep -Fxq "$short"; then
          echo "network: orphan purge → uci delete $sec"
          uci -q delete "$sec" 2>/dev/null
        fi
      done
}


# ── Main reconciliation entry point ──────────────────────────────────

network_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local count
  count=$(echo "$payload" | jsonfilter -e '@.items[*].slug' 2>/dev/null | wc -l)
  if [ -z "$count" ] || [ "$count" -eq 0 ]; then
    echo "network: catalog empty — nothing to reconcile"
    # Still run the orphan purge below so a deleted-last-network
    # propagates as a removal.
  fi

  # We accumulate the names of sections we (re)wrote this run so the
  # orphan purger knows what to keep.
  local kept_network="" kept_dhcp="" kept_firewall=""
  local touched_network=0 touched_firewall=0 touched_dhcp=0

  local i=0
  while [ "$i" -lt "$count" ]; do
    local slug bridge cidr gw dhcp_en ipv6_en ipv6_cidr
    local reach_inet peers_csv services admin_ui ssh
    slug=$(echo "$payload"       | jsonfilter -e "@.items[$i].slug"                  2>/dev/null)
    bridge=$(echo "$payload"     | jsonfilter -e "@.items[$i].bridge_name"           2>/dev/null)
    cidr=$(echo "$payload"       | jsonfilter -e "@.items[$i].subnet_cidr"           2>/dev/null)
    gw=$(echo "$payload"         | jsonfilter -e "@.items[$i].gateway_ip"            2>/dev/null)
    dhcp_en=$(echo "$payload"    | jsonfilter -e "@.items[$i].dhcp_enabled"          2>/dev/null)
    ipv6_en=$(echo "$payload"    | jsonfilter -e "@.items[$i].ipv6_enabled"          2>/dev/null)
    ipv6_cidr=$(echo "$payload"  | jsonfilter -e "@.items[$i].ipv6_subnet_cidr"      2>/dev/null)
    reach_inet=$(echo "$payload" | jsonfilter -e "@.items[$i].reach_internet"        2>/dev/null)
    peers_csv=$(
      echo "$payload" | jsonfilter -e "@.items[$i].reachable_networks[*]" 2>/dev/null \
        | tr '\n' ',' | sed 's/,$//'
    )
    services=$(echo "$payload"   | jsonfilter -e "@.items[$i].services_access"       2>/dev/null)
    admin_ui=$(echo "$payload"   | jsonfilter -e "@.items[$i].admin_ui_access"       2>/dev/null)
    ssh=$(echo "$payload"        | jsonfilter -e "@.items[$i].ssh_access"            2>/dev/null)
    i=$((i + 1))

    if [ -z "$slug" ] || [ -z "$bridge" ] || [ -z "$cidr" ] || [ -z "$gw" ]; then
      echo "network: incomplete record for '$slug' — skip" >&2
      continue
    fi

    local slug_up
    slug_up=$(_net_slug_to_section "$slug")

    # network namespace
    if _net_upsert_bridge_device "$slug" "$bridge"; then
      kept_network="${kept_network}${slug}_dev
"
      touched_network=1
    fi
    if _net_upsert_interface "$slug" "$bridge" "$cidr" "$gw" "$ipv6_en" "$ipv6_cidr"; then
      kept_network="${kept_network}${slug}
"
      touched_network=1
    fi

    # dhcp namespace
    if _net_upsert_dhcp "$slug" "$dhcp_en"; then
      kept_dhcp="${kept_dhcp}${slug}
"
      touched_dhcp=1
    fi

    # firewall namespace : zone + service rules + forwardings
    if _net_upsert_firewall_zone "$slug"; then
      kept_firewall="${kept_firewall}${slug}
"
      touched_firewall=1
      _net_upsert_services_rules "$slug" "$slug_up" "$services" "$admin_ui" "$ssh"
      # Track every rule we just wrote so the orphan purger keeps them.
      [ "$services" = "true" ] && kept_firewall="${kept_firewall}SC_FR_NET_${slug_up}_DHCP
SC_FR_NET_${slug_up}_DNS
SC_FR_NET_${slug_up}_ICMP
"
      [ "$admin_ui" = "true" ] && kept_firewall="${kept_firewall}SC_FR_NET_${slug_up}_LUCI
"
      [ "$ssh" = "true" ] && kept_firewall="${kept_firewall}SC_FR_NET_${slug_up}_SSH
"
      _net_apply_forwardings "$slug" "$slug_up" "$reach_inet" "$peers_csv"
      [ "$reach_inet" = "true" ] && kept_firewall="${kept_firewall}SC_FR_FWD_${slug_up}_TO_WAN
"
      local oldIFS="$IFS"; IFS=','
      for peer in $peers_csv; do
        [ -z "$peer" ] && continue
        local peer_up
        peer_up=$(_net_slug_to_section "$peer")
        kept_firewall="${kept_firewall}SC_FR_FWD_${slug_up}_TO_${peer_up}
"
      done
      IFS="$oldIFS"
    fi
  done

  # Purge orphans in each namespace.
  _net_orphan_purge_config "network"  "$kept_network"
  _net_orphan_purge_config "dhcp"     "$kept_dhcp"
  _net_orphan_purge_config "firewall" "$kept_firewall"

  # Commit + reload only what actually changed. uci commit is cheap
  # but reload restarts services so we skip it when we can.
  local commits=0
  if [ -n "$(uci changes network 2>/dev/null)" ]; then
    uci commit network 2>&1 && commits=$((commits + 1))
  fi
  if [ -n "$(uci changes dhcp 2>/dev/null)" ]; then
    uci commit dhcp 2>&1 && commits=$((commits + 1))
  fi
  if [ -n "$(uci changes firewall 2>/dev/null)" ]; then
    uci commit firewall 2>&1 && commits=$((commits + 1))
  fi

  if [ "$commits" -eq 0 ]; then
    echo "network: nothing changed, no reload needed"
    return 0
  fi

  # Reload sequence : network first (bridges come up), then dnsmasq
  # (so the new DHCP pool starts serving), then firewall (rules
  # reference the new zones, has to run last).
  if [ -x "$_NET_INIT_NETWORK" ]; then
    "$_NET_INIT_NETWORK" reload 2>&1 || echo "network: $_NET_INIT_NETWORK reload returned non-zero" >&2
  fi
  if [ -x "$_NET_INIT_DNSMASQ" ]; then
    "$_NET_INIT_DNSMASQ" restart 2>&1 || echo "network: $_NET_INIT_DNSMASQ restart returned non-zero" >&2
  fi
  if command -v fw3 >/dev/null 2>&1; then
    fw3 reload 2>&1 || echo "network: fw3 reload returned non-zero" >&2
  elif [ -x "$_NET_INIT_FIREWALL" ]; then
    "$_NET_INIT_FIREWALL" reload 2>&1 || echo "network: $_NET_INIT_FIREWALL reload returned non-zero" >&2
  fi

  echo "network: reconcile done ($commits namespace(s) committed)"
  return 0
}

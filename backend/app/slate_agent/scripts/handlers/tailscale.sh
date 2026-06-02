# tailscale.sh — slate-controller Tailscale subsystem handler.
#
# Sourced by slate-ctrl. Receives the `tailscale` block of the profile JSON
# via stdin and applies it. Idempotent: calling apply with the same input
# twice is a no-op except for re-emitted log lines.
#
# Profile JSON schema (tailscale block):
#   {
#     "enabled": bool,
#     "connection": {
#        "accept_routes": bool|null,
#        "accept_dns":    bool|null,
#        "exit_node":     "<host-or-empty>"|null,
#        "shields_up":    bool|null,
#        "advertise_routes":     ["10.0.0.0/24",...]|null,
#        "advertise_exit_node":  bool|null
#     } | null,
#     "admin_only":      bool,            // restrict admin surface
#     "admin_ips":       ["100.64.0.5"],  // whitelist of tailnet peers
#     "admin_ports_tcp": [22, 80, 443],   // TCP ports to guard
#     "ha": { ... } | null   // HA watchdog stays controller-owned for now
#   }
#
# None / null = "leave the field as it is — don't touch". Only set values
# get translated into `tailscale set` flags.
#
# admin_only firewall : when true (and `admin_ips` is non-empty), the
# handler generates UCI firewall rules named `SC_FR_TS_ADMIN_*` that
# accept TCP traffic from each admin IP to the Slate's admin ports, then
# reject everything else inbound on the tailnet CGNAT range 100.64.0.0/10
# matching those ports. Tailnet peers can still use the Slate as a router
# / subnet route gateway — only the admin plane is locked down.

# Tailnet CGNAT range — every tailnet peer's IPv4 falls in here.
# IPv6 tailnet addresses (fd7a:115c:a1e0::/48) get their own rules
# when/if we extend to IPv6 — out of scope for v1.
_TS_TAILNET_CIDR="100.64.0.0/10"

# Purge every managed `SC_FR_TS_ADMIN_*` rule from /etc/config/firewall.
# Called unconditionally at the start of every apply so the rule set is
# regenerated from the JSON payload — deletion of an admin IP in
# Settings propagates without leaving stale rules behind.
_ts_admin_firewall_purge() {
  local sec
  uci show firewall 2>/dev/null \
    | grep -oE '^firewall\.SC_FR_TS_ADMIN_[^=]+=' \
    | sed 's/=$//' \
    | sort -u \
    | while read -r sec; do
        [ -n "$sec" ] && uci -q delete "$sec" 2>/dev/null
      done
}

# Build the firewall rules from the admin payload. Returns 0 if any
# changes were applied (caller will commit+reload), 1 on error.
#
# $1 admin_only_flag ("true" | "false" | "")
# $2 admin_ips_csv   ("100.64.0.5,100.64.0.12")
# $3 ports_csv       ("22,80,443,3000,8000")
_ts_admin_firewall_apply() {
  local flag="$1" ips_csv="$2" ports_csv="$3"

  # Always purge first — idempotent reset. If the flag is OFF we stop
  # right after the purge so the rules just disappear.
  _ts_admin_firewall_purge

  if [ "$flag" != "true" ]; then
    echo "tailscale: admin_only OFF — SC_FR_TS_ADMIN_* rules purged"
    return 0
  fi

  if [ -z "$ips_csv" ]; then
    # Safety net : admin_only=true with an empty whitelist would lock
    # the user out of the Slate via the tailnet. The controller-side
    # sync layer already downgrades this case to OFF, so we shouldn't
    # see it here ; warn and bail just in case.
    echo "tailscale: admin_only=true but no admin_ips — refusing self-DoS" >&2
    return 0
  fi

  # UCI accepts space-separated port lists in `option dest_port`.
  local ports_uci
  ports_uci=$(echo "$ports_csv" | tr ',' ' ')

  # One ACCEPT rule per admin IP. Numeric suffix keeps section names
  # ≤ 32 chars and grep-able in LuCI.
  local idx=0
  local oldIFS="$IFS"
  IFS=','
  for ip in $ips_csv; do
    [ -z "$ip" ] && continue
    # Slugify the IP for the section suffix : replace dots/colons with
    # underscores so the UCI section id stays in [A-Z0-9_].
    local ip_slug
    ip_slug=$(echo "$ip" | tr '.:' '__' | tr -c 'A-Za-z0-9_' '_' | tr 'a-z' 'A-Z')
    local sec="SC_FR_TS_ADMIN_ALLOW_${ip_slug}"
    uci set "firewall.$sec=rule"
    uci set "firewall.$sec.name=$sec"
    uci set "firewall.$sec.enabled=1"
    uci set "firewall.$sec.proto=tcp"
    uci set "firewall.$sec.src=*"
    uci set "firewall.$sec.src_ip=$ip"
    uci set "firewall.$sec.dest_port=$ports_uci"
    uci set "firewall.$sec.target=ACCEPT"
    idx=$((idx + 1))
    echo "tailscale: admin allow $ip → tcp:[$ports_uci] ($sec)"
  done
  IFS="$oldIFS"

  # Catch-all REJECT for the tailnet range on the same ports. ORDER
  # matters in /etc/config/firewall : the upserts above wrote the
  # ACCEPT rules first (top of the section list), so iptables sees
  # them before this REJECT.
  local drop_sec="SC_FR_TS_ADMIN_DROP_ALL"
  uci set "firewall.$drop_sec=rule"
  uci set "firewall.$drop_sec.name=$drop_sec"
  uci set "firewall.$drop_sec.enabled=1"
  uci set "firewall.$drop_sec.proto=tcp"
  uci set "firewall.$drop_sec.src=*"
  uci set "firewall.$drop_sec.src_ip=$_TS_TAILNET_CIDR"
  uci set "firewall.$drop_sec.dest_port=$ports_uci"
  uci set "firewall.$drop_sec.target=REJECT"
  echo "tailscale: admin reject tailnet→tcp:[$ports_uci] ($drop_sec)"
  return 0
}

tailscale_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local TS=/usr/sbin/tailscale
  [ -x "$TS" ] || TS=$(which tailscale 2>/dev/null)
  if [ -z "$TS" ] || [ ! -x "$TS" ]; then
    echo "tailscale binary not found" >&2
    return 1
  fi

  # 1. enabled toggle. true → up, false → down (preserving identity).
  local enabled
  enabled=$(echo "$payload" | jsonfilter -e '@.enabled' 2>/dev/null)
  if [ "$enabled" = "false" ]; then
    local backend
    backend=$($TS status --json 2>/dev/null | jsonfilter -e '@.BackendState' 2>/dev/null)
    if [ "$backend" = "Running" ]; then
      # --accept-risk=lose-ssh : le daemon refuse `down` par défaut quand
      # la session courante (SSH ou apply slate-ctrl) passe par Tailscale.
      # Le profile a été appliqué intentionnellement → on accepte le risque.
      $TS down --accept-risk=lose-ssh 2>&1 || return 1
      echo "tailscale down"
    else
      echo "tailscale already down (backend=$backend)"
    fi
    return 0
  fi

  if [ "$enabled" != "true" ]; then
    # Field absent: leave the daemon state alone.
    :
  else
    # enabled = true → ensure the daemon is up. If it isn't, we can't bring
    # it up here without an auth key; surface that to the operator.
    local backend
    backend=$($TS status --json 2>/dev/null | jsonfilter -e '@.BackendState' 2>/dev/null)
    if [ "$backend" != "Running" ]; then
      echo "WARN: tailscale enabled=true but backend=$backend (login required, apply skipped)" >&2
      return 0
    fi
  fi

  # 2. Connection overrides → translate to `tailscale set` flags.
  local conn
  conn=$(echo "$payload" | jsonfilter -e '@.connection' 2>/dev/null)
  if [ -z "$conn" ] || [ "$conn" = "null" ]; then
    return 0
  fi

  # Pull each override. jsonfilter prints an empty string for missing keys,
  # so we use `null` as our explicit "absent" marker by checking with -e
  # against the parent first.
  local accept_routes accept_dns shields_up advertise_exit_node exit_node advertise_routes
  accept_routes=$(echo "$conn" | jsonfilter -e '@.accept_routes' 2>/dev/null)
  accept_dns=$(echo "$conn" | jsonfilter -e '@.accept_dns' 2>/dev/null)
  shields_up=$(echo "$conn" | jsonfilter -e '@.shields_up' 2>/dev/null)
  advertise_exit_node=$(echo "$conn" | jsonfilter -e '@.advertise_exit_node' 2>/dev/null)
  exit_node=$(echo "$conn" | jsonfilter -e '@.exit_node' 2>/dev/null)
  # advertise_routes is a list — jsonfilter -e @.x[*] gives one per line.
  advertise_routes=$(echo "$conn" | jsonfilter -e '@.advertise_routes[*]' 2>/dev/null | tr '\n' ',' | sed 's/,$//')

  local args=""
  [ "$accept_routes"       = "true" ]  && args="$args --accept-routes=true"
  [ "$accept_routes"       = "false" ] && args="$args --accept-routes=false"
  [ "$accept_dns"          = "true" ]  && args="$args --accept-dns=true"
  [ "$accept_dns"          = "false" ] && args="$args --accept-dns=false"
  [ "$shields_up"          = "true" ]  && args="$args --shields-up=true"
  [ "$shields_up"          = "false" ] && args="$args --shields-up=false"
  [ "$advertise_exit_node" = "true" ]  && args="$args --advertise-exit-node=true"
  [ "$advertise_exit_node" = "false" ] && args="$args --advertise-exit-node=false"

  # exit_node: "" means "disable exit-node use". A non-empty string is a
  # host name. jsonfilter returns empty for both null AND empty string;
  # distinguish by re-checking with a key probe.
  local exit_node_key
  exit_node_key=$(echo "$conn" | jsonfilter -t '@.exit_node' 2>/dev/null)
  if [ -n "$exit_node_key" ] && [ "$exit_node_key" != "null" ]; then
    args="$args --exit-node=$exit_node"
  fi

  # advertise_routes : always emitted by the controller (computed from
  # the network catalog ; networks with `expose_to_tailnet=true` are in,
  # everything else is out). We pass the flag every time — empty value
  # means "clear advertised routes" so toggling a network OFF actually
  # un-advertises it next sync. The key-probe distinguishes "field
  # missing" (legacy payload — don't touch) from "field present but
  # empty list" (modern payload, intentional clear).
  local routes_key
  routes_key=$(echo "$conn" | jsonfilter -t '@.advertise_routes' 2>/dev/null)
  if [ -n "$routes_key" ] && [ "$routes_key" != "null" ]; then
    args="$args --advertise-routes=$advertise_routes"
  fi

  # 3. Admin-only firewall rules. Read regardless of whether `args` is
  # empty — the firewall apply is orthogonal to `tailscale set`.
  local admin_only admin_ips_csv ports_csv
  admin_only=$(echo "$payload" | jsonfilter -e '@.admin_only' 2>/dev/null)
  admin_ips_csv=$(
    echo "$payload" | jsonfilter -e '@.admin_ips[*]' 2>/dev/null \
      | tr '\n' ',' | sed 's/,$//'
  )
  ports_csv=$(
    echo "$payload" | jsonfilter -e '@.admin_ports_tcp[*]' 2>/dev/null \
      | tr '\n' ',' | sed 's/,$//'
  )
  if _ts_admin_firewall_apply "$admin_only" "$admin_ips_csv" "$ports_csv"; then
    # Commit the firewall changes only if there's actually a rule set
    # to push or remove (the purge alone is enough to require commit
    # when stale rules existed). `uci changes firewall` returns non-empty
    # iff there's pending state.
    if [ -n "$(uci changes firewall 2>/dev/null)" ]; then
      uci commit firewall 2>&1 || {
        echo "tailscale: uci commit firewall failed" >&2
        return 1
      }
      # fw3 reload : faster than `/etc/init.d/firewall restart`, no
      # iptables flush of unrelated chains.
      if command -v fw3 >/dev/null 2>&1; then
        fw3 reload 2>&1 || echo "tailscale: fw3 reload returned non-zero" >&2
      else
        /etc/init.d/firewall reload 2>&1 \
          || echo "tailscale: firewall reload returned non-zero" >&2
      fi
    fi
  fi

  if [ -z "$args" ]; then
    echo "tailscale set: nothing to apply"
    return 0
  fi

  # --accept-risk=lose-ssh : certains flags (--exit-node clear, changement
  # d'advertise-routes qui retire la route que la session emprunte) peuvent
  # déclencher le même warning. Profile-driven → on accepte.
  # shellcheck disable=SC2086
  if $TS set --accept-risk=lose-ssh $args 2>&1; then
    echo "tailscale set:$args"
  else
    echo "tailscale set FAILED:$args" >&2
    return 1
  fi
}

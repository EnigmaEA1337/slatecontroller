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
#     "ha": { ... } | null   // HA watchdog stays controller-owned for now
#   }
#
# None / null = "leave the field as it is — don't touch". Only set values
# get translated into `tailscale set` flags.

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
      $TS down 2>&1 || return 1
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

  if [ -n "$advertise_routes" ]; then
    args="$args --advertise-routes=$advertise_routes"
  fi

  if [ -z "$args" ]; then
    echo "tailscale set: nothing to apply"
    return 0
  fi

  # shellcheck disable=SC2086
  if $TS set $args 2>&1; then
    echo "tailscale set:$args"
  else
    echo "tailscale set FAILED:$args" >&2
    return 1
  fi
}

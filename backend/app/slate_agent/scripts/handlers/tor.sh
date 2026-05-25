# tor.sh — slate-controller Tor subsystem handler.
#
# Toggles the local Tor relay/client according to the profile. Tor is NOT
# part of the stock GL.iNet firmware — it needs `opkg install tor` (and
# typically `tor-geoipdb` for country-aware exit selection). When tor is
# absent on the box, the handler logs a clear "not installed" line rather
# than failing the whole apply.
#
# Profile JSON shape (tor block):
#   {
#     "enabled": bool,   // start/stop the tor daemon
#     "bridge":  bool    // use bridges (obfs4) instead of direct connection
#                        // — needed in jurisdictions that block Tor's
#                        // public guards (CN, IR, RU, TR…).
#   }
#
# Bridge support :
#   Requires obfs4proxy + a Bridge line in /etc/tor/torrc. We don't ship
#   bridges with the handler (they're per-deployment + rotate frequently);
#   `bridge=true` only flips the UseBridges flag and logs a notice if no
#   Bridge lines are present in torrc.

tor_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local enabled bridge
  enabled=$(echo "$payload" | jsonfilter -e '@.enabled' 2>/dev/null)
  bridge=$(echo "$payload" | jsonfilter -e '@.bridge' 2>/dev/null)

  if [ -z "$enabled" ]; then
    echo "tor: 'enabled' field absent, nothing to do"
    return 0
  fi

  # --- presence check ---------------------------------------------------
  if [ ! -x /etc/init.d/tor ] && ! command -v tor >/dev/null 2>&1; then
    if [ "$enabled" = "true" ]; then
      echo "tor: requested ON but tor not installed — run 'opkg install tor tor-geoipdb' on the Slate first" >&2
      return 1
    fi
    # enabled=false + no tor = consistent, nothing to do
    echo "tor: not installed (consistent with profile enabled=false)"
    return 0
  fi

  # --- bridge toggle (only meaningful if enabled=true) ------------------
  if [ -f /etc/tor/torrc ]; then
    if [ "$bridge" = "true" ]; then
      # Flip UseBridges to 1. If commented out, uncomment.
      if grep -q "^UseBridges" /etc/tor/torrc; then
        sed -i 's/^UseBridges.*$/UseBridges 1/' /etc/tor/torrc
      elif grep -q "^#UseBridges" /etc/tor/torrc; then
        sed -i 's/^#UseBridges.*$/UseBridges 1/' /etc/tor/torrc
      else
        echo "UseBridges 1" >> /etc/tor/torrc
      fi
      # Warn if no Bridge lines are present — UseBridges alone is useless
      # without at least one Bridge.
      if ! grep -qE "^Bridge\s" /etc/tor/torrc; then
        echo "tor: UseBridges=1 set BUT no 'Bridge <transport> <addr:port> <fingerprint>' line in /etc/tor/torrc — fetch bridges from https://bridges.torproject.org/ and append" >&2
      fi
    elif [ "$bridge" = "false" ]; then
      if grep -q "^UseBridges" /etc/tor/torrc; then
        sed -i 's/^UseBridges.*$/UseBridges 0/' /etc/tor/torrc
      fi
    fi
  fi

  # --- enable/disable + start/stop --------------------------------------
  if [ "$enabled" = "true" ]; then
    /etc/init.d/tor enable >/dev/null 2>&1
    if /etc/init.d/tor status 2>/dev/null | grep -q running; then
      # If we touched torrc above, restart instead of skip so the change
      # actually applies.
      if [ "$bridge" = "true" ] || [ "$bridge" = "false" ]; then
        /etc/init.d/tor restart >/dev/null 2>&1 && \
          echo "tor: running, restarted to pick up bridge=$bridge config"
      else
        echo "tor: already running"
      fi
    else
      if /etc/init.d/tor start 2>&1; then
        echo "tor: started + enabled at boot${bridge:+ (bridges=$bridge)}"
      else
        echo "tor: start failed (check 'logread | grep tor' for details)" >&2
        return 1
      fi
    fi
  elif [ "$enabled" = "false" ]; then
    /etc/init.d/tor stop >/dev/null 2>&1
    /etc/init.d/tor disable >/dev/null 2>&1
    echo "tor: stopped + disabled at boot"
  else
    echo "tor: 'enabled' must be true|false, got '$enabled'" >&2
    return 1
  fi

  return 0
}

# screen.sh — fb takeover for the "loading profile" status message.
#
# Runs unconditionally on every `slate-ctrl apply` — doesn't need a JSON
# block. Reads the active profile name from $SLATE_CTRL_PROFILE_NAME
# (exported by the dispatcher), looks up a pre-rendered RGB565 raw at
# /etc/slate-controller/screens/loading_<profile>.raw, and shows it on
# the panel via direct framebuffer write.
#
# Why a pre-rendered raw and not a PNG: rendering a PNG on the Slate
# would need a PIL-equivalent + TTF + ~200ms CPU. The controller already
# has Pillow + the OEM TTFs cached, so we render once at sync time and
# push the raw 240×320 RGB565 bytes (153600 B). The handler just
# `cat`s them onto /dev/fb0.
#
# Runs async (backgrounded) so the rest of the slate-ctrl apply chain
# (tailscale, dns, …) executes IN PARALLEL with the visible message.
# Hold duration = 4 s; that covers a typical apply.

screen_apply() {
  # Consume stdin so the dispatcher's pipe doesn't break. We don't use
  # the JSON block — context comes from $SLATE_CTRL_PROFILE_NAME.
  cat > /dev/null

  local profile_name="${SLATE_CTRL_PROFILE_NAME:-}"
  if [ -z "$profile_name" ]; then
    echo "screen: SLATE_CTRL_PROFILE_NAME unset, skipping"
    return 0
  fi

  local screens_dir="/etc/slate-controller/screens"
  local raw="${screens_dir}/loading_${profile_name}.raw"

  if [ ! -f "$raw" ]; then
    echo "screen: no loading raw at $raw, skipping"
    return 0
  fi

  # Fire the takeover loop in the background so the rest of the apply
  # chain runs in parallel. The loop is ~4 s of "kill gl_screen +
  # rewrite /dev/fb0 every 120 ms" — same recipe the controller uses,
  # busybox-compatible.
  (
    # Stop the daemon first via procd. Without this, procd will keep
    # respawning gl_screen aggressively (nice -20, respawn 1 5 -1) and
    # racing our cat-to-fb writes.
    /etc/init.d/gl_screen stop 2>/dev/null

    end=$(( $(date +%s) + 4 ))
    while [ "$(date +%s)" -lt "$end" ]; do
      # busybox has killall but NOT pkill — verified on this firmware.
      killall -9 gl_screen 2>/dev/null
      cat "$raw" > /dev/fb0 2>/dev/null
      usleep 120000
    done

    # Restart the daemon — it picks up whatever wallpapers are now on
    # disk (a sibling handler may have updated them mid-apply).
    /etc/init.d/gl_screen start 2>/dev/null
  ) &

  # Disown so the background loop survives this script's exit.
  # ash's `disown` may not exist; redirecting the child's stdin works
  # as a fallback to detach it from the parent.
  disown 2>/dev/null || true

  echo "screen: takeover started for $profile_name (4s, background)"
  return 0
}

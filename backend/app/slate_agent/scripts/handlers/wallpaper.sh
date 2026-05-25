# wallpaper.sh — slate-controller wallpaper subsystem handler.
#
# Copies the pre-rendered home + lock wallpaper PNGs from the agent's
# cache (/etc/slate-controller/wallpapers/) into the gl_screen daemon's
# expected paths, then nudges gl_screen to pick them up.
#
# The PNGs are produced and pushed by the controller's sync step (cf
# `app/slate_agent/sync.py::sync_profile_wallpapers`). The reasoning for
# the split :
#   - The controller has Pillow + the source blobs + fit_mode logic ; it
#     handles the resize once, when the user uploads.
#   - The agent runs ON the Slate (busybox + no Python) ; it only does
#     a file copy + service reload, which is all it needs.
#
# Profile JSON shape (wallpaper block — added by `add_wallpaper_block`
# in sync.py) :
#   {
#     "home": bool,   // a home wallpaper PNG exists in the cache
#     "lock": bool    // same for lock-screen wallpaper
#   }
#
# Cache layout on the Slate :
#   /etc/slate-controller/wallpapers/<profile>_home.png
#   /etc/slate-controller/wallpapers/<profile>_lock.png
#
# gl_screen target paths (verified via `strings /usr/bin/gl_screen | grep
# wallpaper` ; we write to ALL of them because the daemon's expected name
# varies by firmware version) :
#   home  →  /etc/gl_screen/wallpaper_home.png
#            /etc/gl_screen/image/wallpaper.png
#            /etc/gl_screen/image/wallpaper_home_style_default.png
#   lock  →  /etc/gl_screen/wallpaper_wake_display.png
#            /etc/gl_screen/image/wallpaper_wake_display_style1.png
#            /etc/gl_screen/image/wallpaper_wake_display_style2.png

WALLPAPER_CACHE="/etc/slate-controller/wallpapers"

# Target paths per kind. Space-separated so we can iterate easily.
WALLPAPER_HOME_TARGETS="/etc/gl_screen/wallpaper_home.png /etc/gl_screen/image/wallpaper.png /etc/gl_screen/image/wallpaper_home_style_default.png"
WALLPAPER_LOCK_TARGETS="/etc/gl_screen/wallpaper_wake_display.png /etc/gl_screen/image/wallpaper_wake_display_style1.png /etc/gl_screen/image/wallpaper_wake_display_style2.png"

wallpaper_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local profile_name="${SLATE_CTRL_PROFILE_NAME:-}"
  if [ -z "$profile_name" ]; then
    echo "wallpaper: SLATE_CTRL_PROFILE_NAME unset (dispatcher should export it)" >&2
    return 1
  fi

  local has_home has_lock
  has_home=$(echo "$payload" | jsonfilter -e '@.home' 2>/dev/null)
  has_lock=$(echo "$payload" | jsonfilter -e '@.lock' 2>/dev/null)

  local applied=0
  local skipped=0
  local errors=0

  # --- home ---
  if [ "$has_home" = "true" ]; then
    if _wallpaper_apply_kind "home" "$profile_name" "$WALLPAPER_HOME_TARGETS"; then
      applied=$((applied + 1))
    else
      errors=$((errors + 1))
    fi
  else
    skipped=$((skipped + 1))
  fi

  # --- lock ---
  if [ "$has_lock" = "true" ]; then
    if _wallpaper_apply_kind "lock" "$profile_name" "$WALLPAPER_LOCK_TARGETS"; then
      applied=$((applied + 1))
    else
      errors=$((errors + 1))
    fi
  else
    skipped=$((skipped + 1))
  fi

  # If we copied anything, nudge gl_screen so the new files are read on
  # the next wake/redraw. Backgrounded with a tiny delay so the apply
  # chain returns first — gl_screen restart can take 1-2s and we don't
  # want the dispatcher hanging on it.
  if [ "$applied" -gt 0 ]; then
    (sleep 1 && /etc/init.d/gl_screen reload >/dev/null 2>&1) &
    echo "wallpaper: applied=$applied skipped=$skipped errors=$errors (gl_screen reload scheduled)"
  else
    echo "wallpaper: nothing to apply (applied=0 skipped=$skipped errors=$errors)"
  fi

  [ "$errors" -gt 0 ] && return 1
  return 0
}

# Copy the cached PNG for (profile, kind) into every relevant gl_screen
# target path. Returns 0 on success, 1 if the source is missing or copy
# fails on every target.
_wallpaper_apply_kind() {
  local kind="$1"
  local profile_name="$2"
  local targets="$3"

  local src="${WALLPAPER_CACHE}/${profile_name}_${kind}.png"
  if [ ! -f "$src" ]; then
    echo "wallpaper: $kind cache miss ($src) — re-run sync from the controller" >&2
    return 1
  fi

  local target ok_count=0 fail_count=0
  for target in $targets; do
    # Ensure parent dir exists — image/ is usually there but be defensive.
    local target_dir
    target_dir=$(dirname "$target")
    mkdir -p "$target_dir" 2>/dev/null
    if cp "$src" "$target" 2>/dev/null; then
      ok_count=$((ok_count + 1))
    else
      fail_count=$((fail_count + 1))
      echo "wallpaper: $kind copy to $target failed" >&2
    fi
  done

  if [ "$ok_count" -eq 0 ]; then
    return 1
  fi
  echo "wallpaper: $kind '$profile_name' copied to $ok_count target(s)"
  return 0
}

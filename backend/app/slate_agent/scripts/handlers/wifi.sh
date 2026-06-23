# wifi.sh — slate-controller Wi-Fi handler (catalog-driven layout model).
#
# STRATEGY (rewritten 2026-06-02):
#   The on-disk layout is driven by the WiFi CATALOG, not by the active
#   profile. The controller now ships the full catalog in every apply
#   (every SSID, every band, every profile). The handler :
#     - Materializes one dedicated `SC_WL_<SLUG>_<BAND>` UCI section per
#       (slug, band) pair of every catalog SSID. Section is cloned from
#       the per-band OEM template on first contact (ra0/rai0/rax0), then
#       reused forever — same SSID always lives on the same VAP.
#     - Provisions LAYOUT fields (ssid / encryption / network / etc.)
#       once based on the catalog state. Subsequent re-applies with the
#       same catalog are uci no-ops.
#     - Flips `disabled` per (slug, band) according to the profile's
#       `enabled` choice on that SSID. Profile switches are LAYOUT-stable
#       → no `uci changes` outside `.disabled=` → no reboot.
#
# Reboot decision (the key knob, unchanged):
#   - Any uci change on a NON-disabled field → catalog changed → LAYOUT
#     pending → reboot required. The MTK mt7990 driver only re-reads
#     layout fields on boot reinit ; `wifi reload` doesn't apply them
#     and even kills global MTK daemons.
#   - Only `disabled` changed → just a profile switch → ip link toggle,
#     NO reboot.
#   - Nothing changed → no-op.
#
# Allocation model :
#   - Section name : SC_WL_<SLUG_ENV>_<BAND_SUFFIX>   (e.g. SC_WL_TRON_5)
#   - ifname/vifidx : lowest free index in <prefix><N> (N=0..15) where
#     prefix=ra/rai/rax. OEM sections (ra0/rai0/guest5g/mld0/...) occupy
#     the low slots ; our SC_WL_* sections take the next free indexes.
#   - Capacity : 16 BSSes per radio (driver max). Practical sweet spot
#     ~3-5 active per band before perf degrades — see memory
#     [[feedback-wifi-slot-pool]].
#
# Profile JSON shape (wifi block, enriched by the controller):
#   { "ssids": [ { "slug","name","bands":["2","5"],"mlo":false,
#                  "security","network_slug","client_isolation",
#                  "hidden","enabled" }, ... ] }
#   `ssids` is the FULL catalog (all SSIDs, every apply). `enabled` is
#   per-SSID and reflects the active profile's choice.

# ── Slot pools ───────────────────────────────────────────────────────

_wifi_regular_pool_for_band() {
  case "$1" in
    2|2g|24|2GHz|2.4GHz) echo "ra0 guest2g wlanmldguest2g" ;;
    5|5g|5GHz)           echo "rai0 guest5g wlanmldguest5g SC_WL_BLACKICE_5 SC_WL_NEXUS7_5" ;;
    6|6g|6GHz)           echo "rax0 guest6g wlanmldguest6g" ;;
    *)                   echo "" ;;
  esac
}

_wifi_mlo_section_for_band() {
  case "$1" in
    2|2g|24|2GHz|2.4GHz) echo "wlanmld2g" ;;
    5|5g|5GHz)           echo "wlanmld5g" ;;
    6|6g|6GHz)           echo "wlanmld6g" ;;
    *)                   echo "" ;;
  esac
}

# Every slot the handler may touch (the "disable unclaimed" sweep). Keep
# in sync with the pools above + the MLO link sections + the leftover MLD
# group mld1 (OEM artefact, never used here — explicitly disabled).
_WIFI_ALL_SLOTS="ra0 guest2g wlanmld2g wlanmldguest2g \
rai0 guest5g wlanmld5g wlanmldguest5g SC_WL_BLACKICE_5 SC_WL_NEXUS7_5 \
rax0 guest6g wlanmld6g wlanmldguest6g SC_WL_NEXUS7_6"

_WIFI_MARK="slate_ctrl_managed"

# Security → uci encryption value. Mirror of app/wifi/models.py.
_wifi_encryption_for_security() {
  case "$1" in
    WPA3-SAE|WPA3-PSK)              echo "sae" ;;
    WPA2-WPA3-Mixed|WPA3-SAE-MIXED) echo "sae-mixed" ;;
    WPA2-PSK)                        echo "psk2+ccmp" ;;
    open|none)                       echo "none" ;;
    *)                               echo "" ;;
  esac
}

# slug → wifi.env variable name. Mirror of `_slug_to_env` in deploy.py.
_wifi_slug_to_env() {
  echo "$1" | tr 'a-z-' 'A-Z_' | sed 's/[^A-Z0-9_]/_/g'
}

_wifi_psk_for_slug() {
  local env_name psk
  env_name=$(_wifi_slug_to_env "$1")
  eval "psk=\${WIFI_${env_name}_PSK:-}"
  echo "$psk"
}

# Fields we override after cloning so we never carry stale values from
# the OEM template (factory MAC, factory SSID, etc.).
_WIFI_OVERRIDE_FIELDS="ssid key encryption network disabled isolate hidden ifname factory_macaddr macaddr vifidx mld slate_ctrl_managed"

# ifname prefix + radio device + clone template per band. The MediaTek
# mtkwifi Lua driver crashes on `wifi reload` if a wifi-iface is missing
# any of ~60 vendor fields, so we always clone an existing OEM section.
_wifi_ifname_prefix_for_band() {
  case "$1" in
    2|2g|24|2GHz|2.4GHz) echo "ra"  ;;
    5|5g|5GHz)           echo "rai" ;;
    6|6g|6GHz)           echo "rax" ;;
    *) echo "" ;;
  esac
}
_wifi_template_for_band() {
  case "$1" in
    2|2g|24|2GHz|2.4GHz) echo "ra0"  ;;
    5|5g|5GHz)           echo "rai0" ;;
    6|6g|6GHz)           echo "rax0" ;;
    *) echo "" ;;
  esac
}

# Allocate the lowest free ifname index (0..15) for the given prefix,
# considering every wifi-iface section already in uci. Index 0 is
# typically the OEM main SSID, so practical allocations start at 4+.
#
# IMPORTANT — busybox sed quirks :
#   - `\+` is GNU-only, NOT supported by OpenWrt's busybox sed → use the
#     POSIX BRE quantifier `\{1,\}` instead.
#   - `-E` (ERE) IS supported on recent busybox but we stay BRE for
#     widest compat across firmware versions.
#   - Sub-expression `\(...\)` is BRE-standard, works everywhere.
# We bumped this once already (Bug F, 2026-06-02) — every new wifi-iface
# section ended up on ifname='${prefix}0' because the sed silently
# returned empty matches, leaving `used` empty.
_wifi_alloc_ifname_index() {
  local prefix="$1"
  # IFS guard : callers iterate bands with IFS=',' — without this reset,
  # the `for ln in $(...)` below would treat the whole newline-separated
  # pipeline output as ONE token (since ',' isn't in the output) →
  # `used` stays empty → allocator returns 0 → every new SC_WL_<SLUG>
  # section gets the OEM ifname → conflict (Bug F level 2, 2026-06-02).
  local _oldifs="$IFS"
  IFS='
 	'
  local used=" " ln idx
  for ln in $(uci show wireless 2>/dev/null \
              | grep "\\.ifname='${prefix}[0-9]" \
              | sed "s/.*ifname='${prefix}\\([0-9]\\{1,\\}\\)'.*/\\1/" \
              | sort -nu); do
    used="$used$ln "
  done
  IFS="$_oldifs"
  for idx in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    case "$used" in *" $idx "*) continue ;; esac
    echo "$idx"; return 0
  done
  echo ""; return 1
}

# Row-exclusive allocator : return the lowest index 0..15 that is NOT
# used by ANY ifname across ra/rai/rax. Gives "one row in the panel per
# SSID" — a 2.4 GHz lone SSID won't sit on a row already owned by an
# MLO group on 5/6 GHz. Same IFS guard as the per-prefix allocator.
_wifi_alloc_row_index() {
  local _oldifs="$IFS"
  IFS='
 	'
  local used=" " ln idx p
  for p in ra rai rax; do
    for ln in $(uci show wireless 2>/dev/null \
                | grep "\\.ifname='${p}[0-9]" \
                | sed "s/.*ifname='${p}\\([0-9]\\{1,\\}\\)'.*/\\1/" \
                | sort -nu); do
      used="$used$ln "
    done
  done
  IFS="$_oldifs"
  for idx in 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    case "$used" in *" $idx "*) continue ;; esac
    echo "$idx"; return 0
  done
  echo ""; return 1
}

# Echo the ifname index that the slug already uses on ANOTHER band, or
# empty. When we provision SC_WL_TRON_5 right after SC_WL_TRON_2 (same
# slug, different band), this lets us pin the same row index for both.
_wifi_index_for_slug_other_band() {
  local slug="$1"
  local slug_env
  slug_env=$(_wifi_slug_to_env "$slug")
  local ifn p
  for p in ra rai rax; do
    # Look at any SC_WL_<SLUG>_<B> section's ifname matching this prefix.
    ifn=$(uci show wireless 2>/dev/null \
          | grep "^wireless\\.SC_WL_${slug_env}_[0-9]\\+\\.ifname='${p}[0-9]" \
          | head -1 \
          | sed "s/.*ifname='${p}\\([0-9]\\{1,\\}\\)'.*/\\1/")
    [ -n "$ifn" ] && { echo "$ifn"; return 0; }
  done
  echo ""; return 1
}

# Ensure the dedicated SC_WL_<SLUG>_<BAND> section exists in uci. Reuse
# existing on hit ; clone from template + allocate ifname/vifidx on miss.
# Echoes the section name on success, empty on failure. Always uses the
# slug→env mapping so blackice/black-ice both produce BLACKICE.
_wifi_ensure_dedicated_slot() {
  local slug="$1" band="$2"
  local slug_env band_suf section template ifname_prefix device
  slug_env=$(_wifi_slug_to_env "$slug")
  case "$band" in
    2|2g|24|2GHz|2.4GHz) band_suf="2" ;;
    5|5g|5GHz)           band_suf="5" ;;
    6|6g|6GHz)           band_suf="6" ;;
    *) echo ""; return 1 ;;
  esac
  section="SC_WL_${slug_env}_${band_suf}"
  # 1. Already there → reuse (idempotent re-apply), but FIRST validate
  # that its ifname doesn't collide with another section's ifname. Bug F
  # caused a wave of sections to share ifname=<prefix>0 with the OEM
  # main iface — we self-heal by re-allocating + setting a fresh
  # ifname/vifidx if a collision is detected.
  if uci -q get "wireless.$section" >/dev/null 2>&1; then
    local cur_ifname collide
    cur_ifname=$(uci -q get "wireless.$section.ifname")
    if [ -n "$cur_ifname" ]; then
      # Count sections that have this exact ifname. We expect exactly 1
      # (ourselves). Anything more = collision.
      collide=$(uci show wireless 2>/dev/null \
                | grep "\\.ifname='${cur_ifname}'" \
                | wc -l)
      if [ "$collide" -le 1 ]; then
        echo "$section"; return 0
      fi
      echo "wifi: $section.ifname=$cur_ifname collides with another section — re-allocating" >&2
      # Fall through to the clone block's alloc logic to rebuild ifname
      # + vifidx. Drop the stale fields so the alloc sees them as free.
      uci -q delete "wireless.$section.ifname" 2>/dev/null
      uci -q delete "wireless.$section.vifidx" 2>/dev/null
    else
      # No ifname at all — re-allocate.
      :
    fi
    # Jump to alloc-only path (skip the full clone — the section's
    # cloned fields are already there).
    ifname_prefix=$(_wifi_ifname_prefix_for_band "$band")
    local idx alloc_ifname alloc_vifidx
    # Prefer the row index already used by this slug on another band
    # (alignment for multi-band SSIDs) ; fall back to row-exclusive
    # allocation otherwise (one row per SSID in the panel).
    idx=$(_wifi_index_for_slug_other_band "$slug")
    if [ -z "$idx" ]; then
      idx=$(_wifi_alloc_row_index)
    fi
    if [ -z "$idx" ]; then
      echo "wifi: all 16 row slots in use — cannot heal $section" >&2
      echo ""; return 1
    fi
    alloc_ifname="${ifname_prefix}${idx}"
    alloc_vifidx=$((idx + 1))
    uci set "wireless.$section.ifname=$alloc_ifname"
    uci set "wireless.$section.vifidx=$alloc_vifidx"
    echo "wifi: re-allocated $section ifname=$alloc_ifname vifidx=$alloc_vifidx (collision repair)" >&2
    echo "$section"; return 0
  fi
  # 2. Clone path.
  template=$(_wifi_template_for_band "$band")
  ifname_prefix=$(_wifi_ifname_prefix_for_band "$band")
  if ! uci -q get "wireless.$template" >/dev/null 2>&1; then
    echo "wifi: template wireless.$template missing on this firmware — cannot create $section" >&2
    echo ""; return 1
  fi
  device=$(uci -q get "wireless.$template.device" 2>/dev/null)
  if [ -z "$device" ]; then
    echo "wifi: template $template has no device field — cannot create $section" >&2
    echo ""; return 1
  fi
  local idx alloc_ifname alloc_vifidx
  # Multi-band alignment : if this slug already has a section on another
  # band, reuse that row index so the panel shows the slug aligned. Else
  # take the lowest GLOBALLY free row → one row per SSID, no visual
  # collision with MLO groups or other singles.
  idx=$(_wifi_index_for_slug_other_band "$slug")
  if [ -z "$idx" ]; then
    idx=$(_wifi_alloc_row_index)
  fi
  if [ -z "$idx" ]; then
    echo "wifi: all 16 row slots in use — cannot create $section" >&2
    echo ""; return 1
  fi
  alloc_ifname="${ifname_prefix}${idx}"
  alloc_vifidx=$((idx + 1))   # MTK wants 1-based vifidx
  # Create the section + clone every option except the override list.
  uci set "wireless.$section=wifi-iface"
  local ln opt_path opt_name opt_value skip skipfield
  uci show "wireless.$template" 2>/dev/null | while IFS= read -r ln; do
    opt_path="${ln%%=*}"
    opt_value="${ln#*=}"
    opt_name="${opt_path#wireless.${template}.}"
    [ "$opt_path" = "wireless.$template" ] && continue
    [ "$opt_name" = "$opt_path" ] && continue
    opt_value="${opt_value#\'}"
    opt_value="${opt_value%\'}"
    skip=0
    for skipfield in $_WIFI_OVERRIDE_FIELDS; do
      [ "$opt_name" = "$skipfield" ] && { skip=1; break; }
    done
    [ "$skip" = "1" ] && continue
    uci set "wireless.$section.$opt_name=$opt_value"
  done
  uci set "wireless.$section.device=$device"
  uci set "wireless.$section.mode=ap"
  uci set "wireless.$section.ifname=$alloc_ifname"
  uci set "wireless.$section.vifidx=$alloc_vifidx"
  echo "wifi: created dedicated $section ifname=$alloc_ifname vifidx=$alloc_vifidx (cloned from $template)" >&2
  echo "$section"
  return 0
}

# Enumerate every wifi-iface section name currently in uci. Used by the
# "disable unclaimed" sweep so new SC_WL_* sections are auto-included.
_wifi_all_iface_sections() {
  uci show wireless 2>/dev/null \
    | grep "=wifi-iface\$" \
    | sed 's/^wireless\.\([^=]*\)=wifi-iface$/\1/'
}

# Claimed-slot tracking (space-delimited with sentinels for word match).
_WIFI_CLAIMED=" "
_wifi_is_claimed() { case "$_WIFI_CLAIMED" in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
_wifi_claim() { _WIFI_CLAIMED="${_WIFI_CLAIMED}$1 "; }

# A "dedicated" slot is one named `SC_WL_<SLUG_ENV>_<BAND>` — created
# specifically for one SSID slug to guarantee idempotent slot mapping
# across re-applies (same slot = no driver re-init = no reboot needed).
# Dedicated slots are reserved : an unrelated SSID must NEVER consume one,
# otherwise the slug→slot mapping becomes order-dependent (sub-bug B').
_wifi_is_dedicated_slot() {
  case "$1" in SC_WL_*) return 0 ;; *) return 1 ;; esac
}

# Echo the dedicated slot for (slug, band) if one exists in the pool and
# in uci, else empty. Bands : 2/5/6 → suffix _2 / _5 / _6.
_wifi_dedicated_slot_for() {
  local slug="$1" band="$2"
  local slug_env band_suf cand
  slug_env=$(_wifi_slug_to_env "$slug")
  case "$band" in
    2|2g|24|2GHz|2.4GHz) band_suf="2" ;;
    5|5g|5GHz)           band_suf="5" ;;
    6|6g|6GHz)           band_suf="6" ;;
    *) echo ""; return 1 ;;
  esac
  cand="SC_WL_${slug_env}_${band_suf}"
  # Only return it if it's actually in the pool AND defined in uci. We
  # don't auto-create dedicated slots here — that's deploy-time concern.
  case " $(_wifi_regular_pool_for_band "$band") " in
    *" $cand "*) ;;
    *) echo ""; return 1 ;;
  esac
  uci -q get "wireless.$cand" >/dev/null 2>&1 || { echo ""; return 1; }
  echo "$cand"
}

# Echo the first unclaimed regular slot of a band for a given slug, or
# empty. Algorithm :
#   1. If a dedicated SC_WL_<SLUG>_<BAND> slot exists, use that (skip
#      claim check : it's reserved for this slug and nobody else should
#      ever have claimed it).
#   2. Otherwise, walk the regular pool but SKIP dedicated slots — they
#      belong to their owning slug only.
# Force IFS=' ' locally — callers iterate bands with IFS=','.
_wifi_next_free_regular() {
  local band="$1" slug="$2"
  local sec oldifs="$IFS"
  # 1. Dedicated slot first.
  if [ -n "$slug" ]; then
    sec=$(_wifi_dedicated_slot_for "$slug" "$band")
    if [ -n "$sec" ]; then
      if _wifi_is_claimed "$sec"; then
        echo "wifi: dedicated slot $sec already claimed — corrupt state, refusing" >&2
        echo ""; return 1
      fi
      echo "$sec"; return 0
    fi
  fi
  # 2. Generic pool, skipping any dedicated slot.
  IFS=' '
  for sec in $(_wifi_regular_pool_for_band "$band"); do
    if _wifi_is_dedicated_slot "$sec"; then continue; fi
    if ! _wifi_is_claimed "$sec"; then IFS="$oldifs"; echo "$sec"; return 0; fi
  done
  IFS="$oldifs"
  echo ""; return 1
}

# Fully de-bind a slot from any MLD group (drop its ifname from mld0/mld1
# iface lists + delete the slot's mld= field). Needed because regular
# slots that share the OEM mld pool would otherwise keep the group's ssid.
_wifi_unbind_from_mld() {
  local sec="$1" ifname
  ifname=$(uci -q get "wireless.$sec.ifname" 2>/dev/null)
  uci -q delete "wireless.$sec.mld" 2>/dev/null
  [ -z "$ifname" ] && return 0
  local grp
  for grp in mld0 mld1; do
    uci -q get "wireless.$grp.iface" >/dev/null 2>&1 || continue
    local cur new tok oldifs="$IFS"
    cur=$(uci -q get "wireless.$grp.iface")
    case " $cur " in *" $ifname "*) ;; *) continue ;; esac
    new=""
    IFS=' '
    for tok in $cur; do
      [ "$tok" = "$ifname" ] && continue
      new="${new:+$new }$tok"
    done
    IFS="$oldifs"
    uci set "wireless.$grp.iface=$new"
  done
}

# Set LAYOUT fields on a slot — ssid / encryption / key / network / mld /
# isolate / hidden + the managed mark. Does NOT touch `disabled` (that's
# the activation knob, handled separately). uci dedupes identical sets so
# this is naturally idempotent.
#   $1 section  $2 ssid  $3 key  $4 encryption  $5 network
#   $6 isolate(true/false)  $7 hidden(true/false)  $8 mld_group ("" = standalone)
_wifi_provision_slot() {
  local sec="$1" ssid="$2" key="$3" enc="$4" net="$5" iso="$6" hidden="$7" mldgrp="$8"
  if ! uci -q get "wireless.$sec" >/dev/null 2>&1; then
    echo "wifi: slot $sec missing on this firmware — skip" >&2
    return 1
  fi
  if ! uci -q get "network.$net" >/dev/null 2>&1; then
    echo "wifi: network 'network.$net' missing for SSID '$ssid' — skip" >&2
    return 1
  fi
  if [ -n "$mldgrp" ]; then
    uci set "wireless.$sec.mld=$mldgrp"
  else
    _wifi_unbind_from_mld "$sec"
  fi
  uci set "wireless.$sec.ssid=$ssid"
  uci set "wireless.$sec.encryption=$enc"
  if [ "$enc" != "none" ]; then
    uci set "wireless.$sec.key=$key"
  else
    uci -q delete "wireless.$sec.key" 2>/dev/null
  fi
  uci set "wireless.$sec.network=$net"
  if [ "$iso" = "true" ]; then
    uci set "wireless.$sec.isolate=1"
  else
    uci set "wireless.$sec.isolate=0"
  fi
  if [ "$hidden" = "true" ]; then
    uci set "wireless.$sec.hidden=1"
  else
    uci set "wireless.$sec.hidden=0"
  fi
  uci set "wireless.$sec.${_WIFI_MARK}=1"
  _wifi_claim "$sec"
  return 0
}

# Apply the 8 MTK advanced UCI options on a wifi-iface section. Each
# field is OPTIONAL — when empty/null we leave the existing value
# untouched (so SSIDs in old payloads keep their behaviour). Maps :
#   pmf         disabled→ieee80211w=0   optional→1   required→2
#   ft          true→ieee80211r=1       false→0
#   k           true→ieee80211k=1       false→0
#   v           true→ieee80211v=1       false→0
#   dtim        integer → dtim_period
#   wmm         true→wmm=1              false→0
#   parp        true→proxy_arp=1        false→0
#   wds         true→wds=1              false→0
#
# $1 section  $2 pmf  $3 ft  $4 k  $5 v  $6 dtim  $7 wmm  $8 parp  $9 wds
_wifi_apply_advanced() {
  local sec="$1" pmf="$2" ft="$3" k="$4" v="$5" dtim="$6" wmm="$7" parp="$8" wds="$9"
  case "$pmf" in
    disabled) uci set "wireless.$sec.ieee80211w=0" ;;
    optional) uci set "wireless.$sec.ieee80211w=1" ;;
    required) uci set "wireless.$sec.ieee80211w=2" ;;
  esac
  case "$ft" in
    true)  uci set "wireless.$sec.ieee80211r=1" ;;
    false) uci set "wireless.$sec.ieee80211r=0" ;;
  esac
  case "$k" in
    true)  uci set "wireless.$sec.ieee80211k=1" ;;
    false) uci set "wireless.$sec.ieee80211k=0" ;;
  esac
  case "$v" in
    true)  uci set "wireless.$sec.ieee80211v=1" ;;
    false) uci set "wireless.$sec.ieee80211v=0" ;;
  esac
  case "$dtim" in
    [1-9]|10) uci set "wireless.$sec.dtim_period=$dtim" ;;
  esac
  case "$wmm" in
    true)  uci set "wireless.$sec.wmm=1" ;;
    false) uci set "wireless.$sec.wmm=0" ;;
  esac
  case "$parp" in
    true)  uci set "wireless.$sec.proxy_arp=1" ;;
    false) uci set "wireless.$sec.proxy_arp=0" ;;
  esac
  case "$wds" in
    true)  uci set "wireless.$sec.wds=1" ;;
    false) uci set "wireless.$sec.wds=0" ;;
  esac
}


# Set the activation state in uci. Live effect is applied separately via
# ip link, after the commit/reboot decision.
_wifi_set_disabled() {
  local sec="$1" want="$2"   # want=0 (enabled) or 1 (disabled)
  uci -q get "wireless.$sec" >/dev/null 2>&1 || return 0
  uci set "wireless.$sec.disabled=$want"
}

# ip link state of an iface (UP / DOWN / missing). Reads /sys for speed
# and to avoid parsing iproute output.
_wifi_link_state() {
  local ifname="$1"
  if [ -f "/sys/class/net/$ifname/flags" ]; then
    local flags
    flags=$(cat "/sys/class/net/$ifname/flags")
    # 0x1 = IFF_UP. Use bash-free POSIX arithmetic.
    if [ $((flags & 1)) -ne 0 ]; then echo UP; else echo DOWN; fi
  else
    echo missing
  fi
}

# Current bridge master of an iface, or empty. /sys is faster than parsing
# `ip link` and we don't need a busybox dep.
_wifi_link_master() {
  local ifname="$1"
  if [ -L "/sys/class/net/$ifname/master" ]; then
    basename "$(readlink "/sys/class/net/$ifname/master")"
  else
    echo ""
  fi
}

# Toggle a VAP's beacon live, per-VAP (does NOT touch siblings or the
# uplink). On up, also ensures the iface is attached to its target bridge
# — `ip link set up` alone doesn't redo the bridge attachment that netifd
# does at boot, so a VAP that wasn't bridged stays UP-but-orphan and never
# beacons (the br-<net> stays NO-CARRIER). Idempotent : only acts if the
# current state differs.
#
# **MTK chip-level beacon engine** : on MT7990, plain ``ip link set down``
# only drops the netdev's TX/RX queue (visible via /proc/net/dev counters
# stuck at 0), it does NOT tell the chip's internal beacon table to stop
# broadcasting that BSS over the air. The canonical MTK way (seen in
# /lib/wifi/set_bcn.lua and /lib/wifi/mtwifi.lua used by /sbin/wifi) is
# ``mwctl <ifname> set no_bcn 0|1`` which flips the per-VAP beacon flag
# on the chip directly. We pair it with the link toggle so both layers
# agree :
#   - down  : mwctl no_bcn=1  +  ip link down
#   - up    : ip link up      +  mwctl no_bcn=0   (set BEFORE bridging so
#             beacons resume in sync with the link coming up)
# Failures are silently swallowed via ``|| true`` because mwctl is MTK-
# specific — running this script on a non-MTK build (mac80211 only) must
# stay a no-op there, not error out.
#   $1 ifname  $2 want (up|down)  $3 net_slug (empty → no bridging step)
_wifi_link_apply() {
  local ifname="$1" want="$2" net="$3"
  [ -z "$ifname" ] && return 0
  local cur
  cur=$(_wifi_link_state "$ifname")
  if [ "$want" = "down" ]; then
    # Stop chip beacons FIRST, then drop the netdev. Reversing the order
    # leaves a window where the chip still beacons a half-torn-down VAP.
    mwctl "$ifname" set no_bcn 1 2>/dev/null || true
    [ "$cur" = "UP" ] && ip link set "$ifname" down
  else  # want=up
    case "$cur" in
      missing) return 0 ;;
      DOWN) ip link set "$ifname" up ;;
    esac
    # Re-arm chip beacons AFTER the netdev is up so the driver finds the
    # iface in the expected state when it accepts the no_bcn=0 flip.
    mwctl "$ifname" set no_bcn 0 2>/dev/null || true
  fi
  if [ "$want" = "up" ] && [ -n "$net" ]; then
    local want_master="br-$net" cur_master
    cur_master=$(_wifi_link_master "$ifname")
    if [ "$cur_master" != "$want_master" ]; then
      if ip -o link show "$want_master" >/dev/null 2>&1; then
        ip link set "$ifname" master "$want_master" 2>/dev/null
      fi
    fi
  fi
}

# Fetch one JSON field for the i-th SSID. Cheap helper to avoid 8 inline
# jsonfilter calls per loop iteration.
_wifi_field() {
  local payload="$1" idx="$2" path="$3"
  echo "$payload" | jsonfilter -e "@.ssids[$idx].$path" 2>/dev/null
}

wifi_apply() {
  local payload
  payload=$(cat)
  if [ -z "$payload" ] || [ "$payload" = "null" ]; then
    return 0
  fi

  local count
  count=$(echo "$payload" | jsonfilter -e '@.ssids[*].slug' 2>/dev/null | wc -l)
  if [ -z "$count" ]; then count=0; fi

  # Source the PSK secrets once.
  local secrets_file="/etc/slate-controller/secrets/wifi.env"
  [ -f "$secrets_file" ] && . "$secrets_file"

  local errors=0

  # ── 1) Build (slug,index) pairs in payload order ─────────────────────
  # The controller's sync.py has already ordered the SSIDs (multi-band
  # non-MLO first, then alphabetical) so that multi-band SSIDs claim
  # the same low index on each of their bands → aligned slot rows in
  # the panel. We just preserve that order here ; no extra sort.
  local pairs=""
  local i=0
  while [ "$i" -lt "$count" ]; do
    local s
    s=$(_wifi_field "$payload" "$i" slug)
    [ -n "$s" ] && pairs="$pairs$s|$i
"
    i=$((i + 1))
  done
  local sorted
  # Strip blanks but preserve payload order — sync.py picked the right
  # multi-band-first ordering for slot-row alignment, re-sorting here
  # would undo that work.
  sorted=$(printf '%s' "$pairs" | grep -v '^$')

  # ── 2) Per-SSID provisioning (LAYOUT) + uci disabled (ACTIVATION) ────
  # Tracks (slug,ifname,want) tuples so we can ip-link-toggle later.
  local link_plan=""   # newline-separated "ifname:up|down"
  local mlo_ssid_present=0
  local mlo_iface_list=""

  # Iterate sorted lines : set IFS=newline once for the `for` expansion,
  # restore inside the body so inner string ops behave normally.
  local oldifs_lines="$IFS"
  IFS='
'
  for line in $sorted; do
    IFS="$oldifs_lines"
    [ -z "$line" ] && continue
    local slug idx
    slug="${line%%|*}"
    idx="${line##*|}"

    local name mlo enabled bands_csv security network isolate hidden
    name=$(_wifi_field "$payload" "$idx" name)
    mlo=$(_wifi_field "$payload" "$idx" mlo)
    enabled=$(_wifi_field "$payload" "$idx" enabled)
    bands_csv=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].bands[*]" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
    security=$(_wifi_field "$payload" "$idx" security)
    network=$(_wifi_field "$payload" "$idx" network_slug)
    isolate=$(_wifi_field "$payload" "$idx" client_isolation)
    hidden=$(_wifi_field "$payload" "$idx" hidden)
    # MTK advanced fields — every field has a safe default at the
    # controller, so missing values here just inherit the prior UCI
    # value (which is "off" on a fresh deploy thanks to the migration
    # server_default). Picked once per SSID and applied to every slot
    # it provisions below.
    local pmf ft k v dtim wmm parp wds
    pmf=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.pmf" 2>/dev/null)
    ft=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.ft_802_11r" 2>/dev/null)
    k=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.rrm_802_11k" 2>/dev/null)
    v=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.btm_802_11v" 2>/dev/null)
    dtim=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.dtim_period" 2>/dev/null)
    wmm=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.wmm" 2>/dev/null)
    parp=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.proxy_arp" 2>/dev/null)
    wds=$(echo "$payload" | jsonfilter -e "@.ssids[$idx].advanced.wds" 2>/dev/null)

    [ -z "$name" ] && { IFS='
'; continue; }

    local enc psk want_disabled
    enc=$(_wifi_encryption_for_security "$security")
    if [ -z "$enc" ]; then
      echo "wifi: '$name' unknown security '$security' — skip" >&2
      errors=$((errors + 1))
      continue
    fi
    psk=""
    if [ "$enc" != "none" ]; then
      psk=$(_wifi_psk_for_slug "$slug")
      if [ -z "$psk" ]; then
        echo "wifi: '$name' needs a PSK (WIFI_$(_wifi_slug_to_env "$slug")_PSK unset) — skip" >&2
        errors=$((errors + 1))
        continue
      fi
    fi
    if [ "$enabled" = "true" ]; then want_disabled=0; else want_disabled=1; fi

    if [ "$mlo" = "true" ]; then
      # MLO requires WPA3 (sae*).
      if [ "$enc" != "sae" ] && [ "$enc" != "sae-mixed" ]; then
        echo "wifi: '$name' MLO requires WPA3 — got '$enc', skip" >&2
        errors=$((errors + 1))
        continue
      fi
      # Provision each per-band MLD link section + join mld0.iface.
      local band ok=0
      local oldcsv="$IFS"; IFS=','
      for band in $bands_csv; do
        [ -z "$band" ] && continue
        local mlosec
        mlosec=$(_wifi_mlo_section_for_band "$band")
        [ -z "$mlosec" ] && continue
        if _wifi_provision_slot "$mlosec" "$name" "$psk" "$enc" "$network" "$isolate" "$hidden" "mld0"; then
          _wifi_apply_advanced "$mlosec" "$pmf" "$ft" "$k" "$v" "$dtim" "$wmm" "$parp" "$wds"
          local ifn
          ifn=$(uci -q get "wireless.$mlosec.ifname")
          mlo_iface_list="${mlo_iface_list:+$mlo_iface_list }$ifn"
          _wifi_set_disabled "$mlosec" "$want_disabled"
          link_plan="$link_plan$ifn:$([ "$want_disabled" = 0 ] && echo up || echo down):$network
"
          ok=$((ok + 1))
        fi
      done
      IFS="$oldcsv"
      if [ "$ok" -gt 0 ] && uci -q get "wireless.mld0" >/dev/null 2>&1; then
        # MLO clients (Wi-Fi 7) negotiate auth against the MLD group, not
        # the per-band link sections. So encryption + key MUST be set
        # here too — otherwise clients try the factory PSK that ships
        # in mld0.key and bounce with a SAE auth failure (the iPhone 13
        # / Wi-Fi 6 single-link symptom is even more silent : it sees the
        # 5 GHz link, tries the link's key, and the driver rejects
        # because it expects the MLD-level credential).
        uci set "wireless.mld0.ssid=$name"
        uci set "wireless.mld0.encryption=$enc"
        if [ "$enc" != "none" ]; then
          uci set "wireless.mld0.key=$psk"
        else
          uci -q delete "wireless.mld0.key" 2>/dev/null
        fi
        uci set "wireless.mld0.${_WIFI_MARK}=1"
        _wifi_set_disabled "mld0" "$want_disabled"
        # Claim mld0 so the sweep (step 4) keeps it instead of dropping
        # this MLO group entirely.
        _wifi_claim "mld0"
        mlo_ssid_present=1
        echo "wifi: MLO '$name' → mld0 ($bands_csv) net=$network enabled=$enabled"
      fi
    else
      # Regular SSID → one DEDICATED slot per band from the catalog.
      # The section is created from template on first contact, reused
      # forever after — guarantees a stable VAP per (slug, band) pair so
      # profile switches stay layout-stable.
      local band
      local oldcsv="$IFS"; IFS=','
      for band in $bands_csv; do
        [ -z "$band" ] && continue
        local sec
        sec=$(_wifi_ensure_dedicated_slot "$slug" "$band")
        if [ -z "$sec" ]; then
          echo "wifi: '$name' could not get dedicated slot on band $band — skip" >&2
          errors=$((errors + 1))
          continue
        fi
        if _wifi_provision_slot "$sec" "$name" "$psk" "$enc" "$network" "$isolate" "$hidden" ""; then
          _wifi_apply_advanced "$sec" "$pmf" "$ft" "$k" "$v" "$dtim" "$wmm" "$parp" "$wds"
          local ifn
          ifn=$(uci -q get "wireless.$sec.ifname")
          _wifi_set_disabled "$sec" "$want_disabled"
          link_plan="$link_plan$ifn:$([ "$want_disabled" = 0 ] && echo up || echo down):$network
"
          echo "wifi: '$name' → $sec/$ifn (band $band) net=$network enabled=$enabled"
        else
          errors=$((errors + 1))
        fi
      done
      IFS="$oldcsv"
    fi
  done
  IFS="$oldifs_lines"

  # ── 3) Reflect the MLO group iface list in uci. ──────────────────────
  # We set this explicitly to drop the OEM ra2 (2.4) entry — our MLO SSID
  # only uses 5+6.
  if [ "$mlo_ssid_present" = 1 ] && uci -q get "wireless.mld0" >/dev/null 2>&1; then
    uci set "wireless.mld0.iface=$mlo_iface_list"
  elif uci -q get "wireless.mld0" >/dev/null 2>&1; then
    # No MLO SSID this catalog → disable + empty list.
    _wifi_set_disabled mld0 1
    uci set "wireless.mld0.iface="
  fi

  # mld1 is the OEM "MLO-Guest" group — never used here. Force off + clear
  # iface list. One-time layout change.
  if uci -q get "wireless.mld1" >/dev/null 2>&1; then
    _wifi_set_disabled mld1 1
    uci set "wireless.mld1.iface="
  fi

  # ── 4) Sweep unclaimed sections.  ────────────────────────────────────
  # Two outcomes :
  #   - KEEP + disable : ra0/rai0/rax0 (clone templates) + wlanmld5g/6g +
  #     mld0 when an MLO SSID exists in this catalog. These sections are
  #     load-bearing — destroying them breaks the next clone or the
  #     ongoing MLO group.
  #   - DELETE : everything else (guest2g/5g/6g, wlanmld2g, wlanmldguest*,
  #     mld1, leftover SC_WL_* from a previous catalog state). Their
  #     ifnames free up at the next reboot, letting newly-created
  #     SC_WL_<SLUG>_<BAND> sections claim lower indexes (ra1/2/3 instead
  #     of ra4+). This is what users perceive as "the OEM ghost slots are
  #     gone and I can fit more SSIDs".
  local sec keep_pat
  # Build the keep pattern. mld0 + per-band MLO links are conditional :
  # they only stay if an MLO SSID claimed them in this apply.
  keep_pat=" ra0 rai0 rax0 "
  if _wifi_is_claimed "mld0"; then
    keep_pat="${keep_pat}mld0 "
  fi
  if _wifi_is_claimed "wlanmld5g"; then
    keep_pat="${keep_pat}wlanmld5g "
  fi
  if _wifi_is_claimed "wlanmld6g"; then
    keep_pat="${keep_pat}wlanmld6g "
  fi
  if _wifi_is_claimed "wlanmld2g"; then
    keep_pat="${keep_pat}wlanmld2g "
  fi
  for sec in $(_wifi_all_iface_sections); do
    _wifi_is_claimed "$sec" && continue
    # STA-mode + mesh sections are upstream-link / peer-link wifi-iface
    # entries we do NOT own. They're created by :
    #   - GL.iNet stock LCD "Hotel mode" / travel-router (wireless.sta →
    #     apclii0 associating with the upstream hotel WiFi),
    #   - the OEM stock UI when the operator switches to repeater mode,
    #   - a future repeater/mesh feature on the controller side.
    # The catalog only owns AP-mode SSIDs ; touching STA/mesh sections
    # would churn `-wireless.sta` on every apply, falsely fire the
    # layout-pending path (= REBOOT), and sever the upstream uplink. So
    # we skip them entirely (NEITHER keep NOR delete — leave intact).
    local _sec_mode
    _sec_mode=$(uci -q get "wireless.$sec.mode" 2>/dev/null)
    case "$_sec_mode" in
      sta|mesh) continue ;;
    esac
    case "$keep_pat" in
      *" $sec "*)
        # KEEP — just disable + mark managed. We need this section as a
        # template (ra0/rai0/rax0) or as MLO infrastructure. Also blank
        # the OEM-leaking ssid so the panel doesn't show
        # "GL-BE10000-759" / "-MLO-Guest" on these reserved slots.
        uci set "wireless.$sec.${_WIFI_MARK}=1"
        uci set "wireless.$sec.ssid=_SC_RESERVED_"
        uci set "wireless.$sec.hidden=1"
        _wifi_set_disabled "$sec" 1
        local ifn
        ifn=$(uci -q get "wireless.$sec.ifname")
        [ -n "$ifn" ] && link_plan="$link_plan$ifn:down:
"
        ;;
      *)
        # DELETE — frees the ifname slot at the next reboot.
        echo "wifi: dropping unused section $sec (frees ifname slot)"
        uci -q delete "wireless.$sec" 2>/dev/null
        ;;
    esac
  done

  # ── 4b) Drop unused MLD groups. ──────────────────────────────────────
  # mld1 is the OEM "MLO-Guest" group — never used here. mld0 we keep
  # only when an MLO SSID claimed it (see keep_pat above ; if not in
  # keep_pat, it falls through to delete here). MLD groups are `mld`
  # type, not `wifi-iface`, so the sweep above doesn't see them.
  local mldgrp
  for mldgrp in mld0 mld1; do
    uci -q get "wireless.$mldgrp" >/dev/null 2>&1 || continue
    case "$keep_pat" in
      *" $mldgrp "*) continue ;;
    esac
    echo "wifi: dropping unused MLD group $mldgrp"
    uci -q delete "wireless.$mldgrp" 2>/dev/null
  done

  # ── 5) Reboot decision : layout vs activation-only. ──────────────────
  # `uci changes wireless` lines look like  wireless.<sec>.<field>='val'.
  # Layout = any change on a field OTHER than `disabled`. Activation =
  # disabled-only. We capture this BEFORE committing so the filter works.
  local pending layout_pending
  pending=$(uci changes wireless 2>/dev/null)
  layout_pending=$(printf '%s' "$pending" | grep -v "\.disabled=" )

  if [ -n "$layout_pending" ]; then
    # Slot layout itself changed (ssid / enc / network / mld / iface list
    # / isolate / hidden / mark). The MTK driver only applies these at
    # boot — signal the controller to reboot.
    # Diagnostic line so the operator can see WHICH fields changed when
    # they're surprised by a reboot (e.g. when only profile activation
    # was expected, but a catalog edit / advanced-option drift snuck in).
    echo "wifi: layout-pending uci lines that forced reboot:" >&2
    printf '  %s\n' $layout_pending >&2
    uci commit wireless 2>&1 || { echo "wifi: uci commit failed" >&2; return 1; }
    echo "wifi: layout changed — REBOOT required to apply (uplink-safe)"
    [ "$errors" -gt 0 ] && return 1
    return 0
  fi

  if [ -n "$pending" ]; then
    uci commit wireless 2>&1 || { echo "wifi: uci commit failed" >&2; return 1; }
  fi

  # Always reconcile LIVE state via ip link, even when uci was already in
  # the right shape — self-healing for drift (e.g. a VAP that's UP at the
  # netdev level but never got attached to its bridge because netifd
  # skipped it at boot). Idempotent : _wifi_link_apply no-ops when current
  # state already matches.
  local applied_up=0 applied_down=0
  local entry
  local oldifs="$IFS"; IFS='
'
  for entry in $link_plan; do
    [ -z "$entry" ] && continue
    # entry = "ifname:want:net" (net may be empty for unclaimed slots).
    local ifn want net tmp
    ifn="${entry%%:*}"
    tmp="${entry#*:}"
    want="${tmp%%:*}"
    net="${tmp#*:}"
    [ "$net" = "$tmp" ] && net=""   # no second `:` → empty net
    local before_state before_master
    before_state=$(_wifi_link_state "$ifn")
    before_master=$(_wifi_link_master "$ifn")
    _wifi_link_apply "$ifn" "$want" "$net"
    local after_state after_master
    after_state=$(_wifi_link_state "$ifn")
    after_master=$(_wifi_link_master "$ifn")
    if [ "$before_state" != "$after_state" ] || [ "$before_master" != "$after_master" ]; then
      if [ "$want" = "up" ]; then applied_up=$((applied_up + 1)); else applied_down=$((applied_down + 1)); fi
    fi
  done
  IFS="$oldifs"
  echo "wifi: activation applied LIVE — $applied_up up, $applied_down down (no reboot)"

  [ "$errors" -gt 0 ] && return 1
  return 0
}

#!/bin/sh
# /etc/rc.button/reset — managed by slate-controller.
#
# Preserves the OEM behaviors EXACTLY :
#   SEEN ≥ 20s  → no-op (anti-overshoot guard from OEM)
#   SEEN ≥  8s  → factory_reset
#   SEEN ≥  3s  → reset_network &
#   timeout     → failsafe boot mode
# AND adds :
#   SEEN <  3s  → slate-controller profile cycle (cycle-profile.sh)
#
# The OEM file is backed up to /etc/rc.button/reset.slate-ctrl.backup at
# first install so the user can roll back. Re-deploying the agent is
# idempotent — this script overwrites in place.
#
# IMPORTANT : the factory-reset and reset-network branches stay untouched.
# Bricked-controller recovery via 8s press is the failsafe of last resort
# and must keep working even if our cycle script breaks.

. /lib/functions.sh
. /lib/functions/gl_util.sh

OVERLAY="$( grep ' /overlay ' /proc/mounts )"

case "$ACTION" in
pressed)
	[ -z "$OVERLAY" ] && return 0
	reset_btn_pressed
;;
timeout)
	. /etc/diag.sh
	set_state failsafe
;;
released)
	[ -z "$OVERLAY" ] && return 0
	reset_btn_released

	if [ "$SEEN" -ge 20 ]; then
		return 0
	elif [ "$SEEN" -ge 8 ]; then
		factory_reset
	elif [ "$SEEN" -ge 3 ]; then
		reset_network &
	else
		# slate-controller: short press = cycle profile
		# Backgrounded so the button handler returns quickly.
		[ -x /etc/slate-controller/scripts/cycle-profile.sh ] && \
			/etc/slate-controller/scripts/cycle-profile.sh &
	fi
;;
esac

return 0

#!/bin/sh
set -eu

# Discovery is optional: startup failure must leave the regular receiver usable.
start_discovery() {
    mkdir -p /run/dbus /run/avahi-daemon || return 1
    if ! dbus-send --system --type=method_call --print-reply \
        --dest=org.freedesktop.DBus / org.freedesktop.DBus.ListNames >/dev/null 2>&1; then
        # Container-local files may survive a restart after an unclean exit.
        rm -f /run/dbus/system_bus_socket /run/dbus/pid || return 1
        dbus-uuidgen --ensure || return 1
        dbus-daemon --system --fork --nopidfile || return 1
    fi
    if ! avahi-daemon --check; then
        rm -f /run/avahi-daemon/pid || return 1
        avahi-daemon --daemonize --no-chroot || return 1
    fi
}

if ! start_discovery; then
    printf '%s\n' '[MiAir Next] warning: AirPlay 2 discovery unavailable; receiver startup will fall back to AirPlay 1.' >&2
fi

# Reuse the standard image's volume ownership and privilege-drop logic.
exec entrypoint.sh "$@"

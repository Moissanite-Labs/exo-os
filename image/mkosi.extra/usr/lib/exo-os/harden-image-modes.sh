#!/bin/sh
# Normalize root-controlled image policy/runtime paths after mkosi imports host files.
set -eu
umask 022
root=${EXO_OS_IMAGE_ROOT:-}

normalize_ancestor() {
    path=$1
    path=${root%/}$path
    [ -e "$path" ] || [ -L "$path" ] || return 0
    if [ "$(id -u)" -eq 0 ]; then chown root:root "$path"; fi
    chmod go-w "$path"
}

normalize_tree() {
    path=${root%/}$1
    [ -e "$path" ] || [ -L "$path" ] || return 0
    if [ "$(id -u)" -eq 0 ]; then
        find "$path" -xdev \( -type d -o -type f \) -exec chown root:root {} +
    fi
    find "$path" -xdev \( -type d -o -type f \) -exec chmod go-w {} +
}

# Trusted-path checks walk every ancestor, so normalize those too.
for path in /etc /opt /usr /usr/lib /usr/local /usr/local/bin /usr/lib/systemd \
            /usr/lib/systemd/system /usr/lib/environment.d /usr/lib/exo-os; do
    normalize_ancestor "$path"
done

for path in /etc/exo-os /etc/greetd /etc/pacman.d /etc/profile.d /etc/skel \
            /opt/exo /usr/lib/environment.d /usr/lib/systemd/system \
            /usr/lib/systemd/system-preset /usr/lib/systemd/user \
            /usr/lib/systemd/user-preset; do
    normalize_tree "$path"
done

for path in /etc/fstab /etc/locale.gen; do
    target=${root%/}$path
    if [ -f "$target" ]; then
        if [ "$(id -u)" -eq 0 ]; then chown root:root "$target"; fi
        chmod go-w "$target"
    fi
done

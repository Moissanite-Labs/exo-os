FROM ubuntu:24.04
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-gi gir1.2-gtk-4.0 libgtk-4-bin \
    git ca-certificates xkb-data libxkbcommon-tools dbus-x11 bubblewrap libseccomp2 util-linux \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/test/bin:/usr/libexec:/usr/bin:/bin"
RUN python3 -m venv --system-site-packages /opt/test
WORKDIR /test/exo-os

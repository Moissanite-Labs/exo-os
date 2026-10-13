"""The image's unit files and tmpfiles snippet must keep the receipt key available after a reboot."""
from pathlib import Path

from exo_os.sysd.protocol import PUBLIC_KEY

UNITS = Path(__file__).resolve().parent.parent / "image/mkosi.extra/usr/lib"


def test_receipt_key_is_not_under_a_runtime_directory():
    assert not str(PUBLIC_KEY).startswith(("/run/", "/tmp/", "/var/tmp/"))
    service = (UNITS / "systemd/system/exo-sysd.service").read_text()
    assert "RuntimeDirectory" not in service  # systemd removes it when the service stops


def test_tmpfiles_creates_the_key_directory_root_owned_at_boot():
    rows = [line.split() for line in (UNITS / "tmpfiles.d/exo-sysd.conf").read_text().splitlines()
            if line.strip() and not line.startswith("#")]
    assert ["d", str(PUBLIC_KEY.parent), "0755", "root", "root", "-"] in rows


def test_only_the_socket_is_enabled_so_the_key_cannot_depend_on_the_service():
    preset = (UNITS / "systemd/system-preset/10-exo-os.preset").read_text().splitlines()
    assert "enable exo-sysd.socket" in preset and "enable exo-sysd.service" not in preset

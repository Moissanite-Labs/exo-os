from exo_os import paths


def _marked(config, include="dofile(generated)\n", marker=paths.DESKTOP_OWNER_CONTENT):
    hypr = config / "hypr"
    (hypr / "exo").mkdir(parents=True)
    (hypr / "exo" / "desktop-owner").write_text(marker, encoding="utf-8")
    (hypr / "hyprland.lua").write_text(include, encoding="utf-8")


def test_owns_only_marked_desktop_with_active_profile_include(tmp_path):
    config = tmp_path / "config"
    _marked(config, "-- Exo OS base config\nlocal generated = \"profile.lua\"\n  dofile(generated)  \n")

    assert paths.owns_desktop(config)


def test_unmarked_desktop_is_not_owned_even_with_active_include(tmp_path):
    config = tmp_path / "config"
    _marked(config, marker="")

    assert not paths.owns_desktop(config)


def test_comment_only_include_does_not_mark_desktop_as_owned(tmp_path):
    config = tmp_path / "config"
    _marked(config, "-- dofile(generated)\n")

    assert not paths.owns_desktop(config)


def test_wrong_marker_is_not_owned(tmp_path):
    config = tmp_path / "config"
    _marked(config, marker="exo-os-v2\n")

    assert not paths.owns_desktop(config)


def test_invalid_marker_encoding_fails_closed(tmp_path):
    marker = tmp_path / "config" / "hypr" / "exo" / "desktop-owner"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"\xff")

    assert not paths.owns_desktop(marker.parents[2])

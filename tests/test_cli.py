import argparse

import pytest

from reachy_kids import cli


def test_desktop_entry_uses_floating_terminal_when_available():
    terminal = "/usr/bin/xdg-terminal-exec"
    entry = cli.desktop_entry("Reachy Kids", "Talk", "launch --sim", "/opt/bin/reachy-kids", terminal)
    expected = f"Exec={terminal} --app-id=TUI.float --title=reachy-kids -e /opt/bin/reachy-kids launch --sim"
    assert expected in entry
    assert "Terminal=false" in entry
    assert "Icon=reachy-kids" in entry


def test_desktop_entry_falls_back_to_terminal_flag():
    entry = cli.desktop_entry("Reachy Kids", "Talk", "launch", "/opt/bin/reachy-kids", None)
    assert "Exec=/opt/bin/reachy-kids launch\n" in entry
    assert "Terminal=true" in entry


def test_install_and_uninstall_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    cli.main(["install-launcher"])

    apps = tmp_path / "applications"
    assert {p.name for p in apps.iterdir()} >= set(cli.LAUNCHERS)
    assert "launch --sim" in (apps / "reachy-kids-sim.desktop").read_text()
    icon = tmp_path / "icons/hicolor/scalable/apps/reachy-kids.svg"
    assert icon.read_text().startswith("<svg")

    cli.main(["uninstall-launcher"])
    assert not (apps / "reachy-kids.desktop").exists()
    assert not icon.exists()


@pytest.mark.parametrize(("running_sim", "want_sim"), [(False, True), (True, False)])
def test_launch_refuses_a_daemon_in_the_other_mode(monkeypatch, running_sim, want_sim):
    monkeypatch.setattr(cli, "_daemon_status", lambda: {"simulation_enabled": running_sim})
    monkeypatch.setattr(cli, "run_app", lambda: pytest.fail("must not run against the wrong daemon"))
    args = argparse.Namespace(sim=want_sim, headless=True, no_browser=True)

    with pytest.raises(SystemExit, match="already running"):
        cli.launch(args)


def test_launch_reuses_a_matching_daemon(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_daemon_status", lambda: {"simulation_enabled": True})
    monkeypatch.setattr(cli, "_start_daemon", lambda **_: pytest.fail("must reuse the running daemon"))
    monkeypatch.setattr(cli, "run_app", lambda: ran.append(True))
    monkeypatch.delenv("REACHY_KIDS_MEDIA_BACKEND", raising=False)

    cli.launch(argparse.Namespace(sim=True, headless=True, no_browser=True))

    assert ran == [True]
    assert cli.os.environ["REACHY_KIDS_MEDIA_BACKEND"] == "local"
    monkeypatch.delenv("REACHY_KIDS_MEDIA_BACKEND")

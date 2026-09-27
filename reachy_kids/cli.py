"""`reachy-kids` command: run the app, launch it with a daemon/simulator, install desktop launchers."""

import os
import sys
import time
import shutil
import logging
import argparse
import threading
import subprocess
import webbrowser
import urllib.error
import urllib.request
from pathlib import Path
from importlib.resources import files


logger = logging.getLogger("reachy_kids")

DAEMON_URL = "http://127.0.0.1:8000/api/daemon/status"
APP_ID = "reachy-kids"
LAUNCHERS = {
    "reachy-kids.desktop": ("Reachy Kids", "Talk with your Reachy Mini robot", "launch"),
    "reachy-kids-sim.desktop": ("Reachy Kids Simulator", "Talk with a simulated Reachy Mini", "launch --sim"),
}


def _url_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_for(url: str, timeout_s: float, process: subprocess.Popen[bytes] | None = None) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _url_ready(url):
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def _start_daemon(sim: bool, headless: bool) -> subprocess.Popen[bytes]:
    command = [sys.executable, "-m", "reachy_mini.daemon.app.main"]
    if sim:
        command.append("--sim")
    if headless:
        command.append("--headless")
    logger.info("Starting Reachy Mini daemon: %s", " ".join(command))
    process = subprocess.Popen(command)
    if not _wait_for(DAEMON_URL, timeout_s=90.0, process=process):
        process.terminate()
        raise SystemExit(
            "The Reachy Mini daemon did not start. Is the robot plugged in and powered on? "
            "Use `reachy-kids launch --sim` to try the simulator."
        )
    return process


def _open_browser_when_ready(url: str) -> None:
    def wait_and_open() -> None:
        if _wait_for(url, timeout_s=60.0):
            webbrowser.open(url)

    threading.Thread(target=wait_and_open, daemon=True, name="open-browser").start()


def run_app() -> None:
    """Run the app against the daemon that is already running."""
    os.environ.setdefault("REACHY_KIDS_HOST", "127.0.0.1")
    # Imported late so the REACHY_KIDS_* environment set here and by `launch` is seen by the app class.
    from reachy_kids.main import main

    main()


def launch(args: argparse.Namespace) -> None:
    """Start a daemon if needed (real robot or simulator), open the settings page, run the app."""
    daemon: subprocess.Popen[bytes] | None = None
    if _url_ready(DAEMON_URL):
        logger.info("Using the Reachy Mini daemon that is already running")
    else:
        daemon = _start_daemon(sim=args.sim, headless=args.headless)
    if args.sim:
        # The simulator has no media server; talk to the computer's mic and speakers directly.
        os.environ.setdefault("REACHY_KIDS_MEDIA_BACKEND", "local")
    if not args.no_browser:
        _open_browser_when_ready(f"http://localhost:{os.getenv('REACHY_KIDS_PORT', '8042')}")
    try:
        run_app()
    finally:
        if daemon is not None:
            daemon.terminate()
            try:
                daemon.wait(timeout=15)
            except subprocess.TimeoutExpired:
                daemon.kill()


def desktop_entry(name: str, comment: str, exec_args: str, executable: str, terminal_exec: str | None) -> str:
    """Return a freedesktop entry that runs ``reachy-kids <exec_args>`` in a floating terminal."""
    command = f"{executable} {exec_args}"
    if terminal_exec:
        exec_line, terminal = f"{terminal_exec} --app-id=TUI.float --title={APP_ID} -e {command}", "false"
    else:
        exec_line, terminal = command, "true"
    return (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Comment={comment}\n"
        f"Exec={exec_line}\n"
        f"Terminal={terminal}\n"
        f"Icon={APP_ID}\n"
        "Categories=Education;Robotics;\n"
        "StartupNotify=true\n"
    )


def data_home() -> Path:
    """Return XDG_DATA_HOME (defaults to ~/.local/share)."""
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def install_launcher(_: argparse.Namespace) -> None:
    """Install app-launcher entries (Omarchy's SUPER+SPACE, GNOME, KDE...) and the icon."""
    executable = shutil.which("reachy-kids") or str(Path(sys.argv[0]).resolve())
    terminal_exec = shutil.which("xdg-terminal-exec")
    applications = data_home() / "applications"
    icons = data_home() / "icons" / "hicolor" / "scalable" / "apps"
    applications.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    (icons / f"{APP_ID}.svg").write_bytes(files("reachy_kids").joinpath("static/icon.svg").read_bytes())
    for filename, (name, comment, exec_args) in LAUNCHERS.items():
        path = applications / filename
        path.write_text(desktop_entry(name, comment, exec_args, executable, terminal_exec))
        path.chmod(0o755)
        print(f"Installed {path}")
    update_database = shutil.which("update-desktop-database")
    if update_database:
        subprocess.run([update_database, str(applications)], check=False, capture_output=True)
    print("Find Reachy Kids in your app launcher (SUPER + SPACE on Omarchy).")


def uninstall_launcher(_: argparse.Namespace) -> None:
    """Remove the launcher entries and icon."""
    for path in [
        *(data_home() / "applications" / f for f in LAUNCHERS),
        data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg",
    ]:
        if path.exists():
            path.unlink()
            print(f"Removed {path}")


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(prog="reachy-kids", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="run the app against an already-running daemon (default)")
    launch_parser = commands.add_parser("launch", help="start the daemon if needed, open the settings page, run")
    launch_parser.add_argument("--sim", action="store_true", help="use the MuJoCo simulator instead of a robot")
    launch_parser.add_argument("--headless", action="store_true", help="hide the simulator window")
    launch_parser.add_argument("--no-browser", action="store_true", help="don't open the settings page")
    commands.add_parser("install-launcher", help="add Reachy Kids to your desktop app launcher")
    commands.add_parser("uninstall-launcher", help="remove the desktop launcher entries")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "launch":
        launch(args)
    elif args.command == "install-launcher":
        install_launcher(args)
    elif args.command == "uninstall-launcher":
        uninstall_launcher(args)
    else:
        run_app()


if __name__ == "__main__":
    main()

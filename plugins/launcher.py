"""Runs one BoardLab Sync action so that a failure is never silent.

KiCad only reports "exited with code 1" when a plugin fails, and on some systems its message panel
does not show the plugin's output. So every action's messages also go to a log file, and when an
action fails (including a missing Python package or a TLS error) the reason is shown in a dialog.
Standard library only: this must work even when kicad-python is missing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "boardlab-sync"


def log_file() -> Path:
    return config_dir() / "boardlab-sync.log"


def log(text: str) -> None:
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix(".old.log"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def alert(message: str, *, ok: bool = False) -> None:
    """Show the outcome where the designer looks: a failure, or with ``ok`` a success
    (best effort, never raises). KiCad itself shows nothing but a bare exit code."""
    title = "BoardLab Sync"
    text = message if len(message) < 1500 else message[:1500] + " ..."
    try:
        if os.environ.get("BOARDLAB_SYNC_NO_DIALOG"):
            return
        if sys.platform == "darwin":
            # The dialog belongs to osascript, a background process: "activate" brings it in front
            # of KiCad, and the notification stays in Notification Center even if the dialog does not.
            kind = "informational" if ok else "warning"
            script = ("on run argv\n"
                      "display notification (item 2 of argv) with title (item 1 of argv)\n"
                      "activate\n"
                      f"display alert (item 1 of argv) message (item 2 of argv) as {kind}\n"
                      "end run")
            got = subprocess.run(["osascript", "-e", script, title, text],
                                 timeout=600, check=False, capture_output=True, text=True)
            log(f"dialog: osascript exit {got.returncode} {got.stderr.strip()}".rstrip())
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 if ok else 0x30)
        else:
            for cmd in (["zenity", "--info" if ok else "--warning", "--title", title, "--text", text],
                        ["kdialog", "--title", title, "--msgbox" if ok else "--sorry", text],
                        ["notify-send", title, text]):
                try:
                    subprocess.run(cmd, timeout=600, check=False, capture_output=True)
                    return
                except FileNotFoundError:
                    continue
    except Exception as exc:
        log(f"dialog failed: {type(exc).__name__}: {exc}")


def ask(message: str, choices: list[str]) -> str | None:
    """Ask the designer to pick one of ``choices`` (the first is the default) or Cancel; ``None`` is
    Cancel. With no dialog available (BOARDLAB_SYNC_NO_DIALOG, a Linux without zenity or kdialog)
    the answer is BOARDLAB_SYNC_ANSWER, else the default: the caller saves a backup first and the
    change is one Ctrl+Z, so a missing dialog never blocks the designer."""
    title = "BoardLab Sync"
    text = message if len(message) < 1800 else message[:1800] + " ..."
    fallback = os.environ.get("BOARDLAB_SYNC_ANSWER") or choices[0]
    fallback = None if fallback.lower() == "cancel" else fallback
    if os.environ.get("BOARDLAB_SYNC_NO_DIALOG"):
        log(f"ask (no dialog): {fallback}")
        return fallback
    try:
        if sys.platform == "darwin":
            buttons = ["Cancel"] + list(reversed(choices))  # the default sits on the right
            script = ("on run argv\n"
                      "activate\n"
                      "set b to {" + ", ".join(f"item {i + 3} of argv" for i in range(len(buttons))) + "}\n"
                      "display dialog (item 2 of argv) with title (item 1 of argv) buttons b "
                      "default button (item 3 of argv) cancel button \"Cancel\" with icon note\n"
                      "return button returned of result\n"
                      "end run")
            got = subprocess.run(["osascript", "-e", script, title, text, choices[0], *buttons],
                                 timeout=1800, check=False, capture_output=True, text=True)
            picked = got.stdout.strip()
            return picked if got.returncode == 0 and picked in choices else None
        if sys.platform == "win32":
            import ctypes
            if len(choices) == 1:
                body = text + f"\n\nOK = {choices[0]}"
                r = ctypes.windll.user32.MessageBoxW(None, body, title, 0x1 | 0x40)  # OK / Cancel
                return choices[0] if r == 1 else None
            body = text + f"\n\nYes = {choices[0]}\nNo = {choices[1]}"
            r = ctypes.windll.user32.MessageBoxW(None, body, title, 0x3 | 0x40)  # Yes / No / Cancel
            return {6: choices[0], 7: choices[1]}.get(r)
        try:
            cmd = ["zenity", "--question", "--title", title, "--text", text, "--ok-label", choices[0],
                   "--cancel-label", "Cancel"] + [a for c in choices[1:] for a in ("--extra-button", c)]
            got = subprocess.run(cmd, timeout=1800, check=False, capture_output=True, text=True)
            if got.returncode == 0:
                return choices[0]
            return got.stdout.strip() if got.stdout.strip() in choices else None
        except FileNotFoundError:
            pass
        try:
            if len(choices) == 1:
                cmd = ["kdialog", "--title", title, "--yesno", text, "--yes-label", choices[0], "--no-label", "Cancel"]
            else:
                cmd = ["kdialog", "--title", title, "--yesnocancel", text, "--yes-label", choices[0],
                       "--no-label", choices[1], "--cancel-label", "Cancel"]
            got = subprocess.run(cmd, timeout=1800, check=False, capture_output=True)
            return {0: choices[0], 1: choices[1] if len(choices) > 1 else None}.get(got.returncode)
        except FileNotFoundError:
            pass
    except Exception as exc:
        log(f"question dialog failed: {type(exc).__name__}: {exc}")
    log(f"ask (no dialog available): {fallback}")
    return fallback


def other_copy_note() -> str | None:
    """A second BoardLab Sync left in KiCad's plugins folder (a hand-unzipped copy from before the
    Plugin and Content Manager package) next to the installed package: KiCad may run either one."""
    here = Path(__file__).resolve().parent
    parts = here.parts
    if "3rdparty" in parts:  # <KiCad docs>/<version>/3rdparty/plugins/it_boardlab_sync
        root = Path(*parts[:parts.index("3rdparty")])
        other = root / "plugins" / "boardlab-sync"
    elif here.parent.name == "plugins":  # <KiCad docs>/<version>/plugins/boardlab-sync
        other = here.parent.parent / "3rdparty" / "plugins" / "it_boardlab_sync"
        if (other / "plugin.json").exists():
            return (f"This is an old copy of BoardLab Sync ({here}). The installed package is in {other}: "
                    "move this folder to the Trash and restart KiCad.")
        return None
    else:
        return None
    if (other / "plugin.json").exists():
        return (f"An old copy of BoardLab Sync is still in {other}: move that folder to the Trash and "
                "restart KiCad, so only the installed package runs.")
    return None


def run(action: str) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    log(f"--- {action} (Python {sys.version.split()[0]}, {sys.executable})")
    try:
        import sync
    except ImportError as exc:
        message = (f"BoardLab Sync could not start: {exc}.\n\nKiCad has not finished installing the plugin's "
                   "Python packages (it needs internet the first time). Wait a minute and try again; if it keeps "
                   "failing, restart KiCad. On Linux, install python3-venv first.")
        print(message, flush=True)
        log(message + "\n" + traceback.format_exc())
        alert(message)
        sys.exit(1)
    try:
        code = sync.main(["sync", action])
    except BaseException as exc:  # an unexpected error: say what it was instead of a bare exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            sys.exit(0)
        message = f"BoardLab Sync failed: {type(exc).__name__}: {exc}"
        print(message, flush=True)
        log(message + "\n" + traceback.format_exc())
        alert(message + f"\n\nDetails: {log_file()}")
        sys.exit(1)
    sys.exit(code)

"""BoardLab Sync: the KiCad side. Each toolbar button runs one of the ``action_*`` functions
in its own process (KiCad IPC plugin); what it says goes to KiCad's message panel (stdout).
"""

from __future__ import annotations

import json
import os
import platform
import ssl
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import core
import launcher
from kipy import KiCad
from kipy.board_types import BoardCircle, BoardText, Track, Via
from kipy.geometry import Angle, Vector2
from kipy.proto.board.board_types_pb2 import BoardLayer, PadStackShape
from kipy.util.board_layer import (
    canonical_name,
    is_copper_layer,
    layer_from_canonical_name,
)

DEFAULT_SERVER = "https://boardlab.it"
LINK_WAIT_S = 600


_REPORT: list[str] = []  # what the closing dialog of a successful action says


def say(text: str, *, report: bool = True) -> None:
    print(text, flush=True)
    launcher.log(text)
    if report:
        _REPORT.append(text)


# -- links (one per board, kept in the user's config folder) -------------------------------------


def _links_file() -> Path:
    return launcher.config_dir() / "links.json"


def _board_key(board) -> str:
    try:
        folder = board.get_project().path
    except Exception:
        folder = ""
    return str(Path(folder) / board.name) if folder else board.name


def _read_links() -> dict:
    try:
        return json.loads(_links_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_link(key: str, link: dict) -> None:
    path = _links_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    links = _read_links()
    links[key] = link
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(links, indent=2), encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _link_for(board) -> dict:
    link = _read_links().get(_board_key(board))
    if not link:
        raise SystemExit("This board is not linked to a BoardLab project yet: press \"BoardLab: Link\" first.")
    return link


# -- HTTP ------------------------------------------------------------------------------------------


def _tls() -> ssl.SSLContext:
    """Certificate check with certifi's CA list when present: the Python KiCad ships on macOS has no
    system CA list, so plain urllib fails there with CERTIFICATE_VERIFY_FAILED."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _call(server: str, method: str, path: str, *, token: str | None = None, body: bytes | None = None,
          content_type: str = "application/json", timeout: float = 120) -> dict:
    req = urllib.request.Request(server.rstrip("/") + path, data=body, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"BoardLab-Sync/1 KiCad ({platform.system()})")
    if body is not None:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_tls()) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:
            detail = None
        if exc.code == 401:
            detail = "BoardLab no longer accepts this link (password changed or link expired): press \"BoardLab: Link\" again."
        raise SystemExit(f"BoardLab answered {exc.code}: {detail or exc.reason}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach {server}: {exc.reason}") from None


def _json(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


# -- the open board ------------------------------------------------------------------------------


def _board():
    kicad = KiCad()
    return kicad, kicad.get_board()


def _client_name(kicad) -> str:
    try:
        version = kicad.get_version().full_version
    except Exception:
        version = "?"
    return f"KiCad {version} on {platform.node() or platform.system()}"[:80]


def _snapshot(board, *, with_pads: bool = False, with_net_points: bool = False) -> core.BoardSnapshot:
    snap = core.BoardSnapshot()
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value
        snap.footprints[ref] = (fp.position.x, fp.position.y, fp.orientation.degrees)
        if with_pads:
            snap.pads[ref] = {p.number: (p.position.x, p.position.y) for p in fp.definition.pads}
    snap.nets = {n.name for n in board.get_nets()}
    snap.copper_layers = {canonical_name(l) for l in board.get_enabled_layers() if is_copper_layer(l)}
    if with_net_points:
        for t in board.get_tracks():
            snap.net_points.setdefault(t.net.name, (t.start.x, t.start.y))
    return snap


# -- actions ------------------------------------------------------------------------------------------


def action_link() -> None:
    """Link this board to a BoardLab project; the project is chosen and confirmed on the site."""
    kicad, board = _board()
    server = os.environ.get("BOARDLAB_URL", DEFAULT_SERVER).rstrip("/")
    start = _call(server, "POST", "/api/v2/kicad/device",
                  body=_json({"client": _client_name(kicad), "board": str(board.name or "")[:120]}))
    url = server + start["verify_path"]
    say(f"BoardLab: confirm code {start['user_code']} in your browser ({url}) and pick the project for "
        f"{board.name}.", report=False)
    webbrowser.open(url)
    deadline = time.time() + min(LINK_WAIT_S, int(start.get("expires_in", LINK_WAIT_S)))
    while time.time() < deadline:
        time.sleep(max(1, int(start.get("interval", 3))))
        got = _call(server, "POST", "/api/v2/kicad/device/token", body=_json({"device_code": start["device_code"]}))
        if got.get("status") == "linked":
            project = got["project"]
            _save_link(_board_key(board), {"server": server, "project_id": project["id"],
                                           "project_name": project.get("name", ""), "token": got["token"],
                                           "linked_at": int(time.time())})
            say(f"BoardLab: {board.name} is linked to \"{project.get('name') or project['id']}\".")
            if got.get("send_board"):  # a project just created on the site for this board
                action_push_board()
            return
    raise SystemExit("BoardLab: the link was not confirmed in time; press \"BoardLab: Link\" again.")


KEEP_MINE = "Update board"
REPLACE_MINE = "Replace mine"
BACKUPS_KEPT = 10


def _copper_key(item) -> tuple:
    if isinstance(item, Via):
        return core.via_key(item.net.name, item.position.x, item.position.y, item.diameter, item.drill_diameter)
    return core.track_key(item.net.name, canonical_name(item.layer), item.start.x, item.start.y,
                          item.end.x, item.end.y, item.width)


def _backup_folder(board) -> Path:
    try:
        folder = Path(board.get_project().path)
    except Exception:
        folder = None
    if folder and folder.is_dir():
        return folder / "boardlab-backups"  # "-backups": Send board leaves it out, as KiCad's own
    return launcher.config_dir() / "backups"


def _backup(board, folder: Path) -> Path:
    """The board as it is open now (saved or not), before BoardLab touches it; the last
    BACKUPS_KEPT copies of each board are kept."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(str(board.name or "board")).stem
    path = folder / f"{stem}-before-boardlab-{time.strftime('%Y%m%d-%H%M%S')}.kicad_pcb"
    path.write_text(board.get_as_string(), encoding="utf-8")
    for old in sorted(folder.glob(f"{stem}-before-boardlab-*.kicad_pcb"))[:-BACKUPS_KEPT]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def action_pull_changes() -> None:
    """Update the open board with BoardLab's placement and routing (today: the saved DDR plan).

    Only what changed is written: the parts not yet where BoardLab puts them are moved, the plan's
    tracks and vias the board does not have are added, and the copper an earlier update put there
    and the new routing dropped is removed. The designer's own copper is kept unless they choose
    otherwise. A preview comes first and a copy of the board is saved before the one undoable
    commit."""
    kicad, board = _board()
    link = _link_for(board)
    change = _call(link["server"], "GET", f"/api/v2/boards/{link['project_id']}/kicad/changeset", token=link["token"])
    snap = _snapshot(board)
    problems = core.check_changeset(change, snap)
    if problems:
        raise SystemExit("BoardLab: nothing changed.\n- " + "\n- ".join(problems))
    nets = {n.name: n for n in board.get_nets()}
    replace = set(change.get("replace_copper_of_nets", []))
    copper = [(_copper_key(i), i) for i in list(board.get_tracks()) + list(board.get_vias()) if i.net.name in replace]
    key = _board_key(board)
    applied = core.remembered((link.get("applied") or {}).get("copper"))
    up = core.plan_update(change, snap, copper, applied)
    title = change.get("title", "BoardLab changes")

    def remember() -> None:
        link["applied"] = {"title": title, "at": int(time.time()), "copper": [list(k) for k in up.plan_keys]}
        _save_link(key, link)

    if up.nothing_to_do:
        remember()  # a board updated by an older plugin: from now on its BoardLab copper is known
        say(f"BoardLab: the board already has \"{title}\"; nothing to update."
            + (f" Your {len(up.local)} other tracks or vias on the plan's nets are left as they are." if up.local else ""))
        return

    folder = _backup_folder(board)
    choices = [KEEP_MINE, REPLACE_MINE] if up.local else [KEEP_MINE]
    answer = launcher.ask(core.preview_text(change, up, str(folder)), choices)
    if answer is None:
        say("BoardLab: update cancelled, the board is unchanged.")
        return
    backup = _backup(board, folder)
    remove = up.stale + (up.local if answer == REPLACE_MINE else [])
    by_ref = {fp.reference_field.text.value: fp for fp in board.get_footprints()}

    tracks = []
    for t in up.add_tracks:
        track = Track()
        track.start = Vector2.from_xy(*t["start"])
        track.end = Vector2.from_xy(*t["end"])
        track.width = int(t["width"])
        track.layer = layer_from_canonical_name(t["layer"])
        track.net = nets[t["net"]]
        tracks.append(track)
    vias = []
    for v in up.add_vias:
        via = Via()
        via.position = Vector2.from_xy(*v["at"])
        via.diameter = int(v["diameter"])
        via.drill_diameter = int(v["drill"])
        via.proto.pad_stack.copper_layers[0].shape = PadStackShape.PSS_CIRCLE
        via.net = nets[v["net"]]
        vias.append(via)

    commit = board.begin_commit()
    try:
        if remove:
            board.remove_items(remove)
        moved = []
        for fp in up.moves:
            item = by_ref[fp["ref"]]
            item.orientation = Angle.from_degrees(float(fp["to"]["angle_deg"]))  # turns about its anchor
            item.position = Vector2.from_xy(int(fp["to"]["x"]), int(fp["to"]["y"]))
            moved.append(item)
        if moved:
            board.update_items(moved)
        if tracks or vias:
            board.create_items(tracks + vias)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, title)
    remember()
    kept = len(up.local) if answer != REPLACE_MINE else 0
    summary = (f"Moved {len(up.moves)} parts, added {len(tracks)} tracks and {len(vias)} vias, "
               f"removed {len(remove)} old tracks and vias" + (f", kept {kept} of the designer's" if kept else "")
               + f" on {len(replace)} nets.")
    say(f"BoardLab: applied \"{title}\". {summary} Ctrl+Z undoes it all. Backup: {backup}")
    for note in change.get("notes", []):
        say("  " + note)
    _call(link["server"], "POST", f"/api/v2/boards/{link['project_id']}/kicad/applied", token=link["token"],
          body=_json({"title": title, "summary": summary, "client": _client_name(kicad)}))


def action_pull_findings() -> None:
    """Pin BoardLab's open findings on User.Comments; the previous BoardLab notes are replaced."""
    _kicad, board = _board()
    link = _link_for(board)
    report = _call(link["server"], "GET", f"/api/v2/boards/{link['project_id']}/kicad/findings", token=link["token"])
    snap = _snapshot(board, with_pads=True, with_net_points=True)
    placed, unplaced = core.place_findings(report, snap)
    comments = BoardLayer.BL_Cmts_User
    old = [t for t in board.get_text()
           if isinstance(t, BoardText) and t.layer == comments and t.value.startswith(core.NOTE_MARK)]
    old += [s for s in board.get_shapes()
            if isinstance(s, BoardCircle) and s.layer == comments
            and s.attributes.stroke.width == core.NOTE_STROKE_NM]
    notes = []
    for f, x, y in placed:
        ring = BoardCircle()
        ring.layer = comments
        ring.center = Vector2.from_xy(x, y)
        ring.radius_point = Vector2.from_xy(x + 1_500_000, y)
        ring.attributes.stroke.width = core.NOTE_STROKE_NM
        text = BoardText()
        text.layer = comments
        text.value = core.note_text(f)
        text.position = Vector2.from_xy(x + 2_000_000, y - 2_000_000)
        text.attributes.size = Vector2.from_xy(800_000, 800_000)
        text.attributes.stroke_width = 100_000
        notes += [ring, text]
    commit = board.begin_commit()
    try:
        if old:
            board.remove_items(old)
        if notes:
            board.create_items(notes)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, "BoardLab findings")
    say(f"BoardLab: {len(placed)} findings pinned on User.Comments"
        + (f", replacing {len(old) // 2} older notes" if old else "") + ".")
    if unplaced:
        say(f"  {len(unplaced)} findings have no place on this board: "
            + ", ".join(f["id"] for f in unplaced[:10]) + (" ..." if len(unplaced) > 10 else ""))


def action_push_board() -> None:
    """Send the board as it is open now (saved or not) and the schematic saved next to it (the KiCad
    10 API has no schematic editor) to the linked BoardLab project, then open its page."""
    _kicad, board = _board()
    link = _link_for(board)
    project = link.get("project_name") or link["project_id"]
    text = board.get_as_string()
    got = _call(link["server"], "POST", f"/api/v2/boards/{link['project_id']}/kicad/board", token=link["token"],
                body=text.encode("utf-8"), content_type="application/octet-stream", timeout=600)
    page = f"{link['server'].rstrip('/')}/projects/{link['project_id']}"
    say(f"BoardLab: sent {board.name} to \"{project}\" ({got.get('summary', '')}). It is on {page}")
    _send_schematic(board, link)
    webbrowser.open(page)  # the designer looks for the board there next


def _send_schematic(board, link: dict) -> None:
    try:
        folder = Path(board.get_project().path)
    except Exception:
        folder = None
    data, sheets = core.schematic_zip(folder) if folder and folder.is_dir() else (b"", 0)
    if not sheets:
        say("  No schematic (.kicad_sch) next to the board: add it on the site.")
        return
    try:
        sch = _call(link["server"], "POST", f"/api/v2/boards/{link['project_id']}/kicad/schematic",
                    token=link["token"], body=data, content_type="application/zip", timeout=600)
    except SystemExit as exc:  # the board is already there: say why the schematic is not
        say(f"  The schematic was not sent: {exc.code}")
        return
    say(f"BoardLab: sent the schematic too, as saved on disk ({sch.get('summary', '')}).")


ACTIONS = {
    "link": action_link,
    "pull-changes": action_pull_changes,
    "pull-findings": action_pull_findings,
    "push-board": action_push_board,
}


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else ""
    if name not in ACTIONS:
        say(f"BoardLab: unknown action {name!r}")
        return 2
    try:
        ACTIONS[name]()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            say(str(exc.code))
            launcher.alert(str(exc.code))  # KiCad itself only says "exited with code 1"
            return 1
    note = launcher.other_copy_note()
    if note:
        launcher.log(note)
    if _REPORT or note:
        launcher.alert("\n\n".join(["\n".join(_REPORT), note or ""]).strip(), ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

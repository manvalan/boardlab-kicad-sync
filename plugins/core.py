"""BoardLab Sync, the part that does not need KiCad: what the open board must look like before a
changeset is applied, and where each finding goes on the board. Coordinates are nanometres,
angles degrees counter-clockwise, as in the KiCad API.

Everything is fail-closed: a changeset is applied only when every check passes, so BoardLab's
plan lands on the board it was made for or not at all.
"""

from __future__ import annotations

import io
import zipfile
from collections import Counter
from pathlib import Path
from dataclasses import dataclass, field

PLUGIN_VERSION = "26.11.4"  # calendar versioning; the PCM package and GET /plugins/latest read it from here
PACKAGE_ID = "it.boardlab.sync"
CHANGESET_FORMAT = "boardlab-kicad-changeset"
FINDINGS_FORMAT = "boardlab-kicad-findings"
POSITION_TOLERANCE_NM = 10_000  # 10 um: KiCad stores positions to the nanometre, files to 1e-4 mm
ANGLE_TOLERANCE_DEG = 0.01
NOTE_MARK = "[BoardLab]"  # every note text starts with this: the plugin replaces only its own notes
NOTE_STROKE_NM = 123_400  # the notes' circles are recognised by this stroke width on User.Comments


@dataclass
class BoardSnapshot:
    """The open board, as far as the checks need it."""
    footprints: dict[str, tuple[int, int, float]] = field(default_factory=dict)  # ref -> x, y, angle
    nets: set[str] = field(default_factory=set)
    copper_layers: set[str] = field(default_factory=set)  # canonical names of enabled copper layers
    pads: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)  # ref -> pad number -> x, y
    net_points: dict[str, tuple[int, int]] = field(default_factory=dict)  # net -> a point on its copper
    # copper of the nets the changeset replaces: (net, layer, x1, y1, x2, y2, width) / (net, x, y, diameter, drill)
    tracks: list[tuple] = field(default_factory=list)
    vias: list[tuple] = field(default_factory=list)


def _angle_diff(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def _at(where: tuple[int, int, float], target: dict) -> bool:
    return (abs(where[0] - int(target["x"])) <= POSITION_TOLERANCE_NM
            and abs(where[1] - int(target["y"])) <= POSITION_TOLERANCE_NM
            and _angle_diff(where[2], float(target.get("angle_deg", 0))) <= ANGLE_TOLERANCE_DEG)


def _mm(nm: int) -> str:
    return f"{nm / 1e6:.3f}"


SUPPORTED_VERSIONS = (1, 2)  # 1: one DDR plan; 2: the Layout page's approved changes (several anchors, zones)
ZONE_MARK = "BoardLab "  # every zone BoardLab draws is named so: the plugin replaces only its own


def anchors_of(change: dict) -> list[dict]:
    return ([change["anchor"]] if change.get("anchor") else []) + list(change.get("anchors") or [])


def check_changeset(change: dict, board: BoardSnapshot) -> list[str]:
    """Reasons the changeset cannot go on this board; empty when it can."""
    if change.get("format") != CHANGESET_FORMAT or int(change.get("version", 0)) not in SUPPORTED_VERSIONS:
        return ["This BoardLab Sync is too old for the changes BoardLab sent: update the plugin."]
    problems: list[str] = []
    for anchor in anchors_of(change):
        ref = anchor["ref"]
        if ref not in board.footprints:
            problems.append(f"{ref} is not on this board: open the board that was uploaded to BoardLab.")
        elif not _at(board.footprints[ref], anchor):
            x, y, a = board.footprints[ref]
            problems.append(f"{ref} moved since the upload (now {_mm(x)}, {_mm(y)} mm at {a:g} deg, the plan expects "
                            f"{_mm(anchor['x'])}, {_mm(anchor['y'])} mm at {anchor.get('angle_deg', 0):g} deg): "
                            "send the board to BoardLab and plan again.")
    for z in change.get("zones", []):
        if not str(z.get("name", "")).startswith(ZONE_MARK) or len(z.get("outline") or []) < 3:
            problems.append("A zone BoardLab sent has no BoardLab name or no outline: update the plugin.")
            break
    for fp in change.get("footprints", []):
        where = board.footprints.get(fp["ref"])
        if where is None:
            problems.append(f"{fp['ref']} is not on this board.")
        elif not (_at(where, fp["from"]) or _at(where, fp["to"])):
            problems.append(f"{fp['ref']} moved since the upload and is not where the plan puts it either: "
                            "send the board to BoardLab and plan again.")
    missing_nets = sorted(set(change.get("requires", {}).get("nets", [])) - board.nets)
    if missing_nets:
        problems.append(f"Nets of the plan not on this board: {', '.join(missing_nets[:8])}"
                        + (" ..." if len(missing_nets) > 8 else "") + ". Update the PCB from the schematic first.")
    missing_layers = [l for l in change.get("requires", {}).get("copper_layers", []) if l not in board.copper_layers]
    if missing_layers:
        problems.append(f"Copper layers of the plan missing on this board: {', '.join(missing_layers)}. "
                        "Add them in Board Setup > Board Stackup (the plan's layer count is in BoardLab), then try again.")
    return problems


def footprint_moves(change: dict, board: BoardSnapshot) -> list[dict]:
    """The footprint entries that still have to move (the others are already where the plan puts them)."""
    return [fp for fp in change.get("footprints", []) if not _at(board.footprints[fp["ref"]], fp["to"])]


def _snap(nm) -> int:
    return round(int(nm) / POSITION_TOLERANCE_NM)


def _track_key(net, layer, x1, y1, x2, y2, width) -> tuple:
    a, b = (_snap(x1), _snap(y1)), (_snap(x2), _snap(y2))
    return (net, layer, *min(a, b), *max(a, b), _snap(width))  # a segment drawn either way round is the same


def copper_difference(change: dict, board: BoardSnapshot) -> tuple[list[tuple], list[tuple]]:
    """(in the changeset but not on the board, on the board but not in the changeset), comparing the
    copper of the nets the changeset replaces on a 10 um grid (a segment either way round is the same)."""
    want = Counter(_track_key(t["net"], t["layer"], *t["start"], *t["end"], t["width"]) for t in change.get("tracks", []))
    want.update(("via", v["net"], _snap(v["at"][0]), _snap(v["at"][1]), _snap(v["diameter"]), _snap(v["drill"]))
                for v in change.get("vias", []))
    have = Counter(_track_key(*t) for t in board.tracks)
    have.update(("via", n, _snap(x), _snap(y), _snap(d), _snap(dr)) for n, x, y, d, dr in board.vias)
    return sorted((want - have).elements()), sorted((have - want).elements())


def track_key(net, layer, x1, y1, x2, y2, width) -> tuple:
    """One track as compared between the plan and the board (and remembered between updates)."""
    return _track_key(net, layer, x1, y1, x2, y2, width)


def via_key(net, x, y, diameter, drill) -> tuple:
    return ("via", net, _snap(x), _snap(y), _snap(diameter), _snap(drill))


def _change_keys(change: dict) -> tuple[list[tuple[tuple, dict]], list[tuple[tuple, dict]]]:
    tracks = [(track_key(t["net"], t["layer"], *t["start"], *t["end"], t["width"]), t) for t in change.get("tracks", [])]
    vias = [(via_key(v["net"], *v["at"], v["diameter"], v["drill"]), v) for v in change.get("vias", [])]
    return tracks, vias


def remembered(keys) -> set[tuple]:
    """Keys saved as JSON lists (links.json) back to tuples."""
    return {tuple(k) for k in keys or []}


@dataclass
class BoardUpdate:
    """What "Update board" does to the open board, worked out before anything is written.

    * ``moves``: the footprints not yet where BoardLab puts them.
    * ``add_tracks`` / ``add_vias``: the plan's copper the board does not have yet (copper already
      there is left untouched, not removed and drawn again).
    * ``stale``: copper on the plan's nets that an earlier update put there and the plan no longer
      has (the router changed its mind): removed.
    * ``local``: the designer's own copper on the plan's nets (not from an earlier update): kept,
      unless the designer chooses to replace it.
    * ``plan_keys``: every piece of the plan's copper, remembered so the next update can tell
      BoardLab's copper from the designer's.
    Board copper is given as (key, item) pairs, the item being whatever the caller removes."""
    moves: list[dict] = field(default_factory=list)
    add_tracks: list[dict] = field(default_factory=list)
    add_vias: list[dict] = field(default_factory=list)
    stale: list = field(default_factory=list)
    local: list = field(default_factory=list)
    plan_keys: list[tuple] = field(default_factory=list)
    add_zones: list[dict] = field(default_factory=list)  # BoardLab zones the board lacks (or has with another outline)
    remove_zones: list = field(default_factory=list)  # BoardLab zones to take off: replaced, or dropped by the plan

    @property
    def nothing_to_do(self) -> bool:
        return not (self.moves or self.add_tracks or self.add_vias or self.stale or self.add_zones or self.remove_zones)


def zone_key(name: str, outline) -> tuple:
    """One zone as compared between the plan and the board: its name and outline on the 10 um grid."""
    return (name, tuple((_snap(x), _snap(y)) for x, y in outline))


def zone_update(change: dict, board_zones: list[tuple[str, list, object]], applied_before: set[str]) -> tuple[list[dict], list]:
    """(zones to add, board zone items to remove). ``board_zones`` are the board's zones named by BoardLab
    as (name, outline in nm, item). A zone of the plan already there with the same outline is kept; one
    with the same name and another outline is replaced; a BoardLab zone an earlier update put there and
    the plan no longer has is removed. A zone named "BoardLab ..." that no update put there is left alone."""
    want = {z["name"]: z for z in change.get("zones", [])}
    have = {}
    remove = []
    for name, outline, item in board_zones:
        if name in want and zone_key(name, outline) == zone_key(name, want[name]["outline"]) and name not in have:
            have[name] = item
        elif name in want or name in applied_before:
            remove.append(item)
    return [z for n, z in want.items() if n not in have], remove


def plan_update(change: dict, board: BoardSnapshot, copper: list[tuple[tuple, object]],
                applied_before: set[tuple]) -> BoardUpdate:
    """Compare the changeset with the open board. ``copper`` is the board's copper on the plan's
    nets as (key, item); ``applied_before`` the keys an earlier update of this board put there."""
    tracks, vias = _change_keys(change)
    want = Counter(k for k, _ in tracks + vias)
    have = Counter(k for k, _ in copper)
    up = BoardUpdate(moves=footprint_moves(change, board), plan_keys=sorted(want.elements()))
    spare = Counter(have)
    for key, t in tracks:
        if spare[key] > 0:
            spare[key] -= 1
        else:
            up.add_tracks.append(t)
    for key, v in vias:
        if spare[key] > 0:
            spare[key] -= 1
        else:
            up.add_vias.append(v)
    keep = Counter(want)
    for key, item in copper:
        if keep[key] > 0:
            keep[key] -= 1
        elif key in applied_before:
            up.stale.append(item)
        else:
            up.local.append(item)
    return up


def preview_text(change: dict, up: BoardUpdate, backup: str | None) -> str:
    """The preview shown before the board is touched."""
    lines = [f"\"{change.get('title', 'BoardLab changes')}\" on this board:"]
    listed = change.get("changes") or []
    if len(listed) > 1 or (listed and listed[0].get("title") != change.get("title")):
        lines.append("Approved on BoardLab's Layout page, in this order:")
        for i, c in enumerate(listed, 1):
            what = ", ".join(f"{c[k]} {k}" for k in ("footprints", "tracks", "vias", "zones") if c.get(k))
            lines.append(f"  {i}. {c.get('title', c.get('kind', ''))}" + (f" ({what})" if what else ""))
    if up.moves:
        names = ", ".join(fp["ref"] for fp in up.moves[:12]) + (" ..." if len(up.moves) > 12 else "")
        lines.append(f"- move {len(up.moves)} parts: {names}")
    if up.add_tracks or up.add_vias:
        nets = {t["net"] for t in up.add_tracks} | {v["net"] for v in up.add_vias}
        lines.append(f"- add {len(up.add_tracks)} tracks and {len(up.add_vias)} vias on {len(nets)} nets")
    if up.stale:
        lines.append(f"- remove {len(up.stale)} tracks and vias that an earlier BoardLab update put there "
                     "and the new routing no longer uses")
    if up.add_zones:
        lines.append(f"- add {len(up.add_zones)} keep-out areas: " + ", ".join(z["name"] for z in up.add_zones[:6]))
    if up.remove_zones:
        lines.append(f"- remove {len(up.remove_zones)} BoardLab keep-out areas that the changes replace or no longer have")
    if up.local:
        lines.append(f"- your own {len(up.local)} tracks and vias on these nets are KEPT (run DRC after); "
                     "choose \"Replace mine\" to remove them instead")
    lines.append("Nothing else on the board changes. Ctrl+Z undoes it all in one step.")
    if backup:
        lines.append(f"A copy of the board as it is now is saved first in {backup}")
    return "\n".join(lines)


def copper_is_current(change: dict, board: BoardSnapshot) -> bool:
    """True when every piece of the changeset's copper is already on the board, so applying it again
    would only churn the undo history and the team's diary. Other copper on those nets (the designer's
    own, or a track KiCad moved onto the net by connectivity) does not count against it: it is kept."""
    missing, _extra = copper_difference(change, board)
    return not missing


def place_findings(report: dict, board: BoardSnapshot) -> tuple[list[tuple[dict, int, int]], list[dict]]:
    """((finding, x, y) for each finding placed on the board, the findings with no place).

    A finding goes on its first pin found on the board, else on its part, else on its net."""
    if report.get("format") != FINDINGS_FORMAT:
        raise ValueError("This BoardLab Sync is too old for the findings BoardLab sent: update the plugin.")
    placed: list[tuple[dict, int, int]] = []
    unplaced: list[dict] = []
    for f in report.get("findings", []):
        spot = None
        for pin in f.get("pins") or []:
            ref, _, number = str(pin).partition(".")
            spot = board.pads.get(ref, {}).get(number)
            if spot:
                break
        if spot is None and f.get("ref") in board.footprints:
            x, y, _a = board.footprints[f["ref"]]
            spot = (x, y)
        if spot is None and f.get("net"):
            spot = board.net_points.get(f["net"])
        if spot is None:
            unplaced.append(f)
        else:
            placed.append((f, spot[0], spot[1]))
    return placed, unplaced


def note_text(f: dict) -> str:
    head = f"{NOTE_MARK} {f['id']} {f.get('severity', '')}" + (f" ({f['state']})" if f.get("state") != "open" else "")
    lines = [head, f.get("title", "")]
    if f.get("fix"):
        lines.append("Fix: " + f["fix"])
    return "\n".join(_wrap(line, 60) for line in lines if line)


def _wrap(text: str, width: int) -> str:
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        out.append(line)
    return "\n".join(out)


SCHEMATIC_SUFFIXES = (".kicad_sch", ".kicad_pro")


def schematic_zip(folder: Path) -> tuple[bytes, int]:
    """The project's .kicad_pro and every .kicad_sch under its folder (hierarchical sheets in
    sub-folders included, backups left out), zipped with their relative paths."""
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder)
            if (not path.is_file() or not path.name.lower().endswith(SCHEMATIC_SUFFIXES)
                    or len(rel.parts) > 6 or any(p.endswith("-backups") or p.startswith(".") for p in rel.parts)):
                continue
            zf.write(path, rel.as_posix())
            count += path.name.lower().endswith(".kicad_sch")
    return buf.getvalue(), count


# -- new versions -------------------------------------------------------------------------------

UPDATE_CHECK_EVERY_S = 24 * 3600  # one look at the server a day at most
UPDATE_SNOOZE_S = 24 * 3600  # "Later" asks again the next day


def version_tuple(v: str) -> tuple[int, ...]:
    out = []
    for part in str(v or "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest: str, current: str) -> bool:
    return bool(latest) and version_tuple(latest) > version_tuple(current)


def update_due(state: dict, now: float) -> bool:
    """Time to ask the server again (state is the remembered update.json)."""
    return now - float(state.get("checked_at", 0)) >= UPDATE_CHECK_EVERY_S


def should_offer(latest: str, current: str, state: dict, now: float) -> bool:
    """A newer version the designer has not put off ("Later") in the last day."""
    if not is_newer(latest, current):
        return False
    return not (state.get("snoozed_version") == latest and now < float(state.get("snoozed_until", 0)))


def package_files(data: bytes, expected_sha256: str | None = None) -> dict[str, bytes]:
    """The plugin files (relative path -> bytes) of a downloaded PCM package, checked before anything
    is written: the checksum the server published, this plugin's identifier, a plugin.json, and no
    path that leaves the plugin folder."""
    import hashlib
    import json as _json

    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256.lower():
        raise ValueError("the download is damaged (checksum differs): nothing was changed")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("the download is not a plugin package: nothing was changed") from None
    try:
        meta = _json.loads(zf.read("metadata.json"))
    except (KeyError, ValueError):
        raise ValueError("the package has no metadata.json: nothing was changed") from None
    if meta.get("identifier") != PACKAGE_ID:
        raise ValueError(f"the package is {meta.get('identifier')!r}, not BoardLab Sync: nothing was changed")
    files: dict[str, bytes] = {}
    for name in zf.namelist():
        top, _, rest = name.partition("/")
        if top != "plugins" or not rest or name.endswith("/"):
            continue
        parts = rest.split("/")
        if rest.startswith("/") or "\\" in rest or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
            raise ValueError(f"the package has an unsafe path ({name}): nothing was changed")
        files[rest] = zf.read(name)
    if "plugin.json" not in files or "sync.py" not in files:
        raise ValueError("the package has no plugin.json or sync.py: nothing was changed")
    return files


def unpack_project(data: bytes, folder: Path) -> Path:
    """Write the KiCad project BoardLab sent (a zip with one top folder ``<name>/``) into a new
    folder under ``folder`` and return its ``.kicad_pro``. Nothing already on disk is overwritten:
    when ``<name>`` exists the copy goes to ``<name>-2``, ``<name>-3``... Fail-closed: a damaged
    zip, a path that leaves the folder or a zip without exactly one project file writes nothing."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("BoardLab did not send a KiCad project: nothing was written") from None
    files: dict[str, bytes] = {}
    tops = set()
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        parts = name.split("/")
        if (name.startswith("/") or "\\" in name or len(parts) < 2
                or any(p in ("", ".", "..") for p in parts) or ":" in name):
            raise ValueError(f"the project has an unsafe path ({name}): nothing was written")
        tops.add(parts[0])
        files["/".join(parts[1:])] = zf.read(name)
    projects = [n for n in files if "/" not in n and n.endswith(".kicad_pro")]
    if len(tops) != 1 or len(projects) != 1:
        raise ValueError("BoardLab sent no KiCad project file: nothing was written")
    top = tops.pop()
    target, n = folder / top, 1
    while target.exists():
        n += 1
        target = folder / f"{top}-{n}"
    for rel, blob in files.items():
        path = target.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    return target / projects[0]

"""Build the KiCad PCM package zip and the metadata.json for the official KiCad metadata repository.

Usage: python3 scripts/build_pcm.py 26.11.3
Writes pcm/boardlab-sync-<version>.zip (copy it to dist/ and push tag v<version>) and
pcm/metadata-repo/packages/it.boardlab.sync/metadata.json (submit it to gitlab.com/kicad/addons/metadata).
"""
import hashlib, io, json, os, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/manvalan/boardlab-kicad-sync"
V = sys.argv[1] if len(sys.argv) > 1 else "26.11.3"

meta = {
    "$schema": "https://go.kicad.org/pcm/schemas/v2",
    "name": "BoardLab Sync",
    "description": "Send your board to BoardLab for review and get the findings back as notes.",
    "description_full": (
        "BoardLab Sync links the board open in the KiCad 10 PCB editor to a project on boardlab.it "
        "(you confirm the project on the website with a short code). Send board uploads the board and its "
        "schematic sheets; BoardLab checks every part against its datasheet and the standard interfaces. "
        "Receive findings pins the open findings as notes on User.Comments; Update board brings "
        "BoardLab's placement and routing back into the board: a preview first, a backup copy, then only the "
        "changes as one undo step, your own tracks kept. Requires a BoardLab account (first board up to 30 ICs free) "
        "and Preferences > Plugins > Enable KiCad API."),
    "identifier": "it.boardlab.sync",
    "type": "plugin",
    "author": {"name": "Michele Bigi", "contact": {"web": "https://boardlab.it"}},
    "maintainer": {"name": "Michele Bigi", "contact": {"web": REPO}},
    "license": "Apache-2.0",
    "resources": {"homepage": "https://boardlab.it/kicad", "Source": REPO, "Issues": REPO + "/issues"},
    "tags": ["review", "drc", "datasheet", "collaboration", "ipc"],
    "versions": [{"version": V, "status": "stable", "kicad_version": "10.0", "runtime": "ipc"}],
}

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("metadata.json", json.dumps(meta, indent=2))
    plugins = os.path.join(ROOT, "plugins")
    for root, _, files in sorted(os.walk(plugins)):
        for f in sorted(files):
            if f.endswith(".pyc"):
                continue
            p = os.path.join(root, f)
            z.write(p, "plugins/" + os.path.relpath(p, plugins).replace(os.sep, "/"))
    z.write(os.path.join(ROOT, "resources", "icon.png"), "resources/icon.png")
data = buf.getvalue()
os.makedirs(os.path.join(ROOT, "pcm"), exist_ok=True)
with open(os.path.join(ROOT, "pcm", f"boardlab-sync-{V}.zip"), "wb") as fh:
    fh.write(data)

with zipfile.ZipFile(io.BytesIO(data)) as z:
    install = sum(i.file_size for i in z.infolist())
v = meta["versions"][0]
v.update(download_url=f"https://raw.githubusercontent.com/manvalan/boardlab-kicad-sync/v{V}/dist/boardlab-sync-{V}.zip",
         download_sha256=hashlib.sha256(data).hexdigest(), download_size=len(data), install_size=install)
out = os.path.join(ROOT, "pcm", "metadata-repo", "packages", meta["identifier"])
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "metadata.json"), "w") as fh:
    json.dump(meta, fh, indent=2)
print(json.dumps(v, indent=2))

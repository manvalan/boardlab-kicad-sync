# BoardLab Sync for KiCad 10

Links a board open in the KiCad PCB editor to a BoardLab project, both ways:

| Button | What it does |
|---|---|
| BoardLab: Link | Opens boardlab.it with a short code; you pick the project and confirm there. |
| BoardLab: Receive changes | Applies BoardLab's changes (the saved DDR plan) to the open board as one undo step (Ctrl+Z). Refused, with the reason, when the board is not the one the plan was made for. |
| BoardLab: Receive findings | Pins the open findings as notes on User.Comments (a ring and a text). Each run replaces the previous BoardLab notes. |
| BoardLab: Send board | Sends the board as it is open now, and the schematic saved in the same folder (all hierarchical sheets), to the linked project. KiCad 10 gives plugins no schematic editor, so save the schematic first. |

Every link, every board sent and every change applied goes into the project diary and is emailed to the other team members.

## Install

1. Download `boardlab-sync-kicad.zip` from boardlab.it (sidebar: KiCad plugin). Do not unzip it.
2. KiCad 10, main window: **Plugin and Content Manager > Install from File...** and pick the zip. BoardLab Sync shows in the Installed tab.
3. If KiCad asks to enable the KiCad API, say yes (Preferences > Plugins > **Enable KiCad API**).
4. Open the board in the PCB editor: the four buttons are on the top toolbar. The first time, KiCad installs `kicad-python` in the plugin's own Python environment (needs internet).

Linux (Debian, Ubuntu): KiCad builds that environment with the system Python, which needs the venv package: `sudo apt install python3-venv`, then restart KiCad. On macOS and Windows KiCad's own Python already has it.

Messages appear in the PCB editor's message panel (bottom right of the status bar). When an action fails, a dialog says why, and every message is also written to `boardlab-sync.log` next to the links file.

The links (one per board file) are kept in `boardlab-sync/links.json` in your user configuration folder. Set `BOARDLAB_URL` to use another BoardLab server.

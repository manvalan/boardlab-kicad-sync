# BoardLab Sync for KiCad 10

KiCad 10 plugin (IPC API) that links the board open in the PCB editor to a project on [BoardLab](https://boardlab.it), a hardware review service for KiCad projects.

| Button | What it does |
|---|---|
| BoardLab: Link | Opens boardlab.it with a short code; you pick the project and confirm it there. |
| BoardLab: Send board | Sends the open board and the schematic saved next to it (all sheets) to the linked project. |
| BoardLab: Receive findings | Pins the open findings as notes on User.Comments (a ring and a text). Each run replaces the previous BoardLab notes. |
| BoardLab: Update board | Brings BoardLab's placement and routing (e.g. a saved DDR plan) back into the open board. A preview says what will change and a copy of the board is saved in `boardlab-backups/` first; then only the changes are written, as one undo step. Your own tracks and vias are kept unless you choose "Replace mine". Refused, with the reason, when the board does not match. |

The plugin is only a client: the review runs on boardlab.it and needs an account (the first board up to 30 ICs is free). A plugin never reaches a project without your confirmation on the website.

## Install

From KiCad 10: **Plugin and Content Manager**, search "BoardLab Sync", Install. Or download the zip from [dist/](dist/) and use **Install from File...** without unzipping it.

If KiCad asks, enable **Preferences > Plugins > Enable KiCad API**. On first run KiCad installs `kicad-python` in the plugin's own Python environment (needs internet). Linux (Debian, Ubuntu): `sudo apt install python3-venv`, then restart KiCad.

Set `BOARDLAB_URL` to use another BoardLab server. Links are stored in `boardlab-sync/links.json` in your KiCad user configuration folder; log in `boardlab-sync.log` next to it.

## Data sent

Only when you press a button: the board file, the `.kicad_pro` and the `.kicad_sch` files in the board's folder (Send board). The link token is scoped to that one project's KiCad routes.

## License

Apache-2.0. See [LICENSE](LICENSE).

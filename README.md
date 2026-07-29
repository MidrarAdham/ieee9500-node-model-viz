# IEEE 9500 Interactive Debugger — Version 1

This first version uses the node and edge data already embedded in your existing HTML viewer. It keeps the complete topology in Python/NetworkX and sends only the selected debug subgraph to the browser.

## Current features

- Exact object search
- Configurable neighbor-hop display
- Shortest connected trace to a node marked with `csip_level = system`
- Object property panel
- Clickable nodes
- Original geographic positions from the HTML

## Folder setup

Place your original HTML beside `app.py`, or pass its path on the command line:

```text
ieee9500_debugger/
├── app.py
├── requirements.txt
├── README.md
├── assets/
│   └── style.css
└── ieee9500_csip.html
```

## Install

From the debugger folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell, activate with:

```powershell
.venv\Scripts\Activate.ps1
```

## Run

When the HTML is in the same folder:

```bash
python app.py
```

Or provide its path:

```bash
python app.py /path/to/ieee9500_csip.html
```

Open the address printed in the terminal, normally:

```text
http://127.0.0.1:8050
```

## Important limitation

The source trace currently uses graph connectivity and chooses the shortest path to the nearest object whose `csip_level` is `system`. It does not yet evaluate switch state, electrical direction, phases, or GridLAB-D parent relationships. Those belong in the next diagnostic stage after this interface is verified.

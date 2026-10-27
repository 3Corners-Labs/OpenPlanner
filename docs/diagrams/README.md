# Architecture plates

Shareable views of OpenPlanner as built (Dutch, for stakeholders). All files here are generated;
edit `scripts/diagrams/content.py` and run `make diagrams` (needs the draw.io desktop app and
Chrome locally).

| File | What |
|---|---|
| `openplanner-architectuur.drawio` | Editable source, one page per plate |
| `openplanner-architectuur.pdf` | Complete document, A4 landscape: plates and building-block register |
| `openplanner-architectuur.docx` | The same document in Word |
| `openplanner-architectuur-platen.pdf` | The three plates only, as vector pages |
| `plaat-1.png` … `plaat-3.png` | The plates as images, for slides and mail |

The plates:

1. **Hoofdlijn** — seventeen building blocks (B1–B17) in four families, actors and external systems.
2. **Softwarecomponenten** — the packages under `app/` per layer, in import direction.
3. **Integraties en adapters** — per external system: port, adapter, connection and the data
   that goes there and back.

`tests/scripts/test_diagrams.py` checks that every block, tag and port on the plates exists.

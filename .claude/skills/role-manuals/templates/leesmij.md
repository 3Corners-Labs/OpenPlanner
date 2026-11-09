# Handleidingen versie {{version}}

Vastgestelde versie van de handleidingen per rol en de aanpak voor adoptie, communicatie en
training. Deze map verandert niet meer.

- **Versie:** {{version_text}}.
- **Software:** {{software_version}}; tag `{{release}}`, commit `{{commit}}`. De
  schermafdrukken zijn op {{captured}} opgenomen; tijd en commit per afbeelding staan in
  `evidence/log.md`.
- **Bevroren:** {{frozen}}.
- **Nagelezen en open:** `{{record}}`. De controle staat in `evidence/check.md`.

| Map of bestand | Inhoud |
|---|---|
| `build/` | De handleidingen als PDF, Word en HTML, met `manuals.json` en `help-index.json` |
| `sources/` | De bron in Markdown |
| `img/` | De schermafdrukken |
| `evidence/` | `log.md` (per schermafdruk: commit, tijdstip, rol, adres, afdekkingen), `check.md`, `notes.json`, `shots.json`, `state.json` en `diff/` (het verschil met de opname daarvoor) |
| `variables.json` en de andere Markdown-bestanden | De instellingen, de redactieregels en de lijst van schermafdrukken van deze versie |
| `MANIFEST.sha256` | Het SHA-256-controlegetal van elk ander bestand in deze map |

Controleren dat er niets is veranderd, vanuit deze map:

    shasum -a 256 -c MANIFEST.sha256

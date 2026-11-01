# 0092. Uploads are validated by structure, not by a virus scanner

Status: accepted (2026-11-01), requested by the product owner after the ASVS 5.0.0 L2 audit of
1 November 2026 (V5.1.1, V5.2.2, V5.4.3). Amends ADR 0020 and ADR 0041 (branding, fonts,
theme file), ADR 0037 (closure images), ADR 0064/0070 (own certificate) and ADR 0089
(configuration package). Not built; see *Build status*.

## Context

Administrators upload files; participants' browsers receive some of them. The audit found no
virus scan in any upload path (V5.4.3, a deviation), a font check that looks at four bytes and
the length only (V5.2.2), and no single place that states, per upload, the allowed type, size
and unpacked size (V5.1.1).

What is there today:

| Upload | Who | Allowed | Limit | What happens |
|---|---|---|---|---|
| Page and closure image | admin with the page's permission | PNG, JPEG, WebP by magic bytes; never SVG | 500 kB (`MAX_IMAGE_BYTES`) | structure parsed, metadata chunks stripped (`app/domain/images.py`) |
| Logo | `branding.manage` | as images | 200 kB (`MAX_LOGO_BYTES`) | as images |
| Font | `branding.manage` | WOFF2 by signature | 250 kB (`MAX_FONT_BYTES`) | stored as received |
| Theme file | `branding.manage` | `--booking-*` declarations | 8 kB (`MAX_THEME_FILE_BYTES`) | parsed token by token, re-rendered (`app/domain/theme.py`) |
| Own certificate | superuser | PEM chain and key | 64 kB (`MAX_PEM_BYTES`) | parsed with `cryptography`; key to the volume, never the database |
| Configuration package | superuser or API scope `configuration:transfer` | sealed package | `CONFIGURATION_PACKAGE_MAX_MB` (64) | opened with the passphrase, JSON validated per table |

Everything is served from the application with an exact `Content-Type`, `nosniff` and a CSP
of `default-src 'none'` for assets. Only authenticated administrators can upload.

A virus scanner (ClamAV or an ICAP service) would add a container, a daily signature download
from the internet, a new dependency, and a failure mode on every upload. For the closed list
above it adds little: the types are recognised by structure, and the formats that carry active
content (SVG, HTML, PDF, office files, archives) are not accepted. The font is the weak spot.
Font parsers in browsers have had memory-safety bugs, and a WOFF2 file is only checked for its
first four bytes. Its unpacked size (`totalSfntSize`) is not bounded at all.

## Decision

1. **No virus scanner in the product.** V5.4.3 is a recorded deviation with these
   compensating controls: a closed list of types recognised by structure, never by name or
   client type; no format with active content; uploads only by authenticated administrators
   with the matching permission, audited; served with exact type, `nosniff` and a strict CSP.
   `docs/threat-model.md` names the deviation.
2. **WOFF2 is validated by structure.** `FontFile.from_upload` reads the WOFF2 header and
   table directory and refuses the file when:
   - `length` differs from the file size;
   - `numTables` is 0 or above 64;
   - `totalSfntSize` is above the setting `max_font_unpacked_bytes`;
   - the compressed data does not fit in the file;
   - a reserved field is not 0.

   No new dependency: the header is fixed-size, and the table directory uses the WOFF2
   variable-length integers, which take a few lines. The refusal carries an error code, no
   text.
3. **The table above is the documentation of record** for V5.1.1. A PR that adds an upload
   adds its row here (by a new ADR when the decision changes) and in `docs/threat-model.md`.
4. **Uploaded file names are never used.** Downloads name their file from the server
   (`content-disposition` with a fixed pattern); a test asserts the pattern on every download
   route.
5. **A scanner can be added later behind a port.** If a client demands a scan, it comes as a
   `FileScanner` Protocol in `app/domain/ports.py` with an ICAP adapter, called before the
   domain validation, failing closed. That is a new ADR; it is not built now.

## Settings

| Key | Class | Default | Bounds | Permission | Note |
|---|---|---|---|---|---|
| `max_font_unpacked_bytes` | C platform | 2 000 000 | 256 000–8 000 000 | `settings.platform.manage` | the WOFF2 `totalSfntSize` ceiling; a family with many glyphs may need more |

**Fixed on purpose:** the list of accepted types (a security boundary, not a tuning value);
`numTables` ≤ 64 (the OpenType format has fewer table kinds); the existing byte limits stay as
they are in this ADR.

## Build status

| Step | What | PR | State |
|---|---|---|---|
| 0 | ADR accepted | #309 | in PR |
| 1 | WOFF2 structural validation and `max_font_unpacked_bytes`, tests with malformed fonts | | planned |
| 2 | Test on every download route: server-chosen file name, fixed pattern | | planned |
| 3 | Deviation and compensating controls in `docs/threat-model.md` | | planned |

## Consequences

- A font that browsers accept today may be refused if its header lies about its size; the
  manual topic on branding says what to do (re-export the font).
- The audit's V5.4.3 stays a deviation; an assessor reads why here.
- Step 1 touches `app/config.py` and `app/domain/settings_registry.py`; it runs one at a time
  with other PRs on those files. Step 2 adds tests only.

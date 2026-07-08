# Publication Whitepapers

Standalone, **publish-ready** technical whitepapers for external distribution.
These documents contain **embedded data only** — no code paths, repository links, or
internal document cross-references.

> **Disclaimer:** All figures are simulation outputs unless marked *measured*.
> Prefix external claims with “simulation shows…”

## Documents

| Whitepaper | Markdown | PDF |
|------------|----------|-----|
| ATPE — Adaptive Torque & Power Engine | [ATPE-Whitepaper-v1.0.md](ATPE-Whitepaper-v1.0.md) | [pdf/ATPE-Whitepaper-v1.0.pdf](pdf/ATPE-Whitepaper-v1.0.pdf) |
| PCMRITMS — Inertial Torque Buffer | [PCMRITMS-Whitepaper-v1.0.md](PCMRITMS-Whitepaper-v1.0.md) | [pdf/PCMRITMS-Whitepaper-v1.0.pdf](pdf/PCMRITMS-Whitepaper-v1.0.pdf) |
| Project Phoenix — Integrated System | [Phoenix-Integrated-Whitepaper-v1.0.md](Phoenix-Integrated-Whitepaper-v1.0.md) | [pdf/Phoenix-Integrated-Whitepaper-v1.0.pdf](pdf/Phoenix-Integrated-Whitepaper-v1.0.pdf) |

## Regenerate PDFs

```powershell
.venv\Scripts\python.exe scripts\export_whitepaper_pdfs.py
```

Requires `fpdf2` (installed automatically on first run).

## Internal engineering copies

The repository also keeps engineering-linked versions with code traceability:

- `docs/13-atpe-whitepaper.md` — links to twin modules (internal)
- `docs/14-phoenix-integrated-whitepaper.md` — links to twin modules (internal)

Use the files in **this folder** for investor emails, data rooms, and public disclosure.

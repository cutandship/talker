# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do **not** open a public issue.

- Email: **cutandship@proton.me**
- Or use GitHub's private **"Report a vulnerability"** (repo → Security → Advisories).

You'll get an acknowledgement within a few days and updates as a fix is worked on.
Responsible disclosure is appreciated; please give a reasonable window before
going public.

## Good to know (where to look)

Talker runs locally on Windows and is privacy-first — on the offline path, audio
and dictated text never leave the machine. Areas most worth scrutiny:

- **Local HTTP API** (`api_server.py`, `web_ui.py`): binds to `127.0.0.1` and
  requires a bearer token stored in `.api_token`. Reports about token handling,
  CORS, or it being reachable beyond localhost are in scope.
- **Global keyboard hook** (`keyboard`) and **text injection** (`SendInput` /
  clipboard, `injector.py`): anything that could let another process capture
  dictation or inject into the wrong window.
- **On-disk data**: `config.toml`, `history.json` (local only).

## Out of scope

- Third-party model weights and pip dependencies — report those to their
  upstream projects.
- Issues that require an already-compromised machine or physical access.

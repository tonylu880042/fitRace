# FitRaceStudio — guidance for Claude

Python 3.11+ asyncio system: Edge Nodes (UART → antenna board → BLE/FTMS
equipment) publish MQTT telemetry to a central Hub (FastAPI) driving a live
race dashboard. Local-network only during live races. Specs: DEPLOYMENT.md,
TELEMETRY_SPEC.md, OTA_UPDATE.md.

## Hard rules
- TDD: write the failing test in `tests/` first, then minimal implementation.
  All tests must pass before any commit — never commit red.
- Before commit: `pytest`, then `black .` and `ruff check . --fix`.
  No leftover `print()` / `breakpoint()`.
- Conventional Commits: `<type>(<scope>): <description>` (feat/fix/test/refactor/docs/chore).
- Clean Architecture per module (domain → usecases → adapters → infrastructure);
  dependencies point inward only — inner layers never import FastAPI/bleak/MQTT.

## Page responsibility boundaries (never violate)
- Dashboard `/` is a display-only projection screen (leaderboard, countdown,
  race state, QR, device status). No controls of any kind.
- Game Admin `/gameAdmin`: race operation — mode, leaderboard display, sounds,
  Start/Stop/Reset.
- System Admin `/systemAdmin`: technical maintenance — station assignment,
  edge nodes, updates, power.
- Dashboard behavior changes are driven by backend state / WebSocket events,
  never by local controls on the dashboard page.

## i18n
- Locale dicts live in `infrastructure/locales/` (zh_tw.json, en.json).
  Frontends fetch translations via API — never hardcode zh/en strings in
  static pages.

## Gotchas
- `EDGE_SETUP_HTML` (edge_node/infrastructure/fastapi/app.py) is a Python
  string: Python un-escapes backslashes, which has silently broken the
  embedded page JS before. Avoid backslashes in that JS; after editing,
  extract the JS and run `node --check` on it.
- Never trigger BLE SCAN while other serial operations are in flight — the
  UART is contended and the antenna board can wedge.
- `scripts/deploy.sh` can clobber the device's `config.json` — verify before
  deploying.

Full rationale and TDD walkthrough: AGENT.md.

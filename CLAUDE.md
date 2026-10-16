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

## Delegation (never violate)
- Implementation work is delegated to a subagent on a low-tier model. This
  session writes the spec, reviews the result, and reports — it does not
  implement features itself.
- Every dispatch spec must require: TDD, full `pytest` green before EACH
  commit, `node --check` on any edited page JS, and ONE genuine semantic
  mutation per commit proving the new tests fail without the fix.
- A mutation that only raises TypeError/NameError, breaks a call signature,
  or crashes is NOT proof — it must be a behaviour change with valid,
  running code. Reject such a report and require it re-run.
- Never accept a subagent's self-report. Independently re-run `pytest`, read
  the diff, and design a mutation on an axis the subagent did not test.
  Batches have self-reported success on defects that were still present.
- Known assertion traps, check for these every review:
  - a test satisfied by a nearby *comment* containing the same substring, so
    deleting the real code leaves the suite green;
  - a regression test asserting only on the recorded UART command list while
    a destructive step reaches the board through an injected collaborator;
  - two independent predicates that must stay exact complements (prefer one
    partition pass) on any path that deletes a venue's setup.
- When several subagents share the tree, give each an explicit list of files
  it must not touch, and require staging by path — never `git add -A`.

## Page responsibility boundaries (never violate)
- Dashboard `/` is a display-only projection screen (leaderboard, countdown,
  race state, QR, device status). No controls of any kind.
- Game Admin `/gameAdmin`: race operation — mode, leaderboard display, sounds,
  Start/Stop/Reset (shared with `/teams` for relay races).
- Teams `/teams`: relay team library, team→station assignment, and
  Start/Stop/Next Race for relay races. Race rules and projector settings stay
  in Game Admin.
- System Admin `/systemAdmin`: technical maintenance — station assignment,
  edge nodes, updates, power.
- Class Admin `/classAdmin`: class operation — plan editing, start/stop/reset
  of a class, and switching the projector into class mode.
- Dashboard behavior changes are driven by backend state / WebSocket events,
  never by local controls on the dashboard page.
- Game Admin and Class Admin are mutually exclusive session owners — the hub
  runs one session mode (race or class) at a time, and switching modes is
  blocked while a session is RUNNING.

## i18n
- Locale dicts live in `hub_server/infrastructure/locales/` (en-US.json,
  zh-TW.json, zh-CN.json, it.json, fr.json, de-CH.json, sv.json). Frontends
  fetch translations via API (`/api/locales`) — never hardcode zh/en strings
  in static pages. Pages that carry their own text use a key prefix
  (`game_admin.`, `system_admin.`, `signup_page.`, `docs.`).
- Completeness: en-US, zh-TW and zh-CN are the complete locales and must have
  exactly the same key set. it, fr, de-CH and sv may be missing keys: the hub
  serves them merged over en-US (a missing key shows English), and a
  non-English locale never has a key en-US lacks. New keys go into en-US,
  zh-TW and zh-CN only.
- zh-CN is generated from zh-TW once (OpenCC `tw2sp`, then hand-reviewed for
  mainland wording); edit the JSON afterwards, never add OpenCC to the repo.

## Gotchas
- `EDGE_SETUP_HTML` (edge_node/infrastructure/fastapi/app.py) is a Python
  string: Python un-escapes backslashes, which has silently broken the
  embedded page JS before. Avoid backslashes in that JS; after editing,
  extract the JS and run `node --check` on it.
- Never trigger BLE SCAN while other serial operations are in flight — the
  UART is contended and the antenna board can wedge.
- `scripts/deploy.sh` can clobber the device's `config.json` — verify before
  deploying.

## Release signing
- The OTA release private key never lives in this repo and must never be
  read, printed, committed, or uploaded — only its path is passed to openssl.
- Sign the stable manifest with OpenSSL 3 (macOS LibreSSL lacks -rawin for ed25519):
  `openssl pkeyutl -sign -rawin -inkey <private key> -in deploy_update/upload_config/channels/stable/manifest.json -out deploy_update/upload_config/channels/stable/manifest.json.sig`
- Verify with `fitrace_common/release-ed25519-public.pem` before uploading;
  any edit to manifest.json after signing invalidates the signature.

Full rationale and TDD walkthrough: AGENT.md.

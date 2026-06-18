# FitRaceStudio Session Handoff

Updated: 2026-06-18

## Current Product Direction

FitRaceStudio is now organized around separated operator roles:

- `gameAdmin`: for venue coaches and on-site race operators.
- `systemAdmin`: for technical staff responsible for device nodes, station mappings, updates, and system maintenance.
- `signup`: athlete-only registration. It should not contain race or system management controls.
- `/admin`: a role selection portal that links to `gameAdmin` and `systemAdmin`.

The architectural decision is to manage Edge Node/device setup centrally through the Hub UI where possible. Individual Edge local pages are still useful for local signal checks and equipment discovery, but ongoing venue operations should use Hub-side pages.

## Completed In This Session

### Admin UI Split

- Added `/admin` as a standalone management portal.
- Added `/gameAdmin` route and page.
- Added `/systemAdmin` route and page.
- Removed Race Control and System Power from `signup`.
- Moved editable Station Assignment out of `gameAdmin`.
- `gameAdmin` now has:
  - Race Control
  - read-only Station Status
  - no editable station assignment controls
  - no System Power controls
- `systemAdmin` now has:
  - Edge Nodes
  - Station Assignment
  - Updates
  - System Power
  - admin token prompt on entry

### Documentation

- Updated `SYSTEM_FEATURES.md` to describe the current role split.
- Regenerated `output/pdf/FitRaceStudio_Feature_Overview.pdf`.
- Updated `output/pdf/assets/signup.png` so the PDF no longer shows old Race Control on signup.
- Added `scripts/generate_feature_overview_pdf.py` so the PDF can be regenerated.

### Deployment

Latest deployed Hub release on `192.168.0.130`:

- `/opt/fitracestudio/releases/hub-manual-20260618144635`
- Service: `fitracestudio-hub.service`
- Status at verification time: `active`

Previous deployed releases:

- `/opt/fitracestudio/releases/hub-manual-20260618104556`
- `/opt/fitracestudio/releases/hub-manual-20260618094953`

Remote verification passed after latest deploy:

- Homepage contains Game Admin QR and read-only Edge Nodes status.
- Homepage does not contain Race Type, Save Settings, Start Race, Station Assignment DOM, or Open Local controls.
- `/gameAdmin` contains Race Control and Station Status.
- `/gameAdmin` does not contain Station Assignment, Assign Stream, Unassign Station, or System Power.
- `/systemAdmin` contains Station Assignment, Assign Stream, Unassign Station, and System Power.
- `/systemAdmin` does not contain Race Control.

## Important Files Changed

- `hub_server/infrastructure/fastapi/app.py`
  - Added routes: `/admin`, `/gameAdmin`, `/systemAdmin`.
- `hub_server/static/admin.html`
  - Role selection portal.
- `hub_server/static/gameAdmin.html`
  - Coach race operation page.
- `hub_server/static/systemAdmin.html`
  - Technical device/station/system management page.
- `hub_server/static/signup.html`
  - Management controls removed.
- `tests/integration/test_api.py`
  - Tests now enforce admin role separation.
- `SYSTEM_FEATURES.md`
  - Updated feature overview source.
- `scripts/generate_feature_overview_pdf.py`
  - Regenerates the feature overview PDF.
- `output/pdf/FitRaceStudio_Feature_Overview.pdf`
  - Updated PDF artifact.
- `output/pdf/assets/signup.png`
  - Updated signup screenshot used by the PDF.

## Verification Already Run

Local:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/integration/test_api.py
```

Result:

```text
27 passed, 1 warning
```

Frontend/static checks:

- `gameAdmin.html` and `systemAdmin.html` script syntax checked with Node `new Function(...)`.
- Local HTTP checks confirmed `/gameAdmin` and `/systemAdmin` role content.
- Playwright screenshot regenerated current signup page and confirmed no `RACE CONTROL` text.

PDF checks:

- `pdfinfo` confirmed 8 pages.
- `pdftotext` confirmed updated role-split copy.
- `pdftoppm` rendered PDF pages for visual inspection.
- Visual inspection checked cover, role model, signup, feature summary, and event flow pages.

## Known Worktree State

There were pre-existing/unrelated modified files in the worktree. Do not revert them without explicit confirmation.

Observed dirty files include:

- `ROADMAP.md`
- `hub_server/adapters/mqtt_subscriber.py`
- `hub_server/usecases/race_manager.py`
- `tests/unit/hub/test_race_manager_boundaries.py`
- `scratch/edge_nodes_status_ui.png`
- `scratch/load_demo_data.py`
- `output/`

This session also intentionally changed/added the admin UI, PDF docs, and PDF generation script listed above.

## Suggested Next Steps

1. Add proper auth/role policy if `gameAdmin` should require password on direct entry.
2. Decide whether `gameAdmin` should be token-free for coaches or use a coach-specific credential.
3. Add screenshots/assets for the new `gameAdmin`, `systemAdmin`, and `/admin` portal into the PDF when visual docs need to be more complete.
4. Continue refining Edge Node remote management APIs from Hub-side `systemAdmin`.
5. Consider committing the current admin split and documentation update once the user approves the dirty worktree scope.

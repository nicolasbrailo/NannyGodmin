# NannyGodmin Server

Remote control service for Android devices. Devices register via QR code, report activity periodically, and receive commands from a web dashboard.

## Commands

- `make run` — start the server (pipenv run python3 app.py)
- `make rebuild_deps` — nuke and recreate the virtualenv and Pipfile from scratch
- `pipenv install` — install dependencies from existing Pipfile

There are no tests.

## Architecture

Flask app with Jinja2 templates and SQLite (`nannygodmin.sqlite`). No ORM — raw `sqlite3`.

### Module layout

- `app.py` — Flask routes, request/response handling, Flask `g` pattern for DB connection lifecycle. Thin layer that delegates to `device` and `enroll` modules, mapping their exceptions to HTTP status codes. Only uses `db` directly for connection lifecycle (`db.connect`, `db.init`) and app-level config CRUD (`db.get_config`, `db.set_config`). All device operations go through `device.*`.
- `db.py` — Persistence layer. Pure sqlite3, no Flask dependency. Free functions that take a `db` connection as first argument. Covers schema init, CRUD for devices, action_log, pending_commands, and the config key-value table.
- `device.py` — Device lifecycle logic after enrollment: reports, commands (single + bulk), screenshots, device detail/debug aggregation, alias/daily-limit setters, history clearing, device removal. No Flask dependency. Owns the relock timer state. Delegates to `usage_tracking` for alert/threshold logic. Raises `device.ValidationError` (→ 400) and `device.DeviceNotFound` (→ 401 for API, 404 for HTML).
- `device_timeline.py` — Timeline computation utilities. No Flask dependency. `compute_usage_timeline` (screen on/off + lock transitions → daily hours), `compute_daily_slots` (15-min activity heatmap per day), `compute_app_timeline` (foreground app usage with short-transition collapsing).
- `usage_tracking.py` — Incremental usage tracking and alert system. No Flask dependency. Keeps per-device running state (`accumulated_secs`, `active_since`) seeded once from the timeline on first access, then updated in O(1) per report. Checks usage against a per-device threshold (`devices.daily_limit_mins`) falling back to the global config (`daily_limit_mins`); prints an alert and optionally auto-locks the device (`auto_lock`). Resets accumulated usage and auto-lock state at midnight (lazily on next report). Manual unlock clears `auto_locked` so midnight won't generate a spurious unlock. Only imported by `device.py`; configured via `device.configure_alerts()` from `app.py`.
- `enroll.py` — Device enrollment logic (QR code generation, provisioning). No Flask dependency. Raises `enroll.ValidationError` for invalid input; `app.py` maps these to HTTP 400.

### API Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Web dashboard (list devices, usage, controls) |
| `/new_device` | GET | QR code page for device provisioning |
| `/provision` | POST | Device registration — JSON `{deviceName, androidId}` → `{clientId, locked, poll_interval_secs}` |
| `/device_report` | POST | Device check-in — JSON `{clientId, action?, ...args}` → returns pending commands and clears them |
| `/device_report/screenshot` | POST | Upload screenshot — raw PNG body, `X-Client-Id` header |
| `/device/<id>` | GET | Device detail page (screenshot, usage report, app timeline) |
| `/device/<id>/debug` | GET | Debug page (state transitions, full activity history) |
| `/device/<id>/command` | POST | Queue a command from the dashboard (form: action + optional value) |
| `/device/<id>/alias` | POST | Set device alias |
| `/device/<id>/daily_limit` | POST | Set per-device daily usage limit (overrides global) |
| `/device/<id>/clear_history` | POST | Clear a device's action log |
| `/device/<id>/remove` | POST | Remove a device and its history |
| `/bulk_command` | POST | Lock/unlock all devices (form: action + optional duration) |
| `/config` | GET | Config page |
| `/config` | POST | Save config (poll_interval_secs, daily_limit_mins, auto_lock) |

### Database Tables

- `devices` — registered devices (UUID primary key, name, alias, daily_limit_mins, locked, timestamp)
- `action_log` — event log for both device reports (source=device) and controller commands (source=controller)
- `pending_commands` — fire-and-forget command queue; commands are returned to the device on next `/device_report` call and then deleted
- `config` — key-value store for app-level settings (poll_interval_secs, daily_limit_mins, auto_lock)

### Key Behaviors

- **Command deduplication**: when queueing a command, previous pending commands in the same override group are replaced. Groups: `{set_volume}`, `{lock, unlock}`.
- **Device report**: `action` is optional — a device can call `/device_report` with just `{clientId}` to poll for commands without reporting anything.
- **app_change events**: the Android accessibility service reports `app_change` with `new_activity` set to a package/activity string. `"Unknown"` does NOT mean the screen is off — it's noise from the accessibility service losing track of the foreground app during transitions. The real app keeps running. Use `screen_off`/`screen_on` events for actual screen state. See `device_timeline.compute_app_timeline` for the full heuristic (5 phases: raw spans, contiguous merge, drop short, re-merge, gap merge for same-app spans <5 min apart).
- **Usage alerts**: global threshold (`daily_limit_mins`) and `auto_lock` are stored in the `config` table, with hardcoded defaults in `app.py` (`CONFIG_DEFAULTS`). Per-device overrides via `devices.daily_limit_mins` take precedence. When a device's accumulated screen-on time crosses the effective threshold, an alert is printed to stdout. If `auto_lock` is enabled, the device is locked and marked `auto_locked`; at midnight the lock and threshold are reset lazily on the next device report. Manual unlock (single or bulk) clears `auto_locked` and respects timed unlock durations. Changing a device's per-device limit resets the `triggered` flag so the alert can re-fire at the new threshold.
- **Device alias**: optional per-device alias displayed instead of the device-reported name on the dashboard and detail page title. Set from the detail page.
- **QR code**: encodes `nannygodmin://config?url=http://<lan-ip>:<port>/`; port is read from the incoming request, not hardcoded.

## Dependencies

- `flask` — web framework
- `qrcode[pil]` — QR code generation (pulls in pillow)

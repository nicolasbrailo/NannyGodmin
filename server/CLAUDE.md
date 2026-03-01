# NannyGodmin Server

Remote control service for Android devices. Devices register via QR code, report activity periodically, and receive commands from a web dashboard.

## Commands

- `make run` — start the server (pipenv run python3 app.py)
- `make rebuild_deps` — nuke and recreate the virtualenv and Pipfile from scratch
- `pipenv install` — install dependencies from existing Pipfile

There are no tests.

## Architecture

Single-file Flask app (`app.py`) with Jinja2 templates and SQLite (`nannygodmin.db`). No ORM — raw `sqlite3` with the Flask `g` pattern for connection lifecycle.

### API Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Web dashboard (list devices, action history, controls) |
| `/new_device` | GET | QR code page for device provisioning |
| `/provision` | POST | Device registration — JSON `{deviceName}` → `{clientId}` |
| `/device_report` | POST | Device check-in — JSON `{clientId, action?, ...args}` → returns pending commands and clears them |
| `/device/<id>/remove` | POST | Remove a device and its history |
| `/device/<id>/command` | POST | Queue a command from the dashboard (form: action + optional value) |

### Database Tables

- `devices` — registered devices (UUID primary key, name, timestamp)
- `action_log` — event log for both device reports (source=device) and controller commands (source=controller)
- `pending_commands` — fire-and-forget command queue; commands are returned to the device on next `/device_report` call and then deleted

### Key Behaviors

- **Command deduplication**: when queueing a command, previous pending commands in the same override group are replaced. Groups: `{set_volume}`, `{lock, unlock}`.
- **Device report**: `action` is optional — a device can call `/device_report` with just `{clientId}` to poll for commands without reporting anything.
- **QR code**: encodes `nannygodmin://config?url=http://<lan-ip>:<port>/`; port is read from the incoming request, not hardcoded.

## Dependencies

- `flask` — web framework
- `qrcode[pil]` — QR code generation (pulls in pillow)

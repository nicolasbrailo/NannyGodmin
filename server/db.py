import json
import sqlite3

DATABASE = "nannygodmin.sqlite"


def connect(database=DATABASE):
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init(database=DATABASE):
    db = sqlite3.connect(database)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            alias TEXT,
            android_id TEXT UNIQUE,
            locked INTEGER NOT NULL DEFAULT 0,
            daily_limit_mins INTEGER,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL REFERENCES devices(id),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            action TEXT NOT NULL,
            args TEXT,
            source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL REFERENCES devices(id),
            command TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    db.close()


# --- Devices ---

def get_all_devices(db):
    return db.execute("SELECT * FROM devices ORDER BY registered_at DESC").fetchall()


def get_device(db, device_id):
    return db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()


def find_device_by_android_id(db, android_id):
    return db.execute(
        "SELECT id, locked FROM devices WHERE android_id = ?", (android_id,)
    ).fetchone()


def insert_device(db, device_id, name, android_id):
    db.execute(
        "INSERT INTO devices (id, name, android_id) VALUES (?, ?, ?)",
        (device_id, name, android_id),
    )
    db.commit()


def set_device_alias(db, device_id, alias):
    db.execute(
        "UPDATE devices SET alias = ? WHERE id = ?",
        (alias or None, device_id),
    )
    db.commit()


def set_device_daily_limit(db, device_id, daily_limit_mins):
    db.execute(
        "UPDATE devices SET daily_limit_mins = ? WHERE id = ?",
        (daily_limit_mins, device_id),
    )
    db.commit()


def set_device_locked(db, device_id, locked):
    db.execute(
        "UPDATE devices SET locked = ? WHERE id = ?",
        (1 if locked else 0, device_id),
    )


def remove_device(db, device_id):
    db.execute("DELETE FROM pending_commands WHERE device_id = ?", (device_id,))
    db.execute("DELETE FROM action_log WHERE device_id = ?", (device_id,))
    db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    db.commit()


# --- Action log ---

def insert_action_log(db, device_id, action, args, source):
    db.execute(
        "INSERT INTO action_log (device_id, action, args, source) VALUES (?, ?, ?, ?)",
        (device_id, action, json.dumps(args) if args else None, source),
    )


def get_device_logs(db, device_id, limit=100):
    return db.execute(
        "SELECT * FROM action_log WHERE device_id = ? ORDER BY timestamp DESC LIMIT ?",
        (device_id, limit),
    ).fetchall()


def get_usage_events(db, device_id):
    return db.execute(
        "SELECT timestamp, action, source FROM action_log "
        "WHERE device_id = ? AND action IN ('screen_on', 'screen_off', 'lock', 'unlock') "
        "ORDER BY timestamp ASC",
        (device_id,),
    ).fetchall()


def get_app_and_screen_events(db, device_id):
    return db.execute(
        "SELECT timestamp, action, args FROM action_log "
        "WHERE device_id = ? AND action IN ('app_change', 'screen_off', 'screen_on') "
        "ORDER BY timestamp ASC",
        (device_id,),
    ).fetchall()


def get_current_screen_state(db, device_id):
    row = db.execute(
        "SELECT action FROM action_log "
        "WHERE device_id = ? AND action IN ('screen_on', 'screen_off') "
        "ORDER BY timestamp DESC LIMIT 1",
        (device_id,),
    ).fetchone()
    return row["action"] == "screen_on" if row else False


def clear_action_log(db, device_id):
    db.execute("DELETE FROM action_log WHERE device_id = ?", (device_id,))
    db.commit()


# --- Pending commands ---

def get_and_clear_pending_commands(db, device_id):
    rows = db.execute(
        "SELECT id, command FROM pending_commands WHERE device_id = ?", (device_id,)
    ).fetchall()
    commands = [json.loads(row["command"]) for row in rows]
    if rows:
        db.execute("DELETE FROM pending_commands WHERE device_id = ?", (device_id,))
    return commands


# --- Config ---

def get_config(db):
    rows = db.execute("SELECT key, value FROM config").fetchall()
    return {row["key"]: json.loads(row["value"]) for row in rows}


def set_config(db, key, value):
    db.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    db.commit()


def replace_pending_command(db, device_id, action_name, command):
    db.execute(
        "DELETE FROM pending_commands WHERE device_id = ? AND "
        "json_extract(command, '$.name') = ?",
        (device_id, action_name),
    )
    db.execute(
        "INSERT INTO pending_commands (device_id, command) VALUES (?, ?)",
        (device_id, json.dumps(command)),
    )

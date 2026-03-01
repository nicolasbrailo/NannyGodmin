import io
import json
import socket
import sqlite3
import uuid
from base64 import b64encode

import qrcode
from flask import Flask, g, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)
DATABASE = "nannygodmin.sqlite"


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
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
    """)
    db.close()


@app.route("/")
def dashboard():
    db = get_db()
    devices = db.execute(
        "SELECT * FROM devices ORDER BY registered_at DESC"
    ).fetchall()
    logs = db.execute(
        "SELECT * FROM action_log ORDER BY timestamp DESC LIMIT 100"
    ).fetchall()
    return render_template("dashboard.html", devices=devices, logs=logs)


@app.route("/new_device")
def new_device():
    ip = get_local_ip()
    port = request.host.split(":")[-1] if ":" in request.host else "80"
    base_url = f"http://{ip}:{port}/"
    qr_data = f"nannygodmin://config?url={base_url}"
    img = qrcode.make(qr_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = b64encode(buf.getvalue()).decode("utf-8")
    return render_template("new_device.html", qr_b64=qr_b64, qr_data=qr_data)


@app.route("/provision", methods=["POST"])
def provision():
    data = request.get_json()
    device_name = data.get("deviceName", "Unnamed Device")
    client_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        "INSERT INTO devices (id, name) VALUES (?, ?)", (client_id, device_name)
    )
    db.commit()
    return jsonify({"clientId": client_id})


@app.route("/device_report", methods=["POST"])
def device_report():
    data = request.get_json()
    client_id = data.get("clientId")
    action = data.get("action")

    if not client_id:
        return jsonify({"error": "clientId is required"}), 400

    db = get_db()

    # Verify device exists
    device = db.execute("SELECT id FROM devices WHERE id = ?", (client_id,)).fetchone()
    if not device:
        return jsonify({"error": "unknown device"}), 404

    # Log the reported action if one was provided
    if action and action != "poll":
        args = {k: v for k, v in data.items() if k not in ("clientId", "action")}
        db.execute(
            "INSERT INTO action_log (device_id, action, args, source) VALUES (?, ?, ?, ?)",
            (client_id, action, json.dumps(args) if args else None, "device"),
        )

    # Fetch and clear pending commands
    rows = db.execute(
        "SELECT id, command FROM pending_commands WHERE device_id = ?", (client_id,)
    ).fetchall()
    commands = [json.loads(row["command"]) for row in rows]
    if rows:
        db.execute("DELETE FROM pending_commands WHERE device_id = ?", (client_id,))

    db.commit()
    return jsonify(commands)


@app.route("/device/<device_id>/clear_history", methods=["POST"])
def clear_history(device_id):
    db = get_db()
    db.execute("DELETE FROM action_log WHERE device_id = ?", (device_id,))
    db.commit()
    return redirect(url_for("dashboard"))


@app.route("/device/<device_id>/remove", methods=["POST"])
def remove_device(device_id):
    db = get_db()
    db.execute("DELETE FROM pending_commands WHERE device_id = ?", (device_id,))
    db.execute("DELETE FROM action_log WHERE device_id = ?", (device_id,))
    db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    db.commit()
    return redirect(url_for("dashboard"))


@app.route("/device/<device_id>/command", methods=["POST"])
def send_command(device_id):
    action = request.form.get("action")
    value = request.form.get("value")

    if action == "set_volume":
        cmd = {"name": "set_volume", "arg": int(value)}
    else:
        cmd = {"name": action}

    # Commands that supersede each other
    OVERRIDE_GROUPS = [
        {"set_volume"},
        {"lock", "unlock"},
    ]
    override_names = {action}
    for group in OVERRIDE_GROUPS:
        if action in group:
            override_names = group
            break

    db = get_db()
    # Remove any pending commands that the new one supersedes
    placeholders = ",".join("?" * len(override_names))
    db.execute(
        f"DELETE FROM pending_commands WHERE device_id = ? AND "
        f"json_extract(command, '$.name') IN ({placeholders})",
        (device_id, *override_names),
    )
    db.execute(
        "INSERT INTO pending_commands (device_id, command) VALUES (?, ?)",
        (device_id, json.dumps(cmd)),
    )
    # Also log the command
    db.execute(
        "INSERT INTO action_log (device_id, action, args, source) VALUES (?, ?, ?, ?)",
        (device_id, action, json.dumps(cmd) if value else None, "controller"),
    )
    db.commit()
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", debug=True)

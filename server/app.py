import os

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, url_for

import db
import device
import enroll

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = db.connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

SCREENSHOTS_DIR = "screenshots"
CONFIG_DEFAULTS = {
    "poll_interval_secs": 5,
    "daily_limit_mins": 120,
    "auto_lock": False,
    "warning_enabled": False,
    "warning_mins": 5,
}


def _load_config():
    """Load config from DB, falling back to defaults for missing keys."""
    conn = db.connect()
    saved = db.get_config(conn)
    conn.close()
    config = dict(CONFIG_DEFAULTS)
    config.update(saved)
    return config


def _provision_config(config):
    return {"poll_interval_secs": config["poll_interval_secs"]}


def _alert_config(config):
    return {
        "daily_limit_mins": config["daily_limit_mins"],
        "auto_lock": config["auto_lock"],
        "warning_enabled": config["warning_enabled"],
        "warning_mins": config["warning_mins"],
    }


@app.route("/")
def dashboard():
    devices, usage_today = device.get_all_devices_with_usage(get_db())
    relock_at = device.get_relock_at()
    relock_at = relock_at.isoformat() if relock_at else None
    return render_template("dashboard.html", devices=devices, usage_today=usage_today, relock_at=relock_at)


@app.route("/new_device")
def new_device():
    qr_b64, qr_data = enroll.make_qr_code(request.host)
    return render_template("new_device.html", qr_b64=qr_b64, qr_data=qr_data)


@app.route("/provision", methods=["POST"])
def provision():
    data = request.get_json()
    try:
        result = enroll.provision(get_db(), data.get("deviceName", "Unnamed Device"), data.get("androidId"), _provision_config(_load_config()))
    except enroll.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@app.route("/device_report", methods=["POST"])
def device_report():
    data = request.get_json()
    client_id = data.get("clientId")
    action = data.get("action")
    extra_args = {k: v for k, v in data.items() if k not in ("clientId", "action")}

    try:
        result = device.process_report(get_db(), client_id, action, extra_args)
    except device.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except device.DeviceNotFound as e:
        return jsonify({"error": str(e)}), 401

    return jsonify(result)


@app.route("/device_report/screenshot", methods=["POST"])
def device_report_screenshot():
    client_id = request.headers.get("X-Client-Id")
    data = request.get_data()

    try:
        device.save_screenshot(get_db(), SCREENSHOTS_DIR, client_id, data)
    except device.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except device.DeviceNotFound as e:
        return jsonify({"error": str(e)}), 401

    return jsonify({"ok": True})


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.root_path, "favicon.ico", mimetype="image/x-icon")
@app.route("/apk")
def apk():
    return send_from_directory(app.root_path, "godmin.apk")


@app.route("/screenshots/<filename>")
def serve_screenshot(filename):
    return send_from_directory(SCREENSHOTS_DIR, filename)


@app.route("/device/<device_id>")
def device_detail(device_id):
    try:
        detail = device.get_device_detail(get_db(), device_id, SCREENSHOTS_DIR)
    except device.DeviceNotFound:
        return "Device not found", 404

    screenshot_url = None
    if detail["screenshot_filename"]:
        screenshot_url = url_for("serve_screenshot", filename=detail["screenshot_filename"])

    config = _load_config()
    return render_template(
        "device_detail.html",
        device=detail["device"],
        screenshot_url=screenshot_url,
        screenshot_time=detail["screenshot_time"],
        daily_hours=detail["daily_hours"],
        daily_slots=detail["daily_slots"],
        slot_hours=detail["slot_hours"],
        app_timeline=detail["app_timeline"],
        global_daily_limit_mins=config["daily_limit_mins"],
    )


@app.route("/device/<device_id>/debug")
def device_debug(device_id):
    try:
        debug = device.get_device_debug(get_db(), device_id)
    except device.DeviceNotFound:
        return "Device not found", 404

    return render_template(
        "device_debug.html",
        device=debug["device"],
        logs=debug["logs"],
        transitions=debug["transitions"],
    )


@app.route("/device/<device_id>/daily_limit", methods=["POST"])
def set_daily_limit(device_id):
    limit = request.form.get("daily_limit_mins", "").strip()
    device.set_device_daily_limit(get_db(), device_id, int(limit) if limit else None)
    return redirect(url_for("device_detail", device_id=device_id))


@app.route("/device/<device_id>/alias", methods=["POST"])
def set_alias(device_id):
    alias = request.form.get("alias", "").strip()
    device.set_device_alias(get_db(), device_id, alias)
    return redirect(url_for("device_detail", device_id=device_id))


@app.route("/device/<device_id>/clear_history", methods=["POST"])
def clear_history(device_id):
    device.clear_history(get_db(), device_id)
    return redirect(url_for("device_detail", device_id=device_id))


@app.route("/device/<device_id>/remove", methods=["POST"])
def remove_device(device_id):
    device.remove_device(get_db(), device_id)
    return redirect(url_for("dashboard"))


@app.route("/device/<device_id>/command", methods=["POST"])
def send_command(device_id):
    action = request.form.get("action")
    args = {k: v for k, v in request.form.items() if k != "action" and v}
    device.send_command(get_db(), device_id, action, args)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/bulk_command", methods=["POST"])
def bulk_command():
    action = request.form.get("action")
    duration_mins = request.form.get("duration")
    duration_mins = int(duration_mins) if duration_mins else None
    device.bulk_command(get_db(), action, duration_mins)
    return redirect(url_for("dashboard"))


@app.route("/config")
def config_page():
    config = _load_config()
    return render_template("config.html", config=config)


@app.route("/config", methods=["POST"])
def config_save():
    conn = get_db()
    poll = request.form.get("poll_interval_secs")
    if poll is not None:
        db.set_config(conn, "poll_interval_secs", int(poll))

    limit = request.form.get("daily_limit_mins")
    if limit is not None:
        db.set_config(conn, "daily_limit_mins", int(limit) if limit != "" else None)

    auto_lock = request.form.get("auto_lock")
    db.set_config(conn, "auto_lock", auto_lock == "on")

    warning_enabled = request.form.get("warning_enabled")
    db.set_config(conn, "warning_enabled", warning_enabled == "on")

    warning_mins = request.form.get("warning_mins")
    if warning_mins is not None:
        db.set_config(conn, "warning_mins", int(warning_mins))

    device.configure_alerts(_alert_config(_load_config()))
    return redirect(url_for("config_page"))


if __name__ == "__main__":
    db.init()
    config = _load_config()
    device.configure_alerts(_alert_config(config))
    app.run(host="0.0.0.0", port=4400, debug=True)

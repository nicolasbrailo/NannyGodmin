import os

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, url_for

import db
import device
import device_timeline
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
PROVISION_CONFIG = {"poll_interval_secs": 5}
ALERT_CONFIG = {
    "daily_limit_mins": 120,  # None to disable
    "auto_lock": False,
}


@app.route("/")
def dashboard():
    conn = get_db()
    devices = db.get_all_devices(conn)
    usage_today = {d["id"]: device_timeline.get_today_usage(conn, d["id"]) for d in devices}
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
        result = enroll.provision(get_db(), data.get("deviceName", "Unnamed Device"), data.get("androidId"), PROVISION_CONFIG)
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

    conn = get_db()
    dev = db.get_device(conn, client_id) if client_id else None

    try:
        if not dev:
            if not client_id:
                raise device.ValidationError("X-Client-Id header is required")
            raise device.DeviceNotFound("unknown device")
        device.save_screenshot(SCREENSHOTS_DIR, client_id, data)
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

    return render_template(
        "device_detail.html",
        device=detail["device"],
        screenshot_url=screenshot_url,
        screenshot_time=detail["screenshot_time"],
        daily_hours=detail["daily_hours"],
        daily_slots=detail["daily_slots"],
        slot_hours=detail["slot_hours"],
        app_timeline=detail["app_timeline"],
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


@app.route("/device/<device_id>/clear_history", methods=["POST"])
def clear_history(device_id):
    db.clear_action_log(get_db(), device_id)
    return redirect(url_for("device_detail", device_id=device_id))


@app.route("/device/<device_id>/remove", methods=["POST"])
def remove_device(device_id):
    db.remove_device(get_db(), device_id)
    return redirect(url_for("dashboard"))


@app.route("/device/<device_id>/command", methods=["POST"])
def send_command(device_id):
    action = request.form.get("action")
    value = request.form.get("value")
    device.send_command(get_db(), device_id, action, value)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/bulk_command", methods=["POST"])
def bulk_command():
    action = request.form.get("action")
    duration_mins = request.form.get("duration")
    duration_mins = int(duration_mins) if duration_mins else None
    device.bulk_command(get_db(), action, duration_mins)
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    db.init()
    device.configure_alerts(ALERT_CONFIG)
    app.run(host="0.0.0.0", port=4400, debug=True)

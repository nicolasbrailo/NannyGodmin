import os
import threading
from datetime import datetime, timedelta, timezone

import db
import device_timeline
import usage_tracking


class ValidationError(Exception):
    pass


class DeviceNotFound(Exception):
    pass


_relock_timer = None
_relock_at = None


def configure_alerts(config):
    usage_tracking.configure(config)


def get_all_devices_with_usage(conn):
    devices = db.get_all_devices(conn)
    usage_today = {d["id"]: device_timeline.get_today_usage(conn, d["id"]) for d in devices}
    return devices, usage_today


def set_device_alias(conn, device_id, alias):
    db.set_device_alias(conn, device_id, alias)


def set_device_daily_limit(conn, device_id, daily_limit_mins):
    db.set_device_daily_limit(conn, device_id, daily_limit_mins)
    usage_tracking.reset_triggered(device_id)


def clear_history(conn, device_id):
    db.clear_action_log(conn, device_id)


def remove_device(conn, device_id):
    db.remove_device(conn, device_id)


def get_relock_at():
    return _relock_at


def process_report(conn, client_id, action, extra_args):
    if not client_id:
        raise ValidationError("clientId is required")

    device = db.get_device(conn, client_id)
    if not device:
        raise DeviceNotFound("unknown device")

    if action and action != "poll":
        db.insert_action_log(conn, client_id, action, extra_args if extra_args else None, "device")

    lock_override = usage_tracking.check_usage(conn, client_id, device["name"], action)

    commands = db.get_and_clear_pending_commands(conn, client_id)
    conn.commit()

    locked = lock_override if lock_override is not None else bool(device["locked"])
    return {"commands": commands, "locked": locked}


def save_screenshot(conn, screenshots_dir, client_id, data):
    if not client_id:
        raise ValidationError("X-Client-Id header is required")
    if not data:
        raise ValidationError("empty body")
    if not db.get_device(conn, client_id):
        raise DeviceNotFound("unknown device")

    os.makedirs(screenshots_dir, exist_ok=True)
    path = os.path.join(screenshots_dir, f"{client_id}_screenshot.png")
    with open(path, "wb") as f:
        f.write(data)


def send_command(conn, device_id, action, value):
    if action in ("lock", "unlock"):
        db.set_device_locked(conn, device_id, action == "lock")
        usage_tracking.update_lock(device_id, action == "lock")
    else:
        cmd = {"name": action}
        if action == "set_volume":
            cmd["arg"] = int(value)
        db.replace_pending_command(conn, device_id, action, cmd)

    db.insert_action_log(conn, device_id, action, {"value": value} if value else None, "controller")
    conn.commit()


def _relock_devices(device_ids):
    global _relock_timer, _relock_at
    _relock_timer = None
    _relock_at = None
    conn = db.connect()
    for did in device_ids:
        db.set_device_locked(conn, did, True)
        db.insert_action_log(conn, did, "lock", None, "controller")
    conn.commit()
    conn.close()


def bulk_command(conn, action, duration_mins=None):
    global _relock_timer, _relock_at
    devices = db.get_all_devices(conn)

    if _relock_timer is not None:
        _relock_timer.cancel()
        _relock_timer = None
        _relock_at = None

    if action == "lock_all":
        for d in devices:
            db.set_device_locked(conn, d["id"], True)
            db.insert_action_log(conn, d["id"], "lock", None, "controller")
            usage_tracking.update_lock(d["id"], True)
    elif action == "unlock_all":
        for d in devices:
            db.set_device_locked(conn, d["id"], False)
            db.insert_action_log(conn, d["id"], "unlock", None, "controller")
            usage_tracking.update_lock(d["id"], False)
    elif action == "unlock_all_timed":
        if duration_mins is None:
            duration_mins = 30
        snapshot = [d["id"] for d in devices if d["locked"]]
        for d in devices:
            db.set_device_locked(conn, d["id"], False)
            db.insert_action_log(conn, d["id"], "unlock", None, "controller")
            usage_tracking.update_lock(d["id"], False)
        if snapshot:
            _relock_at = datetime.now(timezone.utc) + timedelta(minutes=duration_mins)
            _relock_timer = threading.Timer(duration_mins * 60, _relock_devices, args=[snapshot])
            _relock_timer.daemon = True
            _relock_timer.start()

    conn.commit()
    return _relock_at


def get_device_detail(conn, device_id, screenshots_dir):
    device_row = db.get_device(conn, device_id)
    if not device_row:
        raise DeviceNotFound("unknown device")

    screenshot_path = os.path.join(screenshots_dir, f"{device_id}_screenshot.png")
    screenshot_filename = None
    screenshot_time = None
    if os.path.exists(screenshot_path):
        screenshot_filename = f"{device_id}_screenshot.png"
        mtime = os.path.getmtime(screenshot_path)
        screenshot_time = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    transitions, daily_hours = device_timeline.compute_usage_timeline(conn, device_id)
    daily_slots, slot_hours = device_timeline.compute_daily_slots(transitions)
    app_timeline = device_timeline.compute_app_timeline(conn, device_id)

    return {
        "device": device_row,
        "screenshot_filename": screenshot_filename,
        "screenshot_time": screenshot_time,
        "daily_slots": daily_slots,
        "daily_hours": daily_hours,
        "slot_hours": slot_hours,
        "app_timeline": app_timeline,
    }


def get_device_debug(conn, device_id):
    device_row = db.get_device(conn, device_id)
    if not device_row:
        raise DeviceNotFound("unknown device")

    logs = db.get_device_logs(conn, device_id)
    transitions, _ = device_timeline.compute_usage_timeline(conn, device_id)

    return {
        "device": device_row,
        "logs": logs,
        "transitions": transitions,
    }

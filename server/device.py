import os
import threading
import uuid
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


def _staleness_info(conn, device_id):
    """Return (is_stale, is_unknown, last_report_time, now_cap) for a device.

    is_stale: device hasn't reported within the staleness window.
    is_unknown: stale AND the last screen event was not screen_off — we don't
                know the actual device state (e.g. battery died, service killed).
                When stale but last event was screen_off, the state is known (inactive).
    """
    staleness = usage_tracking.get_staleness_secs()
    last_ts = db.get_last_report_time(conn, device_id)
    last_report = datetime.fromisoformat(last_ts) if last_ts else None
    if not staleness or not last_report:
        return False, False, last_ts, None
    elapsed = (datetime.now() - last_report).total_seconds()
    is_stale = elapsed > staleness
    cap = (last_report + timedelta(seconds=staleness)) if is_stale else None
    is_unknown = is_stale and db.get_current_screen_state(conn, device_id)
    return is_stale, is_unknown, last_ts, cap


def _staleness_cap(conn, device_id):
    """Compute a now-cap for timeline functions if the device is stale."""
    _, _, _, cap = _staleness_info(conn, device_id)
    return cap


def configure_alerts(config):
    usage_tracking.configure(config)


def get_all_devices_with_usage(conn):
    devices = db.get_all_devices(conn)
    ignore_re = usage_tracking._usage_ignore_re
    usage_today = {}
    device_staleness = {}
    for d in devices:
        is_stale, is_unknown, last_report_ts, cap = _staleness_info(conn, d["id"])
        active, ignored = device_timeline.get_today_usage_split(conn, d["id"], ignore_re, now_cap=cap)
        usage_today[d["id"]] = {"active_mins": active, "ignored_mins": ignored}
        device_staleness[d["id"]] = {"is_stale": is_stale, "is_unknown": is_unknown, "last_report": last_report_ts}
    groups = db.get_all_device_groups(conn)
    groups_by_id = {g["id"]: g for g in groups}
    return devices, usage_today, groups_by_id, device_staleness


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

    lock_override = usage_tracking.check_usage(conn, client_id, device["alias"] or device["name"], action, extra_args)

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


def send_command(conn, device_id, action, args=None):
    if action in ("lock", "unlock"):
        db.set_device_locked(conn, device_id, action == "lock")
        usage_tracking.update_lock(device_id, action == "lock")
    else:
        cmd = {"name": action}
        if action == "set_volume":
            cmd["arg"] = int(args.get("value", 50))
        elif action == "show_notification":
            cmd["msg"] = args.get("msg", "")
            timeout = args.get("timeout")
            cmd["timeout"] = int(timeout) if timeout else 10
        db.replace_pending_command(conn, device_id, action, cmd)

    db.insert_action_log(conn, device_id, action, args or None, "controller")
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


def push_app_update(conn, url):
    devices = db.get_all_devices(conn)
    cmd = {"name": "app_update_notify", "url": url}
    for d in devices:
        db.replace_pending_command(conn, d["id"], "app_update_notify", cmd)
    conn.commit()


def push_provisioning_config(conn, provision_config):
    devices = db.get_all_devices(conn)
    cmd = {"name": "provisioning_config", **provision_config}
    for d in devices:
        db.replace_pending_command(conn, d["id"], "provisioning_config", cmd)
    conn.commit()


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

    ignore_re = usage_tracking._usage_ignore_re
    cap = _staleness_cap(conn, device_id)
    transitions, daily_hours, ignored_daily_hours = device_timeline.compute_usage_timeline_filtered(
        conn, device_id, ignore_re, now_cap=cap
    )
    daily_slots, slot_hours = device_timeline.compute_daily_slots(transitions, now_cap=cap)
    app_timeline = device_timeline.compute_app_timeline(conn, device_id, now_cap=cap)

    is_stale, is_unknown, last_report_ts, _ = _staleness_info(conn, device_id)

    return {
        "device": device_row,
        "screenshot_filename": screenshot_filename,
        "screenshot_time": screenshot_time,
        "daily_slots": daily_slots,
        "daily_hours": daily_hours,
        "ignored_daily_hours": ignored_daily_hours,
        "slot_hours": slot_hours,
        "app_timeline": app_timeline,
        "is_stale": is_stale,
        "is_unknown": is_unknown,
        "last_report": last_report_ts,
    }


def get_device_debug(conn, device_id):
    device_row = db.get_device(conn, device_id)
    if not device_row:
        raise DeviceNotFound("unknown device")

    logs = db.get_device_logs(conn, device_id)
    cap = _staleness_cap(conn, device_id)
    transitions, _ = device_timeline.compute_usage_timeline(conn, device_id, now_cap=cap)

    return {
        "device": device_row,
        "logs": logs,
        "transitions": transitions,
    }


# --- Device groups ---

def get_all_groups(conn):
    return db.get_all_device_groups(conn)


def get_all_groups_with_usage(conn):
    groups = db.get_all_device_groups(conn)
    ignore_re = usage_tracking._usage_ignore_re
    result = []
    for g in groups:
        devices = db.get_devices_in_group(conn, g["id"])
        total_mins = 0
        for d in devices:
            cap = _staleness_cap(conn, d["id"])
            active, _ = device_timeline.get_today_usage_split(conn, d["id"], ignore_re, now_cap=cap)
            total_mins += active
        result.append({
            "group": g,
            "devices": devices,
            "usage_mins": total_mins,
        })
    return result


def create_group(conn, name, daily_limit_mins=None):
    if not name or not name.strip():
        raise ValidationError("Group name is required")
    group_id = str(uuid.uuid4())
    db.create_device_group(conn, group_id, name.strip(), daily_limit_mins)
    return group_id


def update_group(conn, group_id, name, daily_limit_mins):
    group = db.get_device_group(conn, group_id)
    if not group:
        raise DeviceNotFound("unknown group")
    if not name or not name.strip():
        raise ValidationError("Group name is required")
    db.update_device_group(conn, group_id, name.strip(), daily_limit_mins)
    # Reset triggered state so threshold can re-fire at new limit
    if daily_limit_mins != group["daily_limit_mins"]:
        for d in db.get_devices_in_group(conn, group_id):
            usage_tracking.reset_triggered(d["id"])


def remove_group(conn, group_id):
    devices = db.get_devices_in_group(conn, group_id)
    db.remove_device_group(conn, group_id)
    for d in devices:
        usage_tracking.update_device_group_assignment(d["id"], None)


def assign_device_to_group(conn, device_id, group_id):
    if group_id and not db.get_device_group(conn, group_id):
        raise DeviceNotFound("unknown group")
    db.set_device_group(conn, device_id, group_id)
    usage_tracking.update_device_group_assignment(device_id, group_id)


def get_group_detail(conn, group_id):
    group = db.get_device_group(conn, group_id)
    if not group:
        raise DeviceNotFound("unknown group")
    devices = db.get_devices_in_group(conn, group_id)
    ignore_re = usage_tracking._usage_ignore_re
    total_mins = 0
    device_usage = {}
    for d in devices:
        cap = _staleness_cap(conn, d["id"])
        active, ignored = device_timeline.get_today_usage_split(conn, d["id"], ignore_re, now_cap=cap)
        device_usage[d["id"]] = {"active_mins": active, "ignored_mins": ignored}
        total_mins += active
    return {
        "group": group,
        "devices": devices,
        "device_usage": device_usage,
        "total_usage_mins": total_mins,
    }

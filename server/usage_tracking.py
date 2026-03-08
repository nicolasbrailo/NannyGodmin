from datetime import datetime

import db
import device_timeline

_alert_config = {
    "daily_limit_mins": None,  # None = disabled
    "auto_lock": False,
}

_usage_trackers = {}  # device_id -> tracker dict


def configure(config):
    global _alert_config
    _alert_config = config


def reset_triggered(device_id):
    tracker = _usage_trackers.get(device_id)
    if tracker:
        tracker["triggered"] = False


def _seed_tracker(conn, device_id):
    """Build tracker state from existing data (one-time cost per device after restart)."""
    today = datetime.now().strftime("%Y-%m-%d")
    usage_mins = device_timeline.get_today_usage(conn, device_id)

    dev = db.get_device(conn, device_id)
    server_locked = bool(dev["locked"])
    screen_on = db.get_current_screen_state(conn, device_id)
    active = screen_on and not server_locked
    now = datetime.now()

    tracker = {
        "date": today,
        "accumulated_secs": usage_mins * 60,
        "active_since": now if active else None,
        "screen_on": screen_on,
        "server_locked": server_locked,
        "triggered": False,
        "auto_locked": False,
    }
    _usage_trackers[device_id] = tracker
    return tracker


def _get_tracker(conn, device_id):
    if device_id not in _usage_trackers:
        return _seed_tracker(conn, device_id)
    return _usage_trackers[device_id]


def _notify_threshold(device_id, device_name, usage_mins, threshold_mins):
    """Notify that a device has exceeded its daily usage threshold."""
    print(f"[ALERT] {datetime.now().strftime('%H:%M:%S')} — "
          f"Device '{device_name}' ({device_id[:8]}...) reached "
          f"{usage_mins:.0f}min usage (threshold: {threshold_mins}min)")


def _get_threshold(conn, device_id):
    """Return the effective threshold for a device (per-device override or global)."""
    dev = db.get_device(conn, device_id)
    if dev["daily_limit_mins"] is not None:
        return dev["daily_limit_mins"]
    return _alert_config.get("daily_limit_mins")


def check_usage(conn, device_id, device_name, action):
    """Update usage tracker and check threshold. Called on every device report."""
    threshold = _get_threshold(conn, device_id)
    if threshold is None:
        return None

    tracker = _get_tracker(conn, device_id)
    was_locked_before = tracker["server_locked"]
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Day rollover — reset tracker, unlock if auto-locked
    if tracker["date"] != today:
        if tracker["auto_locked"]:
            db.set_device_locked(conn, device_id, False)
            db.insert_action_log(conn, device_id, "unlock", {"reason": "daily_reset"}, "controller")
            tracker["server_locked"] = False
        tracker["date"] = today
        tracker["accumulated_secs"] = 0
        tracker["triggered"] = False
        tracker["auto_locked"] = False
        if tracker["active_since"]:
            tracker["active_since"] = now

    # Update screen state from device-reported events
    if action == "screen_on":
        tracker["screen_on"] = True
    elif action == "screen_off":
        tracker["screen_on"] = False

    # Recompute active state
    new_active = tracker["screen_on"] and not tracker["server_locked"]
    was_active = tracker["active_since"] is not None

    if was_active and not new_active:
        tracker["accumulated_secs"] += (now - tracker["active_since"]).total_seconds()
        tracker["active_since"] = None
    elif not was_active and new_active:
        tracker["active_since"] = now

    # Compute current usage
    current_secs = tracker["accumulated_secs"]
    if tracker["active_since"]:
        current_secs += (now - tracker["active_since"]).total_seconds()
    current_mins = current_secs / 60

    locked = None
    # Day rollover may have unlocked
    if tracker["server_locked"] != was_locked_before:
        locked = tracker["server_locked"]

    if not tracker["triggered"] and current_mins >= threshold:
        tracker["triggered"] = True
        _notify_threshold(device_id, device_name, current_mins, threshold)

        if _alert_config.get("auto_lock"):
            db.set_device_locked(conn, device_id, True)
            db.insert_action_log(conn, device_id, "lock", {"reason": "usage_threshold"}, "controller")
            tracker["auto_locked"] = True
            tracker["server_locked"] = True
            if tracker["active_since"]:
                tracker["accumulated_secs"] += (now - tracker["active_since"]).total_seconds()
                tracker["active_since"] = None
            locked = True

    return locked


def update_lock(device_id, locked):
    """Update tracker when a lock/unlock command is sent from the controller."""
    tracker = _usage_trackers.get(device_id)
    if not tracker:
        return

    if not locked:
        tracker["auto_locked"] = False

    now = datetime.now()
    tracker["server_locked"] = locked

    new_active = tracker["screen_on"] and not tracker["server_locked"]
    was_active = tracker["active_since"] is not None

    if was_active and not new_active:
        tracker["accumulated_secs"] += (now - tracker["active_since"]).total_seconds()
        tracker["active_since"] = None
    elif not was_active and new_active:
        tracker["active_since"] = now

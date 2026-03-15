import logging
import re
import sys
from datetime import datetime

import db
import device_timeline

sys.path.insert(0, "PyTelegramBot")
from pytelegrambot import TelegramBot

log = logging.getLogger(__name__)

_alert_config = {
    "daily_limit_mins": None,  # None = disabled
    "auto_lock": False,
    "usage_ignore_regex": None,
    "warning_enabled": False,
    "warning_mins": 5,
    "telegram_bot_token": None,
    "telegram_chat_id": None,
}

_telegram_bot = None
_usage_ignore_re = None  # compiled regex, None means nothing ignored

_usage_trackers = {}  # device_id -> tracker dict
_group_state = {}  # group_id -> {"triggered": bool, "warned": bool, "auto_locked": bool, "date": str}


def _is_app_ignored(app):
    return app is not None and _usage_ignore_re is not None and _usage_ignore_re.search(app)


def configure(config):
    global _alert_config, _telegram_bot, _usage_ignore_re
    _alert_config = config
    for tracker in _usage_trackers.values():
        tracker["triggered"] = False
        tracker["warned"] = False
    for gs in _group_state.values():
        gs["triggered"] = False
        gs["warned"] = False

    pattern = config.get("usage_ignore_regex")
    if pattern:
        try:
            _usage_ignore_re = re.compile(pattern)
        except re.error:
            log.warning("Invalid usage_ignore_regex: %s", pattern)
            _usage_ignore_re = None
    else:
        _usage_ignore_re = None

    token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")
    if token and chat_id:
        try:
            _telegram_bot = TelegramBot(token, [int(chat_id)])
            log.info("Telegram bot connected")
        except Exception:
            log.warning("Failed to connect Telegram bot", exc_info=True)
            _telegram_bot = None
    else:
        _telegram_bot = None


def reset_triggered(device_id):
    tracker = _usage_trackers.get(device_id)
    if tracker:
        tracker["triggered"] = False
        tracker["warned"] = False
        group_id = tracker.get("group_id")
        if group_id and group_id in _group_state:
            _group_state[group_id]["triggered"] = False
            _group_state[group_id]["warned"] = False


def _seed_tracker(conn, device_id):
    """Build tracker state from existing data (one-time cost per device after restart)."""
    today = datetime.now().strftime("%Y-%m-%d")
    usage_mins, _ = device_timeline.get_today_usage_split(conn, device_id, _usage_ignore_re)

    dev = db.get_device(conn, device_id)
    server_locked = bool(dev["locked"])
    screen_on = db.get_current_screen_state(conn, device_id)
    current_app = db.get_current_foreground_app(conn, device_id)
    active = screen_on and not server_locked
    counting = active and not _is_app_ignored(current_app)
    now = datetime.now()

    tracker = {
        "date": today,
        "accumulated_secs": usage_mins * 60,
        "counting_since": now if counting else None,
        "active": active,
        "screen_on": screen_on,
        "server_locked": server_locked,
        "current_app": current_app,
        "triggered": False,
        "warned": False,
        "auto_locked": False,
        "group_id": dev["group_id"],
    }
    _usage_trackers[device_id] = tracker
    return tracker


def _get_tracker(conn, device_id):
    if device_id not in _usage_trackers:
        return _seed_tracker(conn, device_id)
    return _usage_trackers[device_id]


def _notify_threshold(device_id, device_name, usage_mins, threshold_mins):
    """Notify that a device has exceeded its daily usage threshold."""
    msg = (f"'{device_name}' reached "
           f"{usage_mins:.0f}min usage (threshold: {threshold_mins}min)")
    print(f"[ALERT] {datetime.now().strftime('%H:%M:%S')} — {msg}")

    if _telegram_bot:
        chat_id = _alert_config.get("telegram_chat_id")
        if chat_id:
            try:
                _telegram_bot.send_message(int(chat_id), f"⚠️ {msg}")
            except Exception:
                log.warning("Failed to send Telegram alert", exc_info=True)


def _get_threshold(conn, device_id):
    """Return the effective threshold for a device.

    Resolution order: group override > device override > global config.
    """
    dev = db.get_device(conn, device_id)
    if dev["group_id"]:
        group = db.get_device_group(conn, dev["group_id"])
        if group and group["daily_limit_mins"] is not None:
            return group["daily_limit_mins"]
    if dev["daily_limit_mins"] is not None:
        return dev["daily_limit_mins"]
    return _alert_config.get("daily_limit_mins")


def _get_group_state(group_id, today):
    """Get or create group-level state for triggered/warned/auto_locked tracking."""
    if group_id not in _group_state:
        _group_state[group_id] = {
            "triggered": False,
            "warned": False,
            "auto_locked": False,
            "date": today,
        }
    gs = _group_state[group_id]
    if gs["date"] != today:
        gs["triggered"] = False
        gs["warned"] = False
        gs["auto_locked"] = False
        gs["date"] = today
    return gs


def _get_group_usage_secs(group_id, now):
    """Sum accumulated usage across all trackers belonging to a group."""
    total = 0
    for tracker in _usage_trackers.values():
        if tracker.get("group_id") != group_id:
            continue
        total += tracker["accumulated_secs"]
        if tracker["counting_since"]:
            total += (now - tracker["counting_since"]).total_seconds()
    return total


def _get_group_device_ids(group_id):
    """Return device IDs of all trackers belonging to a group."""
    return [did for did, t in _usage_trackers.items() if t.get("group_id") == group_id]


def update_device_group_assignment(device_id, group_id):
    """Update tracker when a device's group assignment changes."""
    tracker = _usage_trackers.get(device_id)
    if tracker:
        old_group = tracker.get("group_id")
        tracker["group_id"] = group_id
        if old_group and old_group in _group_state:
            _group_state[old_group]["triggered"] = False
            _group_state[old_group]["warned"] = False
        if group_id and group_id in _group_state:
            _group_state[group_id]["triggered"] = False
            _group_state[group_id]["warned"] = False


def check_usage(conn, device_id, device_name, action, extra_args=None):
    """Update usage tracker and check threshold. Called on every device report."""
    threshold = _get_threshold(conn, device_id)
    if threshold is None:
        return None

    tracker = _get_tracker(conn, device_id)
    was_locked_before = tracker["server_locked"]
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    group_id = tracker.get("group_id")

    # Day rollover — reset tracker, unlock if auto-locked
    if tracker["date"] != today:
        if group_id:
            # Group-level day rollover: unlock all group members if group was auto-locked
            gs = _get_group_state(group_id, today)  # resets group state for new day
            if tracker["auto_locked"]:
                # Each device handles its own unlock on rollover
                db.set_device_locked(conn, device_id, False)
                db.insert_action_log(conn, device_id, "unlock", {"reason": "daily_reset"}, "controller")
                tracker["server_locked"] = False
        else:
            if tracker["auto_locked"]:
                db.set_device_locked(conn, device_id, False)
                db.insert_action_log(conn, device_id, "unlock", {"reason": "daily_reset"}, "controller")
                tracker["server_locked"] = False
        tracker["date"] = today
        tracker["accumulated_secs"] = 0
        tracker["triggered"] = False
        tracker["warned"] = False
        tracker["auto_locked"] = False
        if tracker["counting_since"]:
            tracker["counting_since"] = now

    # Update screen state from device-reported events
    if action == "screen_on":
        tracker["screen_on"] = True
    elif action == "screen_off":
        tracker["screen_on"] = False
    elif action == "app_change" and extra_args:
        app = extra_args.get("new_activity", "Unknown")
        app_name = app.split("/")[0] if "/" in app else app
        if app_name != "Unknown":
            tracker["current_app"] = app_name

    # Recompute active state (screen on + not locked) — determines lock behavior
    new_active = tracker["screen_on"] and not tracker["server_locked"]
    tracker["active"] = new_active

    # Recompute counting state (active + app not ignored) — determines usage accumulation
    new_counting = new_active and not _is_app_ignored(tracker.get("current_app"))
    was_counting = tracker["counting_since"] is not None

    if was_counting and not new_counting:
        tracker["accumulated_secs"] += (now - tracker["counting_since"]).total_seconds()
        tracker["counting_since"] = None
    elif not was_counting and new_counting:
        tracker["counting_since"] = now

    locked = None
    # Day rollover may have unlocked
    if tracker["server_locked"] != was_locked_before:
        locked = tracker["server_locked"]

    # Determine usage and state tracking based on group membership
    if group_id:
        current_secs = _get_group_usage_secs(group_id, now)
        current_mins = current_secs / 60
        gs = _get_group_state(group_id, today)

        # Warning notification before threshold — send to all group devices
        warning_mins = _alert_config.get("warning_mins", 5)
        warning_at = threshold - warning_mins
        if (_alert_config.get("warning_enabled")
                and not gs["warned"]
                and warning_at > 0
                and current_mins >= warning_at
                and current_mins < threshold):
            gs["warned"] = True
            remaining = max(1, round(threshold - current_mins))
            cmd = {"name": "show_notification", "msg": f"{remaining} minutes before shutdown", "timeout": 10}
            for did in _get_group_device_ids(group_id):
                db.replace_pending_command(conn, did, "show_notification", cmd)

        if not gs["triggered"] and current_mins >= threshold:
            gs["triggered"] = True
            _notify_threshold(device_id, device_name, current_mins, threshold)

            if _alert_config.get("auto_lock"):
                gs["auto_locked"] = True
                # Lock all devices in the group
                for did in _get_group_device_ids(group_id):
                    t = _usage_trackers.get(did)
                    if t:
                        db.set_device_locked(conn, did, True)
                        db.insert_action_log(conn, did, "lock", {"reason": "usage_threshold"}, "controller")
                        t["auto_locked"] = True
                        t["server_locked"] = True
                        t["active"] = False
                        if t["counting_since"]:
                            t["accumulated_secs"] += (now - t["counting_since"]).total_seconds()
                            t["counting_since"] = None
                locked = True
    else:
        # Ungrouped device — original per-device logic
        current_secs = tracker["accumulated_secs"]
        if tracker["counting_since"]:
            current_secs += (now - tracker["counting_since"]).total_seconds()
        current_mins = current_secs / 60

        # Warning notification before threshold
        warning_mins = _alert_config.get("warning_mins", 5)
        warning_at = threshold - warning_mins
        if (_alert_config.get("warning_enabled")
                and not tracker["warned"]
                and warning_at > 0
                and current_mins >= warning_at
                and current_mins < threshold):
            tracker["warned"] = True
            remaining = max(1, round(threshold - current_mins))
            cmd = {"name": "show_notification", "msg": f"{remaining} minutes before shutdown", "timeout": 10}
            db.replace_pending_command(conn, device_id, "show_notification", cmd)

        if not tracker["triggered"] and current_mins >= threshold:
            tracker["triggered"] = True
            _notify_threshold(device_id, device_name, current_mins, threshold)

            if _alert_config.get("auto_lock"):
                db.set_device_locked(conn, device_id, True)
                db.insert_action_log(conn, device_id, "lock", {"reason": "usage_threshold"}, "controller")
                tracker["auto_locked"] = True
                tracker["server_locked"] = True
                tracker["active"] = False
                if tracker["counting_since"]:
                    tracker["accumulated_secs"] += (now - tracker["counting_since"]).total_seconds()
                    tracker["counting_since"] = None
                locked = True

    return locked


def update_lock(device_id, locked):
    """Update tracker when a lock/unlock command is sent from the controller."""
    tracker = _usage_trackers.get(device_id)
    if not tracker:
        return

    if not locked:
        tracker["auto_locked"] = False
        group_id = tracker.get("group_id")
        if group_id and group_id in _group_state:
            _group_state[group_id]["auto_locked"] = False

    now = datetime.now()
    tracker["server_locked"] = locked

    new_active = tracker["screen_on"] and not tracker["server_locked"]
    tracker["active"] = new_active
    new_counting = new_active and not _is_app_ignored(tracker.get("current_app"))
    was_counting = tracker["counting_since"] is not None

    if was_counting and not new_counting:
        tracker["accumulated_secs"] += (now - tracker["counting_since"]).total_seconds()
        tracker["counting_since"] = None
    elif not was_counting and new_counting:
        tracker["counting_since"] = now

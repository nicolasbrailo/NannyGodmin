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


def _is_app_ignored(app):
    return app is not None and _usage_ignore_re is not None and _usage_ignore_re.search(app)


def configure(config):
    global _alert_config, _telegram_bot, _usage_ignore_re
    _alert_config = config
    for tracker in _usage_trackers.values():
        tracker["triggered"] = False
        tracker["warned"] = False

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


def _seed_tracker(conn, device_id):
    """Build tracker state from existing data (one-time cost per device after restart)."""
    today = datetime.now().strftime("%Y-%m-%d")
    usage_mins = device_timeline.get_today_usage(conn, device_id)

    dev = db.get_device(conn, device_id)
    server_locked = bool(dev["locked"])
    screen_on = db.get_current_screen_state(conn, device_id)
    current_app = db.get_current_foreground_app(conn, device_id)
    active = screen_on and not server_locked and not _is_app_ignored(current_app)
    now = datetime.now()

    tracker = {
        "date": today,
        "accumulated_secs": usage_mins * 60,
        "active_since": now if active else None,
        "screen_on": screen_on,
        "server_locked": server_locked,
        "current_app": current_app,
        "triggered": False,
        "warned": False,
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
    """Return the effective threshold for a device (per-device override or global)."""
    dev = db.get_device(conn, device_id)
    if dev["daily_limit_mins"] is not None:
        return dev["daily_limit_mins"]
    return _alert_config.get("daily_limit_mins")


def check_usage(conn, device_id, device_name, action, extra_args=None):
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
        tracker["warned"] = False
        tracker["auto_locked"] = False
        if tracker["active_since"]:
            tracker["active_since"] = now

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

    # Recompute active state
    new_active = tracker["screen_on"] and not tracker["server_locked"] and not _is_app_ignored(tracker.get("current_app"))
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

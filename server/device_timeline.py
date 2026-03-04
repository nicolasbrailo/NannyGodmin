import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import db


def compute_usage_timeline(conn, device_id):
    """Compute active/inactive transitions and daily usage hours from screen/lock events."""
    rows = db.get_usage_events(conn, device_id)

    screen_on = False
    server_locked = False
    active = False
    transitions = []

    for row in rows:
        ts = row["timestamp"]
        action = row["action"]
        source = row["source"]

        if action == "screen_on":
            screen_on = True
        elif action == "screen_off":
            screen_on = False
        elif action == "lock" and source == "controller":
            server_locked = True
        elif action == "unlock" and source == "controller":
            server_locked = False
        else:
            continue

        new_active = screen_on and not server_locked
        if new_active != active:
            active = new_active
            transitions.append({
                "timestamp": ts,
                "screen_on": screen_on,
                "server_locked": server_locked,
                "active": active,
            })

    daily_hours = defaultdict(float)
    for i, t in enumerate(transitions):
        if not t["active"]:
            continue
        start = datetime.fromisoformat(t["timestamp"])
        if i + 1 < len(transitions):
            end = datetime.fromisoformat(transitions[i + 1]["timestamp"])
        else:
            end = datetime.now(timezone.utc) if start.tzinfo else datetime.now()

        cursor = start
        while cursor < end:
            day_str = cursor.strftime("%Y-%m-%d")
            midnight = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            segment_end = min(end, midnight)
            daily_hours[day_str] += (segment_end - cursor).total_seconds() / 3600
            cursor = segment_end

    daily_hours = dict(sorted(daily_hours.items()))
    return transitions, daily_hours


def get_today_usage(conn, device_id):
    """Return today's usage in minutes for a single device."""
    _, daily_hours = compute_usage_timeline(conn, device_id)
    today = datetime.now().strftime("%Y-%m-%d")
    return round(daily_hours.get(today, 0) * 60)


def compute_daily_slots(transitions):
    """Convert transitions into per-day 15-minute slot arrays (96 slots per day).

    Returns (daily_slots, slot_hours) where daily_slots is trimmed to the
    global active range and slot_hours contains the hour labels."""
    if not transitions:
        return {}, []

    # Collect all active intervals
    intervals = []
    for i, t in enumerate(transitions):
        if not t["active"]:
            continue
        start = datetime.fromisoformat(t["timestamp"])
        if i + 1 < len(transitions):
            end = datetime.fromisoformat(transitions[i + 1]["timestamp"])
        else:
            end = datetime.now(timezone.utc) if start.tzinfo else datetime.now()
        intervals.append((start, end))

    if not intervals:
        return {}, []

    # Determine date range
    first_day = intervals[0][0].date()
    last_day = intervals[-1][1].date()

    daily_slots = {}
    day = first_day
    while day <= last_day:
        slots = [False] * 96
        day_start = datetime.combine(day, datetime.min.time())
        if intervals[0][0].tzinfo:
            day_start = day_start.replace(tzinfo=timezone.utc)

        for start, end in intervals:
            seg_start = max(start, day_start)
            seg_end = min(end, day_start + timedelta(days=1))
            if seg_start >= seg_end:
                continue
            first_slot = int((seg_start - day_start).total_seconds()) // 900
            last_slot = int((seg_end - day_start).total_seconds() - 1) // 900
            for s in range(max(0, first_slot), min(95, last_slot) + 1):
                slots[s] = True

        daily_slots[day.isoformat()] = slots
        day += timedelta(days=1)

    # Trim to the global active range
    slot_start = 95
    slot_end = 0
    for slots in daily_slots.values():
        for i, active in enumerate(slots):
            if active:
                slot_start = min(slot_start, i)
                slot_end = max(slot_end, i)
    if slot_start <= slot_end:
        daily_slots = {d: s[slot_start:slot_end + 1] for d, s in daily_slots.items()}
    else:
        slot_start = 0
        slot_end = 95

    first_hour = slot_start * 15 // 60
    last_hour = (slot_end * 15 + 15) // 60
    slot_hours = list(range(first_hour, last_hour + 1))

    return daily_slots, slot_hours


def compute_app_timeline(conn, device_id):
    """Build a timeline of foreground app usage from app_change and screen events.

    Returns list of dicts: {app, start, end, duration_secs}.

    Data model quirks handled here:
    - The Android accessibility service reports app_change with new_activity
      set to "Unknown" during UI transitions (e.g. screen wake, app switch
      animations). This does NOT mean the screen is off — the real app keeps
      running. We ignore all app_change→Unknown events entirely.
    - Actual screen state is determined by screen_off / screen_on events.
      screen_off ends the current app span; screen_on just re-enables tracking.
    - app_change and screen_on sometimes arrive at the same timestamp in
      either order, so we don't gate app_change processing on screen_on state.

    Post-processing:
    1. Merge contiguous spans of the same app (same end→start timestamp,
       meaning no screen_off gap between them).
    2. Drop spans shorter than 60 seconds (brief app flickers during setup,
       lock screen, etc).
    3. Re-merge after dropping, in case removing a short span made two
       same-app spans adjacent.
    4. Merge same-app spans separated by short gaps (<5 min). Typical cause:
       screen briefly turns off (e.g. auto-lock) then comes back to the same
       app. The gap time is absorbed into the merged span since the user
       likely intended continuous use.
    """
    rows = db.get_app_and_screen_events(conn, device_id)
    if not rows:
        return []

    # Phase 1: Build raw spans from the event stream.
    #
    # We track the current foreground app. screen_off closes the current span
    # (creating a gap in the timeline — screen-off time is not attributed to
    # any app). app_change to a real app starts/switches spans.
    raw = []
    current_app = None
    current_start = None

    for row in rows:
        ts = datetime.fromisoformat(row["timestamp"])
        action = row["action"]

        if action == "screen_off":
            # End whatever app is running — screen is off, no app is "active"
            if current_app:
                raw.append({"app": current_app, "start": current_start, "end": ts})
                current_app = None
                current_start = None
        elif action == "screen_on":
            pass  # Just marks screen as on; we wait for an app_change to start a span
        elif action == "app_change":
            args = json.loads(row["args"]) if isinstance(row["args"], str) else row["args"]
            app = args.get("new_activity", "Unknown")
            app_name = app.split("/")[0] if "/" in app else app
            # "Unknown" is accessibility-service noise, not a real app transition
            if app_name == "Unknown":
                continue
            if current_app and current_app != app_name:
                raw.append({"app": current_app, "start": current_start, "end": ts})
            if current_app != app_name:
                current_app = app_name
                current_start = ts

    # Close the last open span (device still on)
    if current_app and current_start:
        now = datetime.now(timezone.utc) if current_start.tzinfo else datetime.now()
        raw.append({"app": current_app, "start": current_start, "end": now})

    if not raw:
        return []

    # Phase 2: Merge contiguous spans of the same app.
    # Only merge when end == start (no screen_off gap between them).
    merged = []
    for span in raw:
        if merged and merged[-1]["app"] == span["app"] and merged[-1]["end"] == span["start"]:
            merged[-1]["end"] = span["end"]
        else:
            merged.append(dict(span))

    # Phase 3: Drop short spans (<60s) — brief flickers from app switching,
    # lock screen, etc. Gaps between spans are preserved (we don't extend
    # neighbors to fill the dropped span's time).
    collapsed = [s for s in merged if (s["end"] - s["start"]).total_seconds() >= 60]

    # Phase 4: Re-merge after dropping (adjacent same-app spans may now be contiguous)
    merged = []
    for span in collapsed:
        if merged and merged[-1]["app"] == span["app"] and merged[-1]["end"] == span["start"]:
            merged[-1]["end"] = span["end"]
        else:
            merged.append(span)

    # Phase 5: Merge same-app spans separated by short gaps (<5 min).
    # When the screen briefly turns off and comes back to the same app
    # (e.g. auto-lock, brief screen-off), treat it as one continuous session.
    # The gap time is absorbed into the merged span.
    MAX_GAP = 300  # 5 minutes
    gap_merged = []
    for span in merged:
        if (gap_merged
                and gap_merged[-1]["app"] == span["app"]
                and (span["start"] - gap_merged[-1]["end"]).total_seconds() < MAX_GAP):
            gap_merged[-1]["end"] = span["end"]
        else:
            gap_merged.append(dict(span))

    return [
        {
            "app": s["app"],
            "start": s["start"].isoformat(),
            "end": s["end"].isoformat(),
            "duration_secs": int((s["end"] - s["start"]).total_seconds()),
        }
        for s in gap_merged
    ]

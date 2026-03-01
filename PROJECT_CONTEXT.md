# NannyGodmin Project Context

## Overview
NannyGodmin is an MDM-like Android background monitoring and remote control application. It is designed to run as a persistent foreground service, track user activity, capture screenshots remotely, and allow a remote server to "lock" the device or adjust system settings.

Note this project assumes it's running in a trusted environment (both server and client), and performs no SSL or cert verification. This is a big attack vector if not run in fully trusted environments.

## Key Components

### 1. `MainService.kt`
- **Role**: The central coordinator.
- **Functionality**:
    - Manages `UserActivityTracker` and `RemoteControl`.
    - Listens for screen on/off events to pause/resume tracking and polling.
    - Ensures persistent execution and handles component lifecycle.
    - Delegates provisioning settings retrieval to `ConfigActivity`.

### 2. `RemoteControl.kt`
- **Role**: Bi-directional communication with the remote server.
- **Reporting**: Calls `/device_report` (POST) every `poll_interval_secs` or on event (app change, screen toggle).
- **Commands**:
    - **Hardware Lock**: Server sends a `locked` boolean flag. If true, the app uses `DevicePolicyManager.lockNow()` and stays in `LockActivity`.
    - `set_volume`: Adjusts system music volume.
    - `send_screenshot`: Triggers a screen capture via `NannyAccessibilityService` and uploads it to `/device_report/screenshot`.
- **Error Handling**: Receiving a 404 or 401 triggers a re-provisioning flow by signaling `MainService`.

### 3. `UserActivityTracker.kt`
- **Role**: Monitors foreground activity changes using `UsageStatsManager`.
- **Interaction**: Triggers a callback when the foreground app changes, passed to `RemoteControl` for reporting.

### 4. `LockActivity.kt`
- **Role**: Persistent overlay when the device is "locked".
- **Persistence**: 
    - Disables back button.
    - Intercepts key events (Volume, Home, App Switch).
    - Uses `moveTaskToFront` only when the screen is interactive (`isInteractive`) to avoid waking the screen during a hardware lock.

### 5. `NannyAccessibilityService.kt`
- **Role**: High-privilege service for screen interaction and capture.
- **Functionality**: Provides `takeScreenshot()` capability (Android 11+).

### 6. `ConfigActivity.kt`
- **Role**: Onboarding, Provisioning, and Permissions.
- **Provisioning**: 
    - `POST /provision` sends `deviceName` and `androidId`.
    - Stores `server_url`, `client_id`, and `poll_interval_secs`.
    - Supports Deep Linking (`nannygodmin://config?url=...`).
- **Permissions**: Manages Device Admin, Usage Stats, Overlay, and Accessibility Service.

## Data Storage
- Uses `SharedPreferences` (`"prefs"`) for:
    - `server_url`, `client_id`, `poll_interval_secs`.

## Communication Protocol
- **Provisioning**: `POST /provision` -> Body: `{"deviceName": "string", "androidId": "string"}` -> Response: `{"clientId": "string", "poll_interval_secs": int}`.
- **Reporting/Polling**: `POST /device_report` -> Body: `{"clientId": "string", "action": "poll|app_change|screen_on|screen_off", ...}` -> Response: `{"locked": bool, "commands": JSONArray}`.
- **Screenshots**: `POST /device_report/screenshot` -> Body: `image/png` binary -> Header: `X-Client-Id`.

## Current State & Restrictions
- Target SDK: 36.
- Minimum SDK: 28.
- Screenshotting requires manual user activation of the Accessibility Service.
- Device Admin is used for hardware screen locking (`lockNow`).

# NannyGodmin Project Context

## Overview
NannyGodmin is an MDM-like Android background monitoring and remote control application. It is designed to run as a persistent foreground service, track user activity (specifically which apps are being used), and allow a remote server to "lock" the device or adjust settings like volume.

Note this project assumes it's running in a trusted environment (both server and client), and performs no ssl or cert verification. This is a big attack vector if not run in fully trusted environments.

## Key Components

### 1. `MainService.kt`
- **Role**: The central coordinator.
- **Lifecycle**: Runs as a Foreground Service with `START_STICKY`.
- **Functionality**:
    - Initializes and manages `UserActivityTracker` and `RemoteControl`.
    - Listens for screen on/off events via `BroadcastReceiver` to pause/resume tracking and polling to save battery.
    - Ensures persistent execution by overriding `onTaskRemoved`.

### 2. `RemoteControl.kt`
- **Role**: Communication layer between the app and the remote server.
- **Endpoints**:
    - `/device_report`: POST requests every 10 seconds (or on event). Sends `clientId` and `action` (poll, app_change, screen_on, screen_off).
    - Expects a JSON array of commands in response (e.g., `[{"name": "lock"}, {"name": "set_volume", "arg": 50}]`).
- **Command Handling**:
    - `lock`: Activates `isLocked` state and launches `LockActivity`.
    - `unlock`: Deactivates `isLocked` and sends a local broadcast to close `LockActivity`.
    - `set_volume`: Adjusts system music volume.

### 3. `UserActivityTracker.kt`
- **Role**: Monitors foreground activity changes.
- **Mechanism**: Uses `UsageStatsManager` to poll for `ACTIVITY_RESUMED` events.
- **Interaction**: Triggers a callback when the foreground activity changes, which `RemoteControl` uses to report to the server and enforce locks.

### 4. `LockActivity.kt`
- **Role**: The "Lock Screen" UI.
- **Persistence**:
    - Disables the back button via `OnBackPressedDispatcher`.
    - Uses `moveTaskToFront` in `onPause` to prevent switching apps.
    - Uses flags to show over the system lock screen.
    - Receives a local broadcast (`ACTION_UNLOCK`) from `RemoteControl` to `finish()` itself.

### 5. `ConfigActivity.kt`
- **Role**: Onboarding and Provisioning.
- **Provisioning**:
    - Supports manual URL entry and Deep Linking (`nannygodmin://config?url=...`).
    - Calls `/provision` (POST) with `deviceName` to receive a `clientId`.
- **Permissions**: Provides buttons to grant:
    1. **Device Admin**: Prevents easy uninstallation.
    2. **Usage Stats**: Required for `UserActivityTracker`.
    3. **Overlay Permission**: Allows drawing over system UI.

## Data Storage
- Uses `SharedPreferences` (named `"prefs"`) to store:
    - `server_url`: The base URL of the control server.
    - `client_id`: The unique ID assigned by the server during provisioning.

## Communication Protocol
- **Provisioning**: `POST /provision` -> Body: `{"deviceName": "string"}` -> Response: `{"clientId": "string"}`.
- **Reporting/Polling**: `POST /device_report` -> Body: `{"clientId": "string", "action": "poll|app_change|screen_on|screen_off", ...}` -> Response: `JSONArray` of commands.

## Current State & Restrictions
- Target SDK: 36.
- Minimum SDK: 28.
- Uses `FOREGROUND_SERVICE_TYPE_SPECIAL_USE` (requires Android 14+ specific declaration).
- `ACTION_CLOSE_SYSTEM_DIALOGS` is deprecated and restricted; locking relies on `moveTaskToFront` and Overlay permissions.

## Pending Work / TODOs
- Dynamic retrieval of settings (like poll interval) during provisioning.
- Implementing more granular commands from the server.
- Strengthening the "Lock" mechanism (potentially via Accessibility Service if deeper control is needed).

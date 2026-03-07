# NannyGodmin 

NannyGodmin is an MDM-like Android background monitoring and remote control application. It is designed to run as a persistent foreground service, track user activity, capture screenshots remotely, and allow a remote server to "lock" the device or adjust system settings. It's not meant to be used as a real MDM (for starters, there is no attestation), it's meant to be used as a remote control for devices in a known safe security domain (ie only use it in your LAN).

## Features

TODO


## Installing

Install the server environment with `make rebuild_deps` then `make run` to start the service. For each client: build the apk and install as normal `adb install ./app-debug.apk`. Alternatively, you may download the apk from the server and install manually. Once installed it should open the config view, where you can grant the necessary permissions to the app, and follow the provisioning steps. To provision, enter your server's URL or scan the server's QR with your device.

## Installing in an Amazon Fire tablet

Amazon tables are slightly special, with different policies than most normal Androids. You also probably want to enable the service for a user account, not for the main account. To install Godmin in one:

1. `adb shell pm list users` to find the user id you need
2. `adb install -t --user 11 ./app-debug.apk` (replace the user id; -t is only needed if installing the debug apk)
3. `adb shell pm enable --user 11 com.nicobrailo.nannygodmin`
4. `adb shell am start --user 11 -n com.nicobrailo.nannygodmin/.ConfigActivity`

The app won't appear in the dashboard, but the last command should start it and let you provision it. Some features won't be available, but usage monitoring and locking seems to work.


# Task Hounds Mobile

Independent Android client copied from `ui/web`. Changes in this directory do
not affect the desktop WebUI.

The connection address is stored only in the WebView's local IndexedDB
database. Enter the private URL reported by `tailscale serve status`:

`https://<device-name>.<tailnet-name>.ts.net`

## Prerequisites

- Node.js and npm
- Android Studio with an Android SDK
- Tailscale installed and connected on the Android device
- On the Task Hounds computer:
  `tailscale serve --bg http://127.0.0.1:8766`
- JDK 21

## Build

```powershell
cd mobile
npm install
npm run android:sync
npm run android:open
```

The `android/` project is already included. Run `npm run android:add` only when
regenerating it from scratch.

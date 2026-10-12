# AMS2 Career Hub

An Automobilista 2 career dashboard with race logging, cross-device account sync, championship tracking, and a Windows desktop companion for race uploads.

## Dashboard

Serve the repository root with a static host such as Vercel, or open `index.html` for local-only use. Sign-in and cloud sync require the Firebase settings in `firebase-config.js`, Firebase Email/Password Authentication, and Realtime Database.

The dashboard tracks total races, wins, podiums, clean races, incident averages, career ratings, race history, and scheduled events. Create a championship with its season, round count, and points-per-finish list, then assign manual race results to it. Companion uploads can also be associated with a championship.

Restrict Realtime Database access to the signed-in account owner. A minimal ruleset for this app is:

```json
{
  "rules": {
    "ams2Hub": {
      "$uid": {
        ".read": "auth != null && auth.uid === $uid",
        ".write": "auth != null && auth.uid === $uid"
      }
    }
  }
}
```

Do not use open read/write rules for a deployed app.

## Windows Companion

Requires Python 3 with Tkinter (included in the standard Windows Python installer). From the repository root:

```powershell
python companion/tracker.py
```

1. Sign in with the same Firebase account used by the dashboard. The app reads the Firebase API key and database URL from `firebase-config.js`; they can also be entered in the Account tab. The password is not saved.
2. Use **Race Upload** to upload a result manually, or refresh and select a championship first.
3. For automatic capture, configure a local SimHub JSON endpoint and map its properties in the **SimHub** tab. Use **Test endpoint** to check the values before starting monitoring. Choose a championship for automatic captures if desired.

The companion polls the configured JSON endpoint, recognizes session values containing `race`, remembers the last mapped race data, and uploads when the session changes away from race. Position and incident values must be numeric. Uploaded entries are written under the signed-in user's `companionRaces` Firebase child and imported idempotently into dashboard career totals and history.

SimHub does not provide one stable, universal JSON endpoint/property map across every game and installation. The endpoint and mappings therefore need to match the local SimHub integration in use; automatic capture is not plug-and-play until the endpoint test returns the expected session, track, car, position, and incident values. Manual companion uploads work independently of that telemetry setup.

## Hosting and Offline Use

Static files can be hosted on Vercel or another static host. The PWA files enable install and offline shell use over HTTPS; Firebase account sync requires an internet connection. If you change Tailwind classes, rebuild `app.css` with Tailwind CLI 3.4.17 and `tailwind.config.js`.
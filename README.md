# AMS2 Career Hub

An Automobilista 2 career dashboard with race logging, cross-device account sync, championship tracking, and a Windows desktop companion for race uploads.

## Dashboard

Serve the repository root with a static host such as Vercel, or open `index.html` for local-only use. Sign-in and cloud sync require the Firebase settings in `firebase-config.js`, Firebase Email/Password Authentication, and Realtime Database.

The dashboard tracks total races, wins, podiums, career ratings, race history, scheduled events, and championships with custom points. Use the **Active competition** selector to mark one championship or single event as the current multiplayer session. Race results captured while monitoring is active are attached to that selection. Races from the SimHub bridge include game, track, car/class, and finish position; incident counts are not captured.

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

1. Build the SimHub plugin from the repository root:

  ```powershell
  dotnet build simhub-plugin\Ams2HubCapturePlugin.csproj
  ```

2. Close SimHub, then copy `simhub-plugin\bin\Debug\net48\Ams2HubCapturePlugin.dll` into the SimHub install folder (typically `C:\Program Files (x86)\SimHub`). Windows may request administrator permission. Restart SimHub so it loads the plugin.
3. Run `python companion/tracker.py` and sign in with the same Firebase account as the dashboard. The Firebase API key and database URL are read from `firebase-config.js`; the account password is not saved.
4. In the web dashboard, set a championship or event as **Active competition**. In the companion's **SimHub** tab, click **Refresh competitions**, then **Test plugin**. Start monitoring only after the active competition and telemetry endpoint are shown.

The plugin exposes SimHub's normalized game, session, track, car/class, and position data on `127.0.0.1:17891`. The companion watches for race sessions and uploads their final position to the signed-in user's Firebase inbox, tagged with the active championship or event. Keep the companion open and monitoring during the session. Changes to the active competition in the dashboard are picked up automatically within a few seconds; a race remains tagged to the competition that was active when it began. With no active competition, automatic capture is paused. The active selection is the explicit multiplayer marker; this avoids mistaking offline AI fields for proof of an online lobby.

The SimHub bridge does not capture incident counts. The dashboard displays incident statistics only for records that actually include incident data.

## Hosting and Offline Use

Static files can be hosted on Vercel or another static host. The PWA files enable install and offline shell use over HTTPS; Firebase account sync requires an internet connection. If you change Tailwind classes, rebuild `app.css` with Tailwind CLI 3.4.17 and `tailwind.config.js`.
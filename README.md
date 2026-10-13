# AMS2 Race Events Hub

An Automobilista 2 race-events dashboard for planning championships, tracking race results, and syncing SimHub captures.

## Dashboard

Serve the repository root with a static host such as Vercel, or open `index.html` for local-only use. Sign-in and cloud sync require the Firebase settings in `firebase-config.js`, Firebase Email/Password Authentication, and Realtime Database.

The dashboard focuses on total races, wins, podiums, average finish, race history, scheduled events, and championships with fixed points scoring. Each championship uses one shared vehicle/class, optional lobby name/password, and a planned track and local time slot for every round. Track and vehicle fields suggest values already seen in your history; the search field filters championships by series, round track, or vehicle. The existing dashboard next-race widget shows the earliest upcoming item and opens the full chronological calendar. The calendar combines single events and championship rounds. While the companion is monitoring, scheduled items activate automatically ten minutes before their start. Race results are matched to uncompleted rounds by detected track and the shared championship vehicle, using race start time to distinguish repeated rounds. Mismatches remain visible on the championship/event card for confirmation in the HTML app. Races from the SimHub bridge include game, track, car model/class, and finish position; incident counts are not captured.

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

Lobby passwords are stored in the signed-in user's Realtime Database record and are not end-to-end encrypted. Only save lobby credentials you are comfortable storing in that account.

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
4. In the dashboard, turn **Auto-activation on** for any single events you want tracked. Championship rounds use their scheduled time automatically. In the companion's **SimHub** tab, click **Refresh competitions**, then **Test plugin**, and start monitoring. Keep the companion open while the schedule is active.

The plugin exposes SimHub's normalized game, session, track, car model/class, and position data on `127.0.0.1:17891`. The companion watches for race sessions and uploads their final position to the signed-in user's Firebase inbox, tagged with the scheduled championship round or single event. Scheduled championship rounds and single events enabled with **Auto-activation on** activate ten minutes before their start time. Calendar changes are picked up while monitoring. A race stays tagged to the competition active when the race began. The dashboard attaches exact track/vehicle matches to planned rounds. If telemetry differs, the result remains in the competition with a **Needs confirmation** action; confirming an unplanned result can add it as an extra round. Scheduled calendar items are the multiplayer marker; the app doesn't guess online status from opponent counts.

The SimHub bridge does not capture incident counts.

## Hosting and Offline Use

Static files can be hosted on Vercel or another static host. The PWA files enable install and offline shell use over HTTPS; Firebase account sync requires an internet connection. If you change Tailwind classes, rebuild `app.css` with Tailwind CLI 3.4.17 and `tailwind.config.js`.
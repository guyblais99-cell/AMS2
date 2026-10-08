# AMS2 Racing Career Hub

The app is served from the repository root. Keep `index.html` in the root so Vercel can serve it as the homepage. The `api/` directory contains Vercel serverless functions; `lib/` holds shared server helpers. The PWA manifest, service worker, and icons are root-level files so they share the app's root scope.

```text
api/auth.js
api/data.js
api/lobbies.js
api/logout.js
api/profile.js
lib/server.js
```

## Deploy to Vercel

1. Create a GitHub repository and upload the files individually. Keep `index.html`, `app.css`, `src.css`, `tailwind.config.js`, `package.json`, `vercel.json`, `manifest.webmanifest`, `sw.js`, `.gitignore`, `README.md`, `icon-192.svg`, and both `icon-*.png` files at the repository root.
2. Use GitHub's **Add file → Create new file** and include the folder in each filename, for example `api/auth.js` or `lib/server.js`. Add every file in `api/` and `lib/`; do not upload the Windows `desktop.ini` files.
3. Import the repository into Vercel. Create an Upstash Redis database from the Vercel Marketplace and add its REST URL and REST token to the project's environment variables.
4. Set the following environment variables for Production (and Preview if desired), then make an initial deployment:

   - `SESSION_SECRET`: a private random secret with at least 32 characters.
   - `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`: the Upstash database REST URL and token. Vercel integrations may name these `KV_REST_API_URL` and `KV_REST_API_TOKEN`; the app accepts either pair.
   - `STEAM_API_KEY`: optional for Steam profile/game-stat and public server-list lookups; keep it in Vercel settings and never put it in `index.html`.

5. After Vercel gives you the stable HTTPS URL, set `APP_URL` to that exact origin with no trailing slash (for example, `https://your-project.vercel.app`) and redeploy. For Preview deployments, set `APP_URL` to the Preview URL in the Preview environment; otherwise use Production for Steam sign-in.
6. To show Steam's public profile and any game statistics AMS2 makes available, create a Steam Web API key using your Vercel hostname as the domain, set `STEAM_API_KEY`, and redeploy. Steam profile privacy settings can prevent statistics from being returned. This key is optional for Steam sign-in and cloud sync.
7. Open the deployed HTTPS site and choose **Sign in with Steam**. The app uses Steam OpenID to verify the Steam account; it never asks for or receives your Steam password.

### Generate a session secret on Windows PowerShell

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
[Convert]::ToBase64String($bytes)
```

Copy the output into the Vercel `SESSION_SECRET` setting. Do not upload it to GitHub.

## Steam data and AMS2 limitations

Steam sign-in identifies the account. Steam's Web API only returns game statistics that AMS2 publishes and makes visible to the API; it does not automatically expose native AMS2 multiplayer safety/skill ratings. The app reports returned Steam statistics as published data, and keeps its own manually adjustable/career-calculated ratings separate.

The multiplayer lookup queries Steam's public dedicated-server list using Automobilista 2's Steam App ID (1066890). That list is not a complete AMS2 lobby browser: private sessions, peer-hosted sessions, and servers not returned by Steam's dedicated-server query will not appear. Race schedules and logged results are stored in the account's cloud data.

## PWA behavior

The app is installable on HTTPS-capable browsers. Its service worker caches the app shell, local CSS, and static resources for offline viewing. Sign-in, Steam lookups, and cloud saves require an internet connection. If you edit Tailwind classes later, rebuild `app.css` from `src.css` using Tailwind CLI 3.4.17 and `tailwind.config.js`.

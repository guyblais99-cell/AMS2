# AMS2 Career Dashboard

A standalone, offline-capable dashboard for tracking your Automobilista 2 career. There is no Steam link, login, server, or game-data connection; every value is entered by hand and stored in your browser (`localStorage`).

## Using it

- **Edit Profile & Stats**: set driver name, title, level/XP, Safety and Skill ratings, and totals (races, wins, podiums, clean races, incidents).
- **Log Race Result**: records a race and updates stats, XP, and ratings.
- **Career & Ladder**: shows tier progress and trophies based on your level and ratings.
- **Race Calendar**: schedule and track your own sessions.

Data stays on the device/browser you use. Clearing site data erases it.

## Hosting

Static files only: serve the folder root with any static host (Vercel, GitHub Pages, etc.) or open `index.html` directly. The PWA files (`manifest.webmanifest`, `sw.js`, icons) enable install and offline use over HTTPS. If you edit Tailwind classes, rebuild `app.css` from `src.css` using Tailwind CLI 3.4.17 and `tailwind.config.js`.

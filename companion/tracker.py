import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


APP_NAME = "AMS2 Career Hub Companion"
POLL_SECONDS = 1.5
ACTIVE_COMPETITION_SYNC_SECONDS = 3
SCHEDULE_REFRESH_SECONDS = 30
AUTO_ACTIVATION_LEAD = timedelta(minutes=10)
SIMHUB_TELEMETRY_URL = "http://127.0.0.1:17891/telemetry"
CONFIG_FILE = Path(os.environ.get("APPDATA", Path.home())) / "AMS2CareerHub" / "companion.json"
WEB_CONFIG_FILE = Path(__file__).resolve().parents[1] / "firebase-config.js"
DEFAULT_FIELDS = {
    "session": "session",
    "game": "game",
    "track": "track",
    "carModel": "car",
    "carClass": "carClass",
    "position": "position",
}


def read_firebase_config():
    if not WEB_CONFIG_FILE.exists():
        return {}
    text = WEB_CONFIG_FILE.read_text(encoding="utf-8")
    return {key: match.group(1) for key in ("apiKey", "databaseURL")
            if (match := re.search(rf"\b{key}\s*:\s*['\"]([^'\"]+)", text))}


def request_json(url, payload=None, method=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None or method in {"POST", "PUT", "PATCH"} else None
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def request_form(url, values):
    body = urllib.parse.urlencode(values).encode("ascii")
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read())


def firebase_database_url(database_url, path, id_token):
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    return f"{database_url.rstrip('/')}/{encoded_path}.json?auth={urllib.parse.quote(id_token, safe='')}"


def normalize_active_competition(value):
    if not isinstance(value, dict) or value.get("type") not in {"championship", "event"} or value.get("id") is None:
        return None
    normalized = {
        "type": value["type"],
        "id": str(value["id"]),
        "name": str(value.get("name") or "Active competition"),
        "source": value.get("source") or "manual",
    }
    for key in ("roundId", "roundNumber", "startTime"):
        if value.get(key) is not None:
            normalized[key] = value[key]
    return normalized


def parse_schedule_time(value):
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.astimezone()
        return date.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def rows_from_firebase(value):
    if isinstance(value, dict):
        return [row for row in value.values() if isinstance(row, dict)]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def scheduled_active_competition(events, championships, now=None, completed=()):
    """Stay active for the whole event duration; after that only until a result is recorded (3h cap)."""
    now = now or datetime.now(timezone.utc)
    completed = set(completed)
    candidates = []
    for event in rows_from_firebase(events):
        if not event.get("scheduled"):
            continue
        start = parse_schedule_time(event.get("startTime"))
        if not start:
            continue
        duration_match = re.search(r"\d+", str(event.get("duration", "45")))
        duration_minutes = int(duration_match.group()) if duration_match else 45
        min_end = start + timedelta(minutes=max(5, duration_minutes))
        if f"event:{event.get('id', '')}" in completed:
            end = min_end
        else:
            end = max(min_end + timedelta(minutes=30), start + timedelta(hours=3))
        if start - AUTO_ACTIVATION_LEAD <= now <= end:
            candidates.append((start, {"type": "event", "id": str(event.get("id", "")),
                                       "name": str(event.get("title") or "Scheduled event"),
                                       "source": "schedule", "startTime": start.isoformat()}))

    for championship in rows_from_firebase(championships):
        for round_plan in rows_from_firebase(championship.get("roundPlans")):
            start = parse_schedule_time(round_plan.get("startTime"))
            if not start or not (start - AUTO_ACTIVATION_LEAD <= now <= start + timedelta(hours=3)):
                continue
            if now > start + timedelta(minutes=45) and f"championship:{championship.get('id', '')}:{round_plan.get('id', '')}" in completed:
                continue
            name = str(championship.get("name") or "Championship")
            candidates.append((start, {
                "type": "championship",
                "id": str(championship.get("id", "")),
                "name": name,
                "source": "schedule",
                "roundId": str(round_plan.get("id", "")),
                "roundNumber": int(round_plan.get("roundNumber") or 0),
                "startTime": start.isoformat(),
            }))

    if not candidates:
        return None
    underway = [item for item in candidates if item[0] <= now]
    return max(underway, key=lambda item: item[0])[1] if underway else min(candidates, key=lambda item: item[0])[1]


def nested_value(data, path):
    if not path:
        return None
    value = data
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def lap_ms(value):
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    return milliseconds if 1000 <= milliseconds <= 3600000 else None


def merge_best_laps(laps, payload):
    """Keep each driver's best lap seen during the race; return the player's own best from this frame."""
    for driver in payload.get("drivers") or []:
        if not isinstance(driver, dict):
            continue
        name = str(driver.get("name") or "").strip()[:60]
        milliseconds = lap_ms(driver.get("bestLapMs"))
        if not name or milliseconds is None:
            continue
        entry = laps.setdefault(name, {"name": name, "bestLapMs": milliseconds, "isPlayer": False})
        entry["bestLapMs"] = min(entry["bestLapMs"], milliseconds)
        entry["isPlayer"] = entry["isPlayer"] or bool(driver.get("isPlayer"))
    return lap_ms(payload.get("bestLapMs"))


def finished_lap_times(laps, player_best):
    rows = [dict(entry) for entry in laps.values()]
    own = next((row for row in rows if row["isPlayer"]), None)
    if own:
        own["bestLapMs"] = min(own["bestLapMs"], player_best) if player_best else own["bestLapMs"]
        player_best = own["bestLapMs"]
    elif player_best:
        rows.append({"name": "You", "bestLapMs": player_best, "isPlayer": True})
    rows.sort(key=lambda row: row["bestLapMs"])
    return rows[:64], player_best


class CompanionApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("720x760")
        self.root.minsize(620, 680)
        self.root.configure(bg="#11151b")
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.active_sync_stop_event = threading.Event()
        self.monitor_thread = None
        self.active_sync_thread = None
        self.completed_competitions = set()
        self.auto_watch_thread = None
        self.auto_watch_stop_event = threading.Event()
        self.manual_stop = False
        self.user = None
        self.id_token = None
        self.refresh_token = None
        self.token_expires_at = 0
        self.token_lock = threading.Lock()
        self.championships = {}
        self.single_events = {}
        self.competitions_by_label = {}
        self.active_competition = None
        self.racing = False
        self.race_snapshot = None
        self.config = self.load_config()
        self.build_ui()
        self.root.after(200, self.process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(500, self.restore_session)

    def load_config(self):
        defaults = {
            "api_key": read_firebase_config().get("apiKey", ""),
            "database_url": read_firebase_config().get("databaseURL", ""),
        }
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            defaults.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
        return defaults

    def save_config(self):
        self.config.update({
            "api_key": self.api_key_var.get().strip(),
            "database_url": self.database_url_var.get().strip().rstrip("/"),
        })
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#11151b", borderwidth=0)
        style.configure("TNotebook.Tab", background="#242b34", foreground="#d8e0e8", padding=(12, 8))
        style.map("TNotebook.Tab", background=[("selected", "#00d7e8")], foreground=[("selected", "#071116")])
        style.configure("TCombobox", fieldbackground="#202731", background="#202731", foreground="#f0f4f7")

        header = tk.Frame(self.root, bg="#11151b", padx=20, pady=16)
        header.pack(fill="x")
        tk.Label(header, text="AMS2 HUB", bg="#11151b", fg="#f2f6fa", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        tk.Label(header, text="RACE TELEMETRY COMPANION", bg="#11151b", fg="#00d7e8", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self.status_var = tk.StringVar(value="Not signed in")
        tk.Label(header, textvariable=self.status_var, bg="#11151b", fg="#aab6c2", font=("Segoe UI", 9)).pack(anchor="e")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        account_tab = tk.Frame(notebook, bg="#171d25", padx=18, pady=18)
        race_tab = tk.Frame(notebook, bg="#171d25", padx=18, pady=18)
        telemetry_tab = tk.Frame(notebook, bg="#171d25", padx=18, pady=18)
        notebook.add(account_tab, text="Account")
        notebook.add(race_tab, text="Race Upload")
        notebook.add(telemetry_tab, text="SimHub")
        self.build_account_tab(account_tab)
        self.build_race_tab(race_tab)
        self.build_telemetry_tab(telemetry_tab)

        self.log_text = tk.Text(self.root, height=7, bg="#0c1015", fg="#b7c4cf", insertbackground="white",
                                relief="flat", wrap="word", font=("Consolas", 9), padx=10, pady=8)
        self.log_text.pack(fill="x", padx=16, pady=(0, 16))
        self.log("Ready. Sign in, set an active competition on the dashboard, then start monitoring.")

    def label(self, parent, text):
        tk.Label(parent, text=text, bg="#171d25", fg="#dbe4ec", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(9, 3))

    def entry(self, parent, variable, show=None):
        return tk.Entry(parent, textvariable=variable, show=show, bg="#202731", fg="#f0f4f7",
                        insertbackground="white", relief="flat", highlightthickness=1,
                        highlightbackground="#394450", highlightcolor="#00d7e8")

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg="#00d7e8" if primary else "#293440",
                         fg="#071116" if primary else "#e3ebf2", activebackground="#4ce7f2",
                         relief="flat", padx=12, pady=8, font=("Segoe UI", 9, "bold"), cursor="hand2")

    def build_account_tab(self, parent):
        self.api_key_var = tk.StringVar(value=self.config.get("api_key", ""))
        self.database_url_var = tk.StringVar(value=self.config.get("database_url", ""))
        self.email_var = tk.StringVar(value=self.config.get("email", ""))
        self.password_var = tk.StringVar()
        self.label(parent, "Firebase Web API key")
        self.entry(parent, self.api_key_var).pack(fill="x")
        self.label(parent, "Realtime Database URL")
        self.entry(parent, self.database_url_var).pack(fill="x")
        self.label(parent, "Account email")
        self.entry(parent, self.email_var).pack(fill="x")
        self.label(parent, "Account password")
        self.entry(parent, self.password_var, show="*").pack(fill="x")
        actions = tk.Frame(parent, bg="#171d25")
        actions.pack(fill="x", pady=14)
        self.button(actions, "Sign in", self.sign_in, primary=True).pack(side="left")
        self.button(actions, "Sign out", self.sign_out).pack(side="left", padx=8)
        tk.Label(parent, text="Uses the same Firebase account as the web dashboard. The password is never saved; a refresh token is stored locally so you stay signed in until you sign out.",
                 bg="#171d25", fg="#8493a1", wraplength=580, justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))

    def build_race_tab(self, parent):
        self.race_vars = {key: tk.StringVar(value=value) for key, value in {
            "game": "Automobilista 2", "track": "", "carClass": "", "position": "1"
        }.items()}
        labels = [("game", "Game"), ("track", "Track"), ("carClass", "Car / class"), ("position", "Finish position")]
        for key, label in labels:
            self.label(parent, label)
            self.entry(parent, self.race_vars[key]).pack(fill="x")
        self.label(parent, "Competition")
        self.championship_var = tk.StringVar(value="Career race")
        self.championship_combo = ttk.Combobox(parent, textvariable=self.championship_var, state="readonly")
        self.championship_combo["values"] = ("Career race",)
        self.championship_combo.pack(fill="x")
        self.button(parent, "Refresh championships", self.refresh_championships).pack(anchor="w", pady=(9, 0))
        self.button(parent, "Upload race result", self.upload_manual_race, primary=True).pack(anchor="e", pady=(15, 0))

    def build_telemetry_tab(self, parent):
        self.label(parent, "SimHub bridge endpoint")
        tk.Label(parent, text=SIMHUB_TELEMETRY_URL, bg="#171d25", fg="#00d7e8", font=("Consolas", 10)).pack(anchor="w", pady=(4, 8))
        tk.Label(parent, text="Install and enable the AMS2 Career Hub Capture plugin in SimHub. It exposes normalized game, session, track, car/class, and position data on this PC only.",
                 bg="#171d25", fg="#8493a1", wraplength=590, justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 12))
        self.active_competition_var = tk.StringVar(value="No active competition loaded")
        self.label(parent, "Active competition from dashboard")
        tk.Label(parent, textvariable=self.active_competition_var, bg="#171d25", fg="#f0f4f7", wraplength=590,
                 justify="left", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4, 8))
        actions = tk.Frame(parent, bg="#171d25")
        actions.pack(fill="x", pady=8)
        self.button(actions, "Refresh competitions", self.refresh_championships).pack(side="left")
        self.button(actions, "Test plugin", self.test_endpoint).pack(side="left", padx=8)
        self.monitor_button = self.button(actions, "Start monitoring", self.toggle_monitor, primary=True)
        self.monitor_button.pack(side="right")

    def log(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {text}\n")
        self.log_text.see("end")

    def process_events(self):
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(data)
            elif kind == "status":
                self.status_var.set(data)
            elif kind == "capture":
                self.upload_race(data, "Automatic race captured")
            elif kind == "active_competition":
                self.apply_active_competition(data)
            elif kind == "auto_start":
                self.start_monitor(auto=True)
            elif kind == "stopped":
                self.monitor_button.configure(text="Start monitoring", state="normal")
        self.root.after(200, self.process_events)

    def sign_in(self):
        self.save_config()
        api_key = self.api_key_var.get().strip()
        email = self.email_var.get().strip()
        password = self.password_var.get()
        if not api_key or not email or not password or not self.database_url_var.get().strip():
            messagebox.showerror("Sign in", "Enter Firebase settings, email, and password.")
            return
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={urllib.parse.quote(api_key)}"
        try:
            response = request_json(url, {"email": email, "password": password, "returnSecureToken": True}, "POST")
            self.user = {"localId": response["localId"], "email": response.get("email", email)}
            self.id_token = response["idToken"]
            self.refresh_token = response.get("refreshToken")
            self.token_expires_at = time.monotonic() + max(60, int(response.get("expiresIn", 3600)) - 60)
            self.status_var.set(f"Signed in: {self.user['email']}")
            self.log(f"Signed in as {self.user['email']}.")
            self.password_var.set("")
            self.config.update({"email": self.user["email"], "uid": self.user["localId"],
                                "refresh_token": self.refresh_token or ""})
            self.save_config()
            self.refresh_championships()
            self.start_auto_watch()
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as error:
            messagebox.showerror("Sign in failed", self.error_message(error))

    def restore_session(self):
        saved_token = self.config.get("refresh_token")
        uid = self.config.get("uid")
        if not saved_token or not uid or not self.api_key_var.get().strip():
            return
        try:
            self.user = {"localId": uid, "email": self.config.get("email", "")}
            with self.token_lock:
                self.refresh_token = saved_token
                self.id_token = "x"
                self.token_expires_at = 0
            self.ensure_token()
            self.config["refresh_token"] = self.refresh_token or saved_token
            self.save_config()
            self.status_var.set(f"Signed in: {self.user['email']}")
            self.log(f"Restored saved session for {self.user['email']}.")
            self.refresh_championships()
            self.start_auto_watch()
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, RuntimeError) as error:
            self.user = None
            with self.token_lock:
                self.id_token = None
                self.refresh_token = None
            self.log(f"Saved session expired; sign in again. ({self.error_message(error)})")

    def sign_out(self):
        self.config.pop("refresh_token", None)
        self.config.pop("uid", None)
        self.save_config()
        self.auto_watch_stop_event.set()
        self.stop_event.set()
        self.active_sync_stop_event.set()
        self.user = None
        with self.token_lock:
            self.id_token = None
            self.refresh_token = None
            self.token_expires_at = 0
        self.championships = {}
        self.single_events = {}
        self.competitions_by_label = {}
        self.active_competition = None
        self.championship_var.set("Career race")
        self.championship_combo["values"] = ("Career race",)
        self.active_competition_var.set("Sign in to load active competition.")
        self.status_var.set("Not signed in")
        self.log("Signed out.")

    def error_message(self, error):
        if isinstance(error, urllib.error.HTTPError):
            try:
                payload = json.loads(error.read().decode("utf-8"))
                return payload.get("error", {}).get("message", str(error))
            except (json.JSONDecodeError, OSError):
                return f"HTTP {error.code}"
        return str(error)

    def database_url(self, path):
        with self.token_lock:
            token = self.id_token or ""
        return firebase_database_url(self.database_url_var.get().strip(), path, token)

    def ensure_token(self, api_key=None):
        with self.token_lock:
            if not self.user or not self.id_token:
                raise RuntimeError("Sign in before syncing race data.")
            if time.monotonic() < self.token_expires_at:
                return self.id_token
            if not self.refresh_token:
                raise RuntimeError("Session expired. Sign in again.")
            key = api_key or self.api_key_var.get().strip()
            url = f"https://securetoken.googleapis.com/v1/token?key={urllib.parse.quote(key)}"
            response = request_form(url, {"grant_type": "refresh_token", "refresh_token": self.refresh_token})
            self.id_token = response["id_token"]
            self.refresh_token = response.get("refresh_token", self.refresh_token)
            self.token_expires_at = time.monotonic() + max(60, int(response.get("expires_in", 3600)) - 60)
            return self.id_token

    def apply_active_competition(self, competition):
        if competition == self.active_competition:
            return
        self.active_competition = competition
        if competition:
            self.active_competition_var.set(f"{competition['type'].title()}: {competition['name']}")
            self.log(f"Active competition changed to {competition['name']}.")
        else:
            self.active_competition_var.set("Waiting for a scheduled event or dashboard selection.")
            self.log("No competition is active; scheduled auto-activation is waiting.")

    def refresh_championships(self):
        if not self.user:
            return
        self.active_competition = None
        self.active_competition_var.set("Refreshing active competition...")
        try:
            self.ensure_token()
            state = request_json(self.database_url(f"ams2Hub/{self.user['localId']}")) or {}
            if not isinstance(state, dict):
                raise ValueError("Firebase returned an invalid account record.")
            championship_rows = state.get("championships") or []
            event_rows = state.get("events") or []
            if isinstance(championship_rows, dict):
                championship_rows = list(championship_rows.values())
            if isinstance(event_rows, dict):
                event_rows = list(event_rows.values())
            self.championships = {str(item.get("id")): item for item in championship_rows
                                  if isinstance(item, dict) and item.get("id")}
            self.single_events = {str(item.get("id")): item for item in event_rows
                                  if isinstance(item, dict) and item.get("id")}
            labels = ["Career race"]
            self.competitions_by_label = {}
            for key, item in self.championships.items():
                label = f"Championship: {item.get('name', 'Championship')} [{key}]"
                labels.append(label)
                self.competitions_by_label[label] = {"type": "championship", "id": key, "name": item.get("name", "Championship")}
            for key, item in self.single_events.items():
                label = f"Event: {item.get('title', 'Event')} [{key}]"
                labels.append(label)
                self.competitions_by_label[label] = {"type": "event", "id": key, "name": item.get("title", "Event")}
            self.championship_combo["values"] = labels
            self.active_competition = scheduled_active_competition(
                list(self.single_events.values()),
                list(self.championships.values()),
            )
            active_label = "Career race"
            if isinstance(self.active_competition, dict):
                active_key = f"{self.active_competition.get('type')}:{self.active_competition.get('id')}"
                for label, competition in self.competitions_by_label.items():
                    if f"{competition['type']}:{competition['id']}" == active_key:
                        self.active_competition = {**competition, **self.active_competition}
                        active_label = label
                        break
            self.active_competition_var.set(
                f"{self.active_competition['type'].title()}: {self.active_competition['name']}"
                if self.active_competition else "Waiting for a scheduled event or dashboard selection."
            )
            self.championship_var.set(active_label)
            self.log(f"Loaded {len(self.championships)} championships, {len(self.single_events)} events, and active competition.")
        except (urllib.error.URLError, json.JSONDecodeError, RuntimeError, TypeError, ValueError) as error:
            self.active_competition_var.set("Unable to refresh active competition.")
            self.log(f"Could not load championships: {self.error_message(error)}")

    def selected_competition(self):
        return self.competitions_by_label.get(self.championship_var.get())

    def upload_manual_race(self):
        try:
            position = int(self.race_vars["position"].get())
            if not 1 <= position <= 99:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid result", "Finish position must be between 1 and 99.")
            return
        race = {
            "game": self.race_vars["game"].get().strip() or "Unknown sim",
            "track": self.race_vars["track"].get().strip() or "Unknown track",
            "carClass": self.race_vars["carClass"].get().strip() or "Unknown class",
            "position": position,
        }
        competition = self.selected_competition() or self.active_competition
        if competition:
            race[f"{competition['type']}Id"] = competition["id"]
        self.upload_race(race, "Race result uploaded")

    def upload_race(self, race, success_message):
        if not self.user or not self.id_token:
            self.log("Race not uploaded: sign in first.")
            return
        race_id = str(uuid.uuid4())
        record = {**race, "id": race_id, "date": datetime.now(timezone.utc).date().isoformat(),
                  "recordedAt": datetime.now(timezone.utc).isoformat(), "source": "AMS2 Hub Companion"}
        try:
            self.ensure_token()
            request_json(self.database_url(f"ams2Hub/{self.user['localId']}/companionRaces/{race_id}"), record, "PUT")
            self.log(f"{success_message}: P{record['position']} at {record.get('track', 'Unknown track')}.")
        except (urllib.error.URLError, json.JSONDecodeError, RuntimeError) as error:
            self.log(f"Upload failed: {self.error_message(error)}")

    def test_endpoint(self):
        try:
            data = request_json(SIMHUB_TELEMETRY_URL)
            if not isinstance(data, dict):
                raise ValueError("Plugin response is not a JSON object.")
            mapped = {key: nested_value(data, path) for key, path in DEFAULT_FIELDS.items()}
            self.log("SimHub plugin connected. Telemetry: " + json.dumps(mapped, ensure_ascii=True))
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
            self.log(f"SimHub plugin is not reachable: {self.error_message(error)}")

    def toggle_monitor(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.manual_stop = True
            self.stop_event.set()
            self.active_sync_stop_event.set()
            self.monitor_button.configure(text="Stopping...", state="disabled")
            return
        self.manual_stop = False
        self.start_monitor()

    def start_auto_watch(self):
        if self.auto_watch_thread and self.auto_watch_thread.is_alive():
            return
        self.auto_watch_stop_event.clear()
        self.auto_watch_thread = threading.Thread(target=self.auto_watch_loop, daemon=True)
        self.auto_watch_thread.start()

    def auto_watch_loop(self):
        while not self.auto_watch_stop_event.is_set():
            try:
                if self.user and not self.manual_stop and not (self.monitor_thread and self.monitor_thread.is_alive()):
                    self.ensure_token()
                    base = f"ams2Hub/{self.user['localId']}"
                    events = request_json(self.database_url(f"{base}/events"))
                    championships = request_json(self.database_url(f"{base}/championships"))
                    if scheduled_active_competition(events, championships, completed=self.completed_competitions):
                        self.events.put(("auto_start", None))
            except (urllib.error.URLError, json.JSONDecodeError, RuntimeError, ValueError):
                pass
            self.auto_watch_stop_event.wait(SCHEDULE_REFRESH_SECONDS)

    def start_monitor(self, auto=False):
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        if not self.user:
            if not auto:
                messagebox.showerror("Sign in required", "Sign in before starting telemetry monitoring.")
            return
        self.refresh_championships()
        try:
            telemetry = request_json(SIMHUB_TELEMETRY_URL)
            if not isinstance(telemetry, dict) or "session" not in telemetry:
                raise ValueError("The plugin returned an unexpected response.")
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
            if auto:
                self.log(f"Auto-start waiting for SimHub plugin: {self.error_message(error)}")
            else:
                messagebox.showerror("SimHub plugin unavailable", f"Start SimHub, install the AMS2 Career Hub Capture plugin, then test it again.\n\n{self.error_message(error)}")
            return
        if auto:
            self.log("Scheduled event is active; monitoring started automatically.")
        self.racing = False
        self.race_snapshot = None
        settings = {
            "url": SIMHUB_TELEMETRY_URL,
            "fields": DEFAULT_FIELDS.copy(),
            "competition": self.active_competition.copy() if self.active_competition else None,
            "user_id": self.user["localId"],
            "database_url": self.database_url_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
        }
        self.active_sync_stop_event.clear()
        self.monitor_button.configure(text="Stop monitoring", state="normal")
        self.events.put(("status", f"Monitoring: {self.user['email']}"))
        self.monitor_thread = threading.Thread(target=self.monitor_loop, args=(settings,), daemon=True)
        self.active_sync_thread = threading.Thread(target=self.active_competition_loop, args=(settings,), daemon=True)
        self.monitor_thread.start()
        self.active_sync_thread.start()

    def active_competition_loop(self, settings):
        last_error = 0
        competition = normalize_active_competition(settings.get("competition"))
        events = []
        championships = []
        next_schedule_refresh = 0.0
        while not self.active_sync_stop_event.is_set():
            try:
                token = self.ensure_token(settings["api_key"])
                active_url = firebase_database_url(
                    settings["database_url"],
                    f"ams2Hub/{settings['user_id']}/activeCompetition",
                    token,
                )
                remote_competition = normalize_active_competition(request_json(active_url))
                now_monotonic = time.monotonic()
                if now_monotonic >= next_schedule_refresh:
                    events = rows_from_firebase(request_json(firebase_database_url(
                        settings["database_url"], f"ams2Hub/{settings['user_id']}/events", token
                    )))
                    championships = rows_from_firebase(request_json(firebase_database_url(
                        settings["database_url"], f"ams2Hub/{settings['user_id']}/championships", token
                    )))
                    next_schedule_refresh = now_monotonic + SCHEDULE_REFRESH_SECONDS
                scheduled = scheduled_active_competition(events, championships, completed=self.completed_competitions)
                updated_competition = scheduled
                if updated_competition != remote_competition:
                    request_json(active_url, updated_competition, "PUT")
                if updated_competition != competition:
                    competition = updated_competition
                    self.events.put(("active_competition", competition))
                last_error = 0
            except (urllib.error.URLError, json.JSONDecodeError, RuntimeError, ValueError) as error:
                now = time.monotonic()
                if now - last_error > 30:
                    self.events.put(("log", f"Active competition sync failed: {self.error_message(error)}"))
                    last_error = now
            self.active_sync_stop_event.wait(ACTIVE_COMPETITION_SYNC_SECONDS)

    def monitor_loop(self, settings):
        previous_error = 0
        race_competition = None
        race_started_at = None
        race_laps = {}
        player_best = None
        while not self.stop_event.is_set():
            try:
                payload = request_json(settings["url"])
                fields = {key: nested_value(payload, path) for key, path in settings["fields"].items()}
                session = str(fields.get("session") or "").strip().lower()
                in_race = "race" in session and "results" not in session
                if in_race:
                    if not self.racing:
                        race_competition = self.active_competition.copy() if self.active_competition else None
                        race_started_at = datetime.now(timezone.utc).isoformat()
                        race_laps = {}
                        player_best = None
                    self.racing = True
                    self.race_snapshot = fields
                    own_best = merge_best_laps(race_laps, payload)
                    if own_best:
                        player_best = min(player_best, own_best) if player_best else own_best
                elif self.racing:
                    snapshot = self.race_snapshot or fields
                    self.racing = False
                    position = self.to_int(snapshot.get("position"))
                    if position and position > 0 and race_competition:
                        race = {
                            "game": str(snapshot.get("game") or "Unknown sim"),
                            "track": str(snapshot.get("track") or "Unknown track"),
                            "carModel": str(snapshot.get("carModel") or "Unknown vehicle"),
                            "carClass": str(snapshot.get("carClass") or "Unknown class"),
                            "position": position,
                            "startedAt": race_started_at,
                        }
                        lap_times, own_best_lap = finished_lap_times(race_laps, player_best)
                        if own_best_lap:
                            race["bestLapMs"] = own_best_lap
                        if lap_times:
                            race["lapTimes"] = lap_times
                        race[f"{race_competition['type']}Id"] = race_competition["id"]
                        self.events.put(("capture", race))
                        key = f"{race_competition['type']}:{race_competition['id']}"
                        if race_competition.get("roundId"):
                            key += f":{race_competition['roundId']}"
                        self.completed_competitions.add(key)
                    elif not race_competition:
                        self.events.put(("log", "Race ignored because no active competition was set when it began."))
                    else:
                        self.events.put(("log", "Race session ended, but no valid finish position was mapped; skipped upload."))
                    race_competition = None
                    race_started_at = None
                    race_laps = {}
                    player_best = None
                previous_error = 0
            except (urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
                now = time.monotonic()
                if now - previous_error > 30:
                    self.events.put(("log", f"Telemetry read failed: {self.error_message(error)}"))
                    previous_error = now
            self.stop_event.wait(POLL_SECONDS)
        self.events.put(("status", "Monitoring stopped" if self.user else "Not signed in"))
        self.events.put(("log", "Telemetry monitoring stopped."))
        self.events.put(("stopped", None))

    @staticmethod
    def to_int(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def close(self):
        self.auto_watch_stop_event.set()
        self.stop_event.set()
        self.active_sync_stop_event.set()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    CompanionApp(window)
    window.mainloop()
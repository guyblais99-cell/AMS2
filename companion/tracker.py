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
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


APP_NAME = "AMS2 Career Hub Companion"
POLL_SECONDS = 1.5
CONFIG_FILE = Path(os.environ.get("APPDATA", Path.home())) / "AMS2CareerHub" / "companion.json"
WEB_CONFIG_FILE = Path(__file__).resolve().parents[1] / "firebase-config.js"
DEFAULT_FIELDS = {
    "session": "DataCorePlugin.Computed.SessionTypeName",
    "game": "GameName",
    "track": "TrackName",
    "car": "CarName",
    "position": "Position",
    "incidents": "Incidents",
}


def read_firebase_config():
    if not WEB_CONFIG_FILE.exists():
        return {}
    text = WEB_CONFIG_FILE.read_text(encoding="utf-8")
    return {key: match.group(1) for key in ("apiKey", "databaseURL")
            if (match := re.search(rf"\b{key}\s*:\s*['\"]([^'\"]+)", text))}


def request_json(url, payload=None, method=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
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


class CompanionApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("720x760")
        self.root.minsize(620, 680)
        self.root.configure(bg="#11151b")
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.monitor_thread = None
        self.user = None
        self.id_token = None
        self.refresh_token = None
        self.token_expires_at = 0
        self.championships = {}
        self.racing = False
        self.race_snapshot = None
        self.config = self.load_config()
        self.build_ui()
        self.root.after(200, self.process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def load_config(self):
        defaults = {
            "api_key": read_firebase_config().get("apiKey", ""),
            "database_url": read_firebase_config().get("databaseURL", ""),
            "telemetry_url": "",
            "fields": DEFAULT_FIELDS.copy(),
        }
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            defaults.update(saved)
            defaults["fields"] = {**DEFAULT_FIELDS, **saved.get("fields", {})}
        except (OSError, json.JSONDecodeError):
            pass
        return defaults

    def save_config(self):
        self.config.update({
            "api_key": self.api_key_var.get().strip(),
            "database_url": self.database_url_var.get().strip().rstrip("/"),
            "telemetry_url": self.telemetry_url_var.get().strip(),
            "fields": {key: var.get().strip() for key, var in self.field_vars.items()},
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
        self.log("Ready. Sign in, then upload a test result or configure telemetry.")

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
        self.email_var = tk.StringVar()
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
        tk.Label(parent, text="Uses the same Firebase account as the web dashboard. The password is not saved; the session token stays in memory.",
                 bg="#171d25", fg="#8493a1", wraplength=580, justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))

    def build_race_tab(self, parent):
        self.race_vars = {key: tk.StringVar(value=value) for key, value in {
            "game": "Automobilista 2", "track": "", "carClass": "", "position": "1", "incidents": "0"
        }.items()}
        labels = [("game", "Game"), ("track", "Track"), ("carClass", "Car / class"),
                  ("position", "Finish position"), ("incidents", "Incident points")]
        for key, label in labels:
            self.label(parent, label)
            self.entry(parent, self.race_vars[key]).pack(fill="x")
        self.label(parent, "Championship")
        self.championship_var = tk.StringVar(value="Career race")
        self.championship_combo = ttk.Combobox(parent, textvariable=self.championship_var, state="readonly")
        self.championship_combo["values"] = ("Career race",)
        self.championship_combo.pack(fill="x")
        self.button(parent, "Refresh championships", self.refresh_championships).pack(anchor="w", pady=(9, 0))
        self.button(parent, "Upload race result", self.upload_manual_race, primary=True).pack(anchor="e", pady=(15, 0))

    def build_telemetry_tab(self, parent):
        self.telemetry_url_var = tk.StringVar(value=self.config.get("telemetry_url", ""))
        self.label(parent, "SimHub JSON URL")
        self.entry(parent, self.telemetry_url_var).pack(fill="x")
        self.field_vars = {key: tk.StringVar(value=self.config.get("fields", {}).get(key, default))
                           for key, default in DEFAULT_FIELDS.items()}
        for key, title in [("session", "Race session property"), ("game", "Game property"), ("track", "Track property"),
                           ("car", "Car / class property"), ("position", "Finish position property"), ("incidents", "Incidents property")]:
            self.label(parent, title)
            self.entry(parent, self.field_vars[key]).pack(fill="x")
        note = "Property paths use dot notation. Automatic capture starts when session contains 'race' and uploads when it changes away. Test your SimHub URL and property names first; they vary by setup and game."
        tk.Label(parent, text=note, bg="#171d25", fg="#8493a1", wraplength=590, justify="left", font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 4))
        self.label(parent, "Assign automatic captures to")
        self.auto_championship_var = tk.StringVar(value="Career race")
        self.auto_championship_combo = ttk.Combobox(parent, textvariable=self.auto_championship_var, state="readonly", values=("Career race",))
        self.auto_championship_combo.pack(fill="x")
        actions = tk.Frame(parent, bg="#171d25")
        actions.pack(fill="x", pady=8)
        self.button(actions, "Test endpoint", self.test_endpoint).pack(side="left")
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
            self.refresh_championships()
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as error:
            messagebox.showerror("Sign in failed", self.error_message(error))

    def sign_out(self):
        self.stop_event.set()
        self.user = None
        self.id_token = None
        self.refresh_token = None
        self.token_expires_at = 0
        self.championships = {}
        self.championship_var.set("Career race")
        self.championship_combo["values"] = ("Career race",)
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
        base = self.database_url_var.get().strip().rstrip("/")
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        return f"{base}/{encoded_path}.json?auth={urllib.parse.quote(self.id_token or '', safe='')}"

    def ensure_token(self):
        if not self.user or not self.id_token:
            raise RuntimeError("Sign in before syncing race data.")
        if time.monotonic() < self.token_expires_at:
            return
        if not self.refresh_token:
            raise RuntimeError("Session expired. Sign in again.")
        url = f"https://securetoken.googleapis.com/v1/token?key={urllib.parse.quote(self.api_key_var.get().strip())}"
        response = request_form(url, {"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        self.id_token = response["id_token"]
        self.refresh_token = response.get("refresh_token", self.refresh_token)
        self.token_expires_at = time.monotonic() + max(60, int(response.get("expires_in", 3600)) - 60)

    def refresh_championships(self):
        if not self.user:
            return
        try:
            self.ensure_token()
            entries = request_json(self.database_url(f"ams2Hub/{self.user['localId']}/championships")) or []
            if isinstance(entries, dict):
                entries = list(entries.values())
            self.championships = {str(item.get("id")): item for item in entries if isinstance(item, dict) and item.get("id")}
            labels = ["Career race"] + [f"{item.get('name', 'Championship')} - {item.get('season', 'Season')} [{key}]"
                                         for key, item in self.championships.items()]
            self.championship_combo["values"] = labels
            self.auto_championship_combo["values"] = labels
            self.log(f"Loaded {len(self.championships)} championships.")
        except (urllib.error.URLError, json.JSONDecodeError, RuntimeError) as error:
            self.log(f"Could not load championships: {self.error_message(error)}")

    def selected_championship_id(self):
        return self.championship_id_from(self.championship_var.get())

    @staticmethod
    def championship_id_from(selected):
        match = re.search(r"\[([^\]]+)\]$", selected)
        return match.group(1) if match else None

    def upload_manual_race(self):
        try:
            position = int(self.race_vars["position"].get())
            incidents = int(self.race_vars["incidents"].get())
            if not 1 <= position <= 99 or incidents < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid result", "Finish position must be 1-99 and incidents must be zero or more.")
            return
        race = {
            "game": self.race_vars["game"].get().strip() or "Unknown sim",
            "track": self.race_vars["track"].get().strip() or "Unknown track",
            "carClass": self.race_vars["carClass"].get().strip() or "Unknown class",
            "position": position,
            "incidents": incidents,
            "championshipId": self.selected_championship_id(),
        }
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
        self.save_config()
        try:
            data = request_json(self.telemetry_url_var.get().strip())
            if not isinstance(data, dict):
                raise ValueError("Endpoint JSON must be an object.")
            mapped = {key: nested_value(data, var.get().strip()) for key, var in self.field_vars.items()}
            self.log("Endpoint OK. Mapped values: " + json.dumps(mapped, ensure_ascii=True))
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
            self.log(f"Endpoint test failed: {self.error_message(error)}")

    def toggle_monitor(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.stop_event.set()
            self.monitor_button.configure(text="Stopping...", state="disabled")
            return
        if not self.user:
            messagebox.showerror("Sign in required", "Sign in before starting telemetry monitoring.")
            return
        self.save_config()
        if not self.telemetry_url_var.get().strip():
            messagebox.showerror("Telemetry URL", "Enter a SimHub JSON URL first.")
            return
        self.stop_event.clear()
        self.racing = False
        self.race_snapshot = None
        settings = {
            "url": self.telemetry_url_var.get().strip(),
            "fields": {key: var.get().strip() for key, var in self.field_vars.items()},
            "championship_id": self.championship_id_from(self.auto_championship_var.get()),
        }
        self.monitor_button.configure(text="Stop monitoring", state="normal")
        self.events.put(("status", f"Monitoring: {self.user['email']}"))
        self.monitor_thread = threading.Thread(target=self.monitor_loop, args=(settings,), daemon=True)
        self.monitor_thread.start()

    def monitor_loop(self, settings):
        previous_error = 0
        while not self.stop_event.is_set():
            try:
                payload = request_json(settings["url"])
                fields = {key: nested_value(payload, path) for key, path in settings["fields"].items()}
                session = str(fields.get("session") or "").strip().lower()
                in_race = "race" in session and "results" not in session
                if in_race:
                    self.racing = True
                    self.race_snapshot = fields
                elif self.racing:
                    snapshot = self.race_snapshot or fields
                    self.racing = False
                    position = self.to_int(snapshot.get("position"))
                    if position and position > 0:
                        race = {
                            "game": str(snapshot.get("game") or "Unknown sim"),
                            "track": str(snapshot.get("track") or "Unknown track"),
                            "carClass": str(snapshot.get("car") or "Unknown class"),
                            "position": position,
                            "incidents": max(0, self.to_int(snapshot.get("incidents")) or 0),
                            "championshipId": settings["championship_id"],
                        }
                        self.events.put(("capture", race))
                    else:
                        self.events.put(("log", "Race session ended, but no valid finish position was mapped; skipped upload."))
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
        self.stop_event.set()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    CompanionApp(window)
    window.mainloop()
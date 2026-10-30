# -*- coding: utf-8 -*-
"""
SwingDesk - local swing-trading dashboard.

Run:   python swingdesk.py            (opens http://127.0.0.1:8765 in your browser)
       python swingdesk.py --lan      (also reachable from your phone on the home Wi-Fi)
       python swingdesk.py --remote   (reachable from anywhere through a secure tunnel, password required)
       python swingdesk.py --demo     (synthetic data, no internet needed)
"""
import argparse
import atexit
import csv
import getpass
import shutil
import subprocess
import gzip
import hashlib
import hmac
import io
import json
import secrets
import ssl
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

import engine
import phone

VERSION = "1.5.0"

if getattr(sys, "frozen", False):
    RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    APP_DIR = os.path.dirname(sys.executable)
else:
    RES_DIR = APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(RES_DIR, "web")
DATA_DIR = os.path.join(APP_DIR, "data")
SCAN_DIR = os.path.join(DATA_DIR, "scans")


def configure_dirs(demo):
    """Demo mode keeps its own positions, journal and scans. DATA_DIR env (cloud volume) wins."""
    global DATA_DIR, SCAN_DIR
    base = os.environ.get("DATA_DIR")
    if base:
        DATA_DIR = os.path.join(base, "demo") if demo else base
    else:
        DATA_DIR = os.path.join(APP_DIR, "data_demo" if demo else "data")
    SCAN_DIR = os.path.join(DATA_DIR, "scans")


tls_context = phone.tls_context


# --------------------------------------------------------------------------- auth
class Auth:
    """Password from the environment (website mode, or --lan with SWINGDESK_PASSWORD)."""
    COOKIE = "sd_session"
    DAYS = 30

    def __init__(self, password):
        self.password = password or ""
        self.attempts = {}
        self.recent = []
        self.secret = b""
        self._raw = b""

    @property
    def enabled(self):
        return bool(self.password)

    def _material(self):
        return self.password.encode("utf-8")

    def load_secret(self):
        path = os.path.join(DATA_DIR, "secret.key")
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            os.makedirs(DATA_DIR, exist_ok=True)
            raw = secrets.token_bytes(32)
            with open(path, "wb") as f:
                f.write(raw)
        self._raw = raw
        self._derive()

    def _derive(self):
        # changing the password invalidates every existing session
        self.secret = hashlib.sha256(self._raw + self._material()).digest()

    def token(self):
        exp = int(time.time()) + self.DAYS * 86400
        sig = hmac.new(self.secret, f"v1|{exp}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{sig}"

    def valid(self, tok):
        try:
            exp, sig = tok.split(".", 1)
            good = hmac.new(self.secret, f"v1|{int(exp)}".encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(sig, good) and int(exp) > time.time()
        except Exception:
            return False

    def check_password(self, pw):
        return hmac.compare_digest(hashlib.sha256((pw or "").encode("utf-8")).digest(),
                                   hashlib.sha256(self.password.encode("utf-8")).digest())

    def allow_attempt(self, ip):
        """8 tries per address and 30 in total per 10 minutes (the total stops address-rotating guessers)."""
        now = time.time()
        recent = [t for t in self.attempts.get(ip, []) if now - t < 600]
        self.attempts[ip] = recent
        self.recent = [t for t in self.recent if now - t < 600]
        if len(recent) >= 8 or len(self.recent) >= 30:
            return False
        recent.append(now)
        self.recent.append(now)
        return True

    def push_ok(self, header):
        token = os.environ.get("PUSH_TOKEN") or self.password
        got = (header or "").replace("Bearer ", "", 1).strip()
        return bool(token) and hmac.compare_digest(hashlib.sha256(got.encode()).digest(),
                                                   hashlib.sha256(token.encode()).digest())


class RemoteAuth(Auth):
    """Password for access from outside (the tunnel). Stored as a PBKDF2 hash in data/remote_auth.json."""

    def __init__(self):
        super().__init__("")
        self.pwfile = phone.PasswordFile(os.path.join(DATA_DIR, "remote_auth.json"))

    @property
    def enabled(self):
        return self.pwfile.is_set

    def _material(self):
        return self.pwfile.material()

    def check_password(self, pw):
        return self.pwfile.check(pw or "")

    def set_password(self, pw):
        self.pwfile.set(pw)
        self._derive()

    def push_ok(self, header):
        return False


LOGIN_HTML = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SwingDesk: כניסה</title>
<style>
:root{--bg:#0F1C26;--panel:#142431;--line:#27404F;--text:#DCE6EC;--muted:#8DA3B0;--magenta:#E066D2;--red:#EE6A5C;--cyan:#57D2E6}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font-family:"Segoe UI",Arial,sans-serif}
main{min-height:100%;display:flex;align-items:center;justify-content:center;padding:16px}
form{width:100%;max-width:340px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:28px 24px}
h1{margin:0 0 4px;font-size:22px;font-family:Arial,sans-serif}p{margin:0 0 18px;color:var(--muted);font-size:14px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}
input{width:100%;padding:10px 12px;border-radius:4px;border:1px solid var(--line);background:var(--bg);color:var(--text);font-size:16px;direction:ltr}
input:focus{outline:2px solid var(--cyan);outline-offset:1px}
button{margin-top:14px;width:100%;padding:11px;border:0;border-radius:4px;background:var(--magenta);color:#1a0d18;font-weight:700;font-size:15px;cursor:pointer}
.err{color:var(--red);font-size:13px;min-height:18px;margin-top:10px}
</style></head><body><main><form id="f"><h1>SwingDesk</h1><p>הכנס את הסיסמה כדי לפתוח את הדשבורד.</p>
<label for="pw">סיסמה</label><input id="pw" type="password" autocomplete="current-password" autofocus required>
<button type="submit">כניסה</button><div class="err" id="err" role="alert"></div></form></main>
<script>
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const err=document.getElementById('err');err.textContent='';
try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('pw').value})});
if(r.ok){location.href='/';return}const t=await r.json().catch(()=>({}));
err.textContent=r.status===429?'יותר מדי ניסיונות. נסה שוב בעוד 10 דקות.':(t.error==='not_configured'?'השרת לא מוגדר: חסר APP_PASSWORD.':'סיסמה שגויה.')}
catch(x){err.textContent='אין חיבור לשרת.'}};
</script></body></html>"""

REMOTE_NOT_CONFIGURED_HTML = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SwingDesk</title>
<style>body{margin:0;background:#0F1C26;color:#DCE6EC;font-family:Arial,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px}
div{max-width:460px;background:#142431;border:1px solid #27404F;border-radius:8px;padding:24px;line-height:1.6}</style></head>
<body><div><h2 style="margin-top:0">הגישה מבחוץ נעולה</h2>עדיין לא נקבעה סיסמה לגישה מבחוץ. קובעים אותה במחשב שמריץ את SwingDesk:
הגדרות, בחלק "גישה לדשבורד מכל מקום".</div></body></html>"""

NOT_CONFIGURED_HTML = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><title>SwingDesk</title>
<style>body{margin:0;background:#0F1C26;color:#DCE6EC;font-family:Arial,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px}
div{max-width:460px;background:#142431;border:1px solid #27404F;border-radius:8px;padding:24px;line-height:1.6}code{color:#57D2E6}</style></head>
<body><div><h2 style="margin-top:0">חסרה סיסמה</h2>השרת רץ במצב ענן בלי סיסמה, ולכן הוא חסום. מגדירים משתנה סביבה <code>APP_PASSWORD</code>
(ב-Railway: Variables) והשרת עולה מחדש לבד.</div></body></html>"""


# Verified on 2026-10-28. Edit freely in the Events screen.
SEED_EVENTS = [
    {"date": "2026-10-28", "title": "FOMC - החלטת ריבית ומסיבת עיתונאים", "impact": "high"},
    {"date": "2026-10-29", "title": "Amazon מדווחת (Q3)", "impact": "high"},
    {"date": "2026-11-03", "title": "בחירות אמצע הקדנציה בארה\"ב", "impact": "high"},
    {"date": "2026-11-03", "title": "JOLTS (ספטמבר)", "impact": "low"},
    {"date": "2026-11-06", "title": "דוח תעסוקה - NFP (אוקטובר)", "impact": "high"},
    {"date": "2026-11-10", "title": "CPI (אוקטובר)", "impact": "high"},
    {"date": "2026-11-13", "title": "PPI (אוקטובר)", "impact": "medium"},
    {"date": "2026-11-25", "title": "GDP (הערכה שנייה) + PCE", "impact": "high"},
]

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ico": "image/x-icon", ".json": "application/json", ".woff2": "font/woff2"}
STRAT_HE = {"continuation": "המשך גאפ", "gapfill": "סגירת גאפ", "quality": "איכות בירידה"}
MASK = "••••"


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


# --------------------------------------------------------------------------- telegram
def tg_call(token, method, params=None, timeout=10):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout, context=tls_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def push_scan(settings, res):
    """Send a finished scan to the SwingDesk website (relay mode)."""
    url, token = (settings.get("cloud_url") or "").rstrip("/"), settings.get("cloud_token") or ""
    if not url or not token:
        return False, "missing cloud url or token"
    if not url.startswith("http"):
        url = "https://" + url
    body = gzip.compress(json.dumps(res, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(url + "/api/push-scan", data=body, method="POST", headers={
        "Content-Type": "application/json", "Content-Encoding": "gzip", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=60, context=tls_context()) as r:
            return r.status == 200, ""
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def tg_send(settings, text):
    token, chat = settings.get("tg_token"), settings.get("tg_chat")
    if not token or not chat:
        return False, "missing token or chat id"
    try:
        r = tg_call(token, "sendMessage", {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"})
        return bool(r.get("ok")), r.get("description", "")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- storage
class Store:
    def __init__(self):
        os.makedirs(SCAN_DIR, exist_ok=True)
        self.lock = threading.RLock()
        if not os.path.exists(self._p("events")):
            self.save("events", SEED_EVENTS)

    @staticmethod
    def _p(name):
        return os.path.join(DATA_DIR, f"{name}.json")

    def load(self, name, default):
        with self.lock:
            try:
                with open(self._p(name), encoding="utf-8") as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return default

    def save(self, name, value):
        with self.lock:
            tmp = self._p(name) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._p(name))

    def settings(self):
        s = dict(engine.DEFAULTS)
        s.update({k: v for k, v in self.load("settings", {}).items() if k in engine.DEFAULTS})
        return s

    def scan_path(self, sid):
        safe = "".join(ch for ch in sid if ch.isalnum() or ch in "_-")
        return os.path.join(SCAN_DIR, f"{safe}.json")

    def save_scan(self, res):
        with open(self.scan_path(res["id"]), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        index = [x for x in self.load("scan_index", []) if x["id"] != res["id"]]
        index.insert(0, {"id": res["id"], "last_bar": res["last_bar"], "demo": res["demo"],
                         "pulled_il": res.get("pulled_il") or res["pulled_utc"],
                         "candidates": sum(len(x) for x in res["candidates"].values())
                         if isinstance(res["candidates"], dict) else len(res["candidates"]),
                         "selected": res["selected"]})
        for old in index[25:]:
            try:
                os.remove(self.scan_path(old["id"]))
            except OSError:
                pass
        self.save("scan_index", index[:25])

    def load_scan(self, sid):
        try:
            with open(self.scan_path(sid), encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def delete_scan(self, sid):
        try:
            os.remove(self.scan_path(sid))
        except OSError:
            pass
        self.save("scan_index", [x for x in self.load("scan_index", []) if x["id"] != sid])


# --------------------------------------------------------------------------- app state
# ntfy look per alert type: (priority 1-5, emoji tags). 4 = high: sound + banner, 3 = default.
NTFY_STYLE = {
    "triggered": (4, ["chart_with_upwards_trend"]), "opp_trigger": (4, ["chart_with_upwards_trend"]),
    "sl_hit": (4, ["warning"]), "opp_broke": (4, ["warning"]), "cancel_open": (4, ["no_entry"]),
    "pre_above_cancel": (4, ["no_entry"]), "tp_hit": (4, ["white_check_mark"]), "time_exit": (4, ["alarm_clock"]),
    "scan": (3, ["bar_chart"]), "error": (3, ["x"]), "link": (3, ["link"]), "test": (3, ["bell"]),
}


class App:
    def __init__(self, demo, lan_url=None, cloud=False, auth=None):
        self.cloud = cloud
        self.auth = auth or Auth("")
        self.remote_auth = RemoteAuth()
        self.remote_auth.load_secret()
        self.remote_port = None
        self.notifier = phone.Notifier(log)
        self.tunnel = phone.Tunnel(os.path.join(DATA_DIR, "bin"), log, on_url=self._on_tunnel_url)
        self.store = Store()
        self.source = engine.DemoSource() if demo else engine.YahooSource()
        self.demo = demo
        self.lan_url = lan_url
        self.job = {"state": "idle", "pct": 0, "msg": "", "id": None, "error": None, "started": None}
        self.job_lock = threading.Lock()
        self.live_lock = threading.Lock()
        self.cancel_flag = False
        self.market_cache = (0, None)
        self.news_cache = {}
        self.live = {"updated": None, "state": "closed", "items": [], "error": None}
        self.force_live = True
        self.force_opps = True
        self._plans_cache = (None, [])
        self._opps_cache = (None, [])
        self.opp_quotes = {"updated": None, "quotes": {}, "error": None, "state": "closed"}
        self.last_sync = None
        self.awake_proc = None
        threading.Thread(target=self.monitor, daemon=True).start()

    # ---- scans
    def start_scan(self, overrides, auto=False):
        with self.job_lock:
            if self.job["state"] == "running":
                return False
            self.cancel_flag = False
            self.job = {"state": "running", "pct": 0, "msg": "start", "id": None, "error": None,
                        "started": time.time(), "auto": auto}
        settings = self.store.settings()
        settings.update({k: v for k, v in (overrides or {}).items() if k in engine.DEFAULTS})
        positions = self.store.load("positions", [])
        events = self.store.load("events", [])
        threading.Thread(target=self._run, args=(settings, positions, events), daemon=True).start()
        return True

    def _run(self, settings, positions, events):
        def progress(pct, msg):
            self.job.update(pct=pct, msg=msg)
        try:
            fallback = None
            idx = self.store.load("scan_index", [])
            if idx:
                prev = self.store.load_scan(idx[0]["id"]) or {}
                fallback = prev.get("universe_tickers")
            res = engine.run_scan(settings, positions, events, self.source, progress,
                                  cancel=lambda: self.cancel_flag, fallback_universe=fallback)
            self.store.save_scan(res)
            self.job.update(state="done", pct=100, msg="done", id=res["id"])
            n = sum(len(x) for x in res["candidates"].values())
            log(f"scan done: {res['id']} | candidates {n} | selected {res['selected']}")
            self.force_live = True
            self.force_opps = True
            self.notify(scan_summary(res, header=False), "scan", "scan", settings=settings, title=scan_title(res))
            if settings.get("cloud_url") and not self.demo and not self.cloud:
                ok, msg = push_scan(settings, res)
                log("pushed scan to website" if ok else f"push to website failed: {msg}")
        except engine.Cancelled:
            self.job.update(state="cancelled", msg="cancelled")
        except Exception as e:
            self.job.update(state="error", error=f"{type(e).__name__}: {e}")
            log(f"scan error: {type(e).__name__}: {e}")
            self.notify(f"הסריקה נכשלה ({type(e).__name__}). פרטים בחלון הטרמינל.", "alert", "error")

    # ---- phone: notifications and remote access
    def public_url(self):
        """Where a tap on a notification should lead: the live tunnel, the website, or the home Wi-Fi address."""
        if self.tunnel.state == "on" and self.tunnel.url:
            return self.tunnel.url
        if self.cloud:
            d = (os.environ.get("PUBLIC_URL") or os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip().rstrip("/")
            return (d if d.startswith("http") else "https://" + d) if d else None
        cu = (self.store.settings().get("cloud_url") or "").strip().rstrip("/")
        if cu:
            return cu if cu.startswith("http") else "https://" + cu
        return self.lan_url

    def click_url(self, kind, atype=None, ticker=None):
        base = self.public_url()
        if not base:
            return None
        if atype and atype.startswith("opp_") and ticker:
            return f"{base}/#opp/{ticker}"
        return f"{base}/#live" if kind == "alert" and atype not in ("error",) else f"{base}/#opps"

    def notify(self, text, kind="alert", atype=None, ticker=None, settings=None, title=None):
        """kind: alert | scan | link. Telegram and ntfy each follow their own on/off switches."""
        s = settings or self.store.settings()
        demo = " (דמו)" if self.demo else ""
        tg_flag = {"alert": "tg_alerts", "scan": "tg_on_scan"}.get(kind)
        if kind != "link" and s.get("tg_token") and s.get("tg_chat") and s.get(tg_flag):
            self.notifier.submit("telegram", tg_send, s, (title + "\n" + text) if title else f"SwingDesk{demo}: {text}")
        nt_flag = {"alert": "ntfy_alerts", "scan": "ntfy_on_scan"}.get(kind)
        if s.get("ntfy_topic") and (nt_flag is None or s.get(nt_flag)):
            pr, tags = NTFY_STYLE.get(atype or kind, (3, None))

            def send():
                # resolved at send time: right after a restart the tunnel needs a few seconds for its address
                t0 = time.time()
                while self.tunnel.want and self.tunnel.state in ("starting", "installing") and time.time() - t0 < 45:
                    time.sleep(1)
                return phone.ntfy_publish(s.get("ntfy_server"), s["ntfy_topic"], text,
                                          title=title or (f"SwingDesk{demo}" + (f" · {ticker}" if ticker else "")),
                                          priority=pr, tags=tags, click=self.click_url(kind, atype, ticker),
                                          token=s.get("ntfy_token"))
            self.notifier.submit("ntfy", send)

    def link_message(self, url):
        return (f"הדשבורד פתוח מהטלפון מכל מקום: {url}\n"
                "לחיצה על ההודעה פותחת אותו. הכתובת מתחלפת כש-SwingDesk מופעל מחדש, ואז תגיע הודעה חדשה.")

    def _on_tunnel_url(self, url):
        self.store.save("remote_state", {"url": url, "since": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print(f"  From anywhere (phone): {url}", flush=True)
        s = self.store.settings()
        if s.get("ntfy_topic"):
            self.notify(self.link_message(url), "link", "link", settings=s, title="SwingDesk זמין מכל מקום")

    def remote_start(self):
        if self.cloud:
            return False, "cloud"
        if not self.remote_auth.enabled:
            return False, "no_password"
        s = self.store.settings()
        if not s.get("remote_on"):
            s["remote_on"] = 1
            self.store.save("settings", s)
        self.tunnel.start(self.remote_port)
        return True, ""

    def remote_stop(self):
        s = self.store.settings()
        s["remote_on"] = 0
        self.store.save("settings", s)
        self.tunnel.stop()

    def remote_status(self):
        st = self.tunnel.status()
        st.update(has_password=self.remote_auth.enabled, cloud=self.cloud, enabled=bool(self.store.settings().get("remote_on")),
                  ntfy=self.notifier.status.get("ntfy"), telegram=self.notifier.status.get("telegram"),
                  public_url=self.public_url())
        if st["state"] == "on":
            st["error"] = None
        return st

    def market(self, force=False):
        ts, data = self.market_cache
        if data and not force and time.time() - ts < 90:
            return data
        data = engine.clean(engine.fetch_market(self.source))
        self.market_cache = (time.time(), data)
        return data

    def news(self, t):
        ts, items = self.news_cache.get(t, (0, None))
        if items is not None and time.time() - ts < 600:
            return items
        items = self.source.news(t)
        self.news_cache[t] = (time.time(), items)
        return items

    # ---- opportunities, live prices and sync
    def latest_scan_meta(self):
        idx = self.store.load("scan_index", [])
        return idx[0] if idx else None

    def top_opps(self):
        meta = self.latest_scan_meta()
        if not meta:
            return None, []
        if self._opps_cache[0] != meta["id"]:
            sc = self.store.load_scan(meta["id"]) or {}
            self._opps_cache = (meta["id"], {"rows": ((sc.get("opps") or {}).get("rows") or []),
                                             "cands": [r["ticker"] for v in (sc.get("candidates") or {}).values()
                                                       for r in (v if isinstance(v, list) else [])],
                                             "last_bar": sc.get("last_bar"), "partial": sc.get("partial"),
                                             "entry_date": sc.get("entry_date")})
        return meta["id"], self._opps_cache[1]

    def refresh_opps(self, n=60):
        sid, data = self.top_opps()
        if not data:
            return self.opp_quotes
        rows = data["rows"]
        tickers = list(dict.fromkeys([r["ticker"] for r in rows[:n]] + data["cands"][:30]))
        state = "open" if self.demo else engine.market_state()
        quotes, err = {}, None
        if tickers:
            try:
                quotes = self.source.live(tickers)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                log(f"opp quotes: {err}")
        self.opp_quotes = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "quotes": engine.clean(quotes),
                           "error": err, "state": state, "scan_id": sid}
        settings = self.store.settings()
        top = [r for r in rows if r.get("status") == "ready"][:int(settings.get("opp_alert_top") or 0)]
        ny = engine.now_tz("America/New_York")
        today = ny.date().isoformat() if ny else time.strftime("%Y-%m-%d")
        if top and (self.demo or (data.get("entry_date") or "") <= today):
            fresh = []
            for r in top:
                q = quotes.get(r["ticker"]) or {}
                if q.get("price") is None or (q.get("stale") and not self.demo):
                    continue
                p = r["plan"]
                if q.get("day_high") and q["day_high"] >= p["entry"]:
                    fresh.append(("opp_trigger", r, f"{r['ticker']}: הזדמנות הופעלה. הטריגר {p['entry']:.2f} נכנס "
                                                    f"(שיא יומי {q['day_high']:.2f}). סטופ {p['stop']:.2f}, "
                                                    f"T1 {p['t1']:.2f}" + (f", T2 {p['t2']:.2f}" if p.get('t2') else "") + "."))
                if q["price"] <= p["stop"]:
                    fresh.append(("opp_broke", r, f"{r['ticker']}: שברה את התמיכה. המחיר {q['price']:.2f} מתחת לסטופ "
                                                  f"{p['stop']:.2f}. ההזדמנות בוטלה."))
            if fresh:
                alerts = self.store.load("alerts", [])
                keys = {x["key"] for x in alerts}
                new = []
                for kind, r, text in fresh:
                    key = f"{today}:opp:{r['ticker']}:{kind}"
                    if key not in keys:
                        new.append({"id": uuid.uuid4().hex[:8], "key": key, "ticker": r["ticker"], "type": kind,
                                    "text": text, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
                if new:
                    self.store.save("alerts", (new + alerts)[:200])
                    for x in new:
                        log("alert: " + x["text"])
                        self.notify(x["text"], "alert", x["type"], x["ticker"], settings)
        return self.opp_quotes

    def sync_status(self):
        meta = self.latest_scan_meta()
        _, data = self.top_opps()
        expected = engine.latest_closed_session()
        expected = expected.isoformat() if expected else None
        last_bar = (data or {}).get("last_bar") if data else None
        stale = bool(not self.demo and (not last_bar or (expected and last_bar < expected)
                                         or ((data or {}).get("partial") and expected and last_bar <= expected)))
        return {"last_sync": self.last_sync, "live_updated": self.live.get("updated"),
                "opp_updated": self.opp_quotes.get("updated"), "market_state": "open" if self.demo else engine.market_state(),
                "last_bar": last_bar, "expected_session": expected, "stale": stale, "has_scan": bool(meta),
                "auto_sync": bool(self.store.settings().get("auto_sync")), "job": self.job}

    def sync(self, full=False):
        did = []
        try:
            self.market(force=True); did.append("market")
        except Exception as e:
            log(f"sync market: {e}")
        self.refresh_live(); did.append("live")
        self.refresh_opps(); did.append("opps")
        st = self.sync_status()
        started = False
        if full or st["stale"] or not st["has_scan"]:
            started = self.start_scan(None)
            if started:
                did.append("scan")
        self.last_sync = time.strftime("%Y-%m-%dT%H:%M:%S")
        st = self.sync_status()
        st.update(did=did, scan_started=started)
        return st

    # ---- live monitor
    def selected_plans(self):
        idx = self.store.load("scan_index", [])
        if not idx:
            return []
        sid = idx[0]["id"]
        if self._plans_cache[0] != sid:
            sc = self.store.load_scan(sid) or {}
            self._plans_cache = (sid, [p for p in sc.get("plans", []) if p.get("selected")])
        return self._plans_cache[1]

    def live_items(self):
        positions = self.store.load("positions", [])
        held = {p["ticker"] for p in positions}
        items = []
        for p in self.selected_plans():
            if p["ticker"] in held:
                continue
            items.append({"kind": "plan", "key": p["key"], "ticker": p["ticker"], "strategy": p.get("strategy"),
                          "entry": p["entry"], "sl": p["sl"], "tp": p["tp"], "qty": p["qty"],
                          "cancel_above": p["cancel_above"], "entry_date": p["entry_date"],
                          "time_exit": p["time_exit"]})
        for p in positions:
            items.append({"kind": "position", "key": "pos:" + p["id"], "ticker": p["ticker"],
                          "strategy": p.get("setup"), "entry": p["entry"], "sl": p["sl"], "tp": p.get("tp"),
                          "qty": p["qty"], "time_exit": p.get("time_exit"), "opened": p.get("opened")})
        shown = {i["ticker"] for i in items}
        for t in self.store.load("watchlist", []):
            if t not in shown:
                items.append({"kind": "watch", "key": "watch:" + t, "ticker": t})
        return items

    def refresh_live(self):
        with self.live_lock:
            ny = engine.now_tz("America/New_York")
            state = "open" if self.demo else engine.market_state(ny)
            items = self.live_items()
            tickers = sorted({i["ticker"] for i in items})
            quotes, err = {}, None
            if tickers:
                try:
                    quotes = self.source.live(tickers)
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    log(f"live: {err}")
            settings = self.store.settings()
            new_alerts = []
            today = (ny.date() if ny else time.localtime()).isoformat() if ny else time.strftime("%Y-%m-%d")
            for it in items:
                q = quotes.get(it["ticker"]) or {}
                it["q"] = engine.clean(q)
                new_alerts += self.evaluate(it, q, state, ny, today)
            self.live = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "state": state, "items": items, "error": err,
                         "ny_time": ny.isoformat() if ny else None}
            if new_alerts:
                alerts = self.store.load("alerts", [])
                keys = {a["key"] for a in alerts}
                fresh = [a for a in new_alerts if a["key"] not in keys]
                if fresh:
                    self.store.save("alerts", (fresh + alerts)[:200])
                    for a in fresh:
                        log("alert: " + a["text"])
                        self.notify(a["text"], "alert", a["type"], a["ticker"], settings)
            return self.live

    def evaluate(self, it, q, state, ny, today):
        out = []
        t, px = it["ticker"], q.get("price")
        if px is None or (q.get("stale") and not self.demo):
            it["status"] = "no_data"
            return out

        def alert(kind, text):
            out.append({"id": uuid.uuid4().hex[:8], "key": f"{today}:{it['key']}:{kind}", "ticker": t, "type": kind,
                        "text": text, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})

        if it["kind"] == "plan":
            active = self.demo or it["entry_date"] == today
            it["active"] = active
            if not active:
                it["status"] = "not_today" if it["entry_date"] > today else "expired"
                return out
            pre, op, hi = q.get("pre"), q.get("session_open"), q.get("day_high")
            it["status"] = "waiting"
            if state == "pre" and pre and pre > it["cancel_above"]:
                it["status"] = "pre_above"
                alert("pre_above_cancel", f"{t}: הפרה-מרקט ב-{pre:.2f}, מעל סף הביטול {it['cancel_above']:.2f}. "
                                          f"אם הפתיחה תישאר שם, לבטל את הפקודה.")
            if op and op > it["cancel_above"]:
                it["status"] = "cancel"
                alert("cancel_open", f"{t}: נפתחה ב-{op:.2f}, מעל סף הביטול {it['cancel_above']:.2f}. "
                                     f"לבטל את פקודת ה-Buy Stop.")
            elif hi and hi >= it["entry"]:
                it["status"] = "triggered"
                alert("triggered", f"{t}: הטריגר {it['entry']:.2f} נכנס (שיא יומי {hi:.2f}). לבדוק ביצוע ולרשום "
                                   f"פוזיציה. SL {it['sl']:.2f}, TP {it['tp']:.2f}.")
        elif it["kind"] == "position":
            it["status"] = "open"
            if px <= it["sl"]:
                it["status"] = "sl"
                alert("sl_hit", f"{t}: המחיר {px:.2f} ב-SL ({it['sl']:.2f}) או מתחתיו.")
            elif it.get("tp") and px >= it["tp"]:
                it["status"] = "tp"
                alert("tp_hit", f"{t}: המחיר {px:.2f} הגיע ל-TP ({it['tp']:.2f}).")
            if it.get("time_exit") and it["time_exit"] <= today:
                it["status"] = "time_exit" if it["status"] == "open" else it["status"]
                if state == "open" and ny and (ny.hour, ny.minute) >= (15, 30):
                    alert("time_exit", f"{t}: יום היציאה בזמן. לצאת במחיר שוק לפני הסגירה.")
        else:
            it["status"] = "watch"
        return out

    def keep_awake_check(self, settings):
        """macOS: hold a caffeinate assertion while SwingDesk runs, so the Mac does not fall asleep
        and the alerts and the remote link keep working. A closed laptop lid still sleeps."""
        if sys.platform != "darwin" or self.cloud:
            return
        alive = self.awake_proc is not None and self.awake_proc.poll() is None
        if settings.get("keep_awake") and not alive and shutil.which("caffeinate"):
            try:
                self.awake_proc = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())],
                                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                   stderr=subprocess.DEVNULL)
                log("keeping the Mac awake while SwingDesk runs (caffeinate)")
            except Exception as e:
                log(f"caffeinate: {e}")
                self.awake_proc = None
        elif not settings.get("keep_awake") and alive:
            self.awake_proc.terminate()
            self.awake_proc = None

    def monitor(self):
        last = last_opp = 0.0
        while True:
            try:
                self.auto_scan_check()
                self.keep_awake_check(self.store.settings())
                settings = self.store.settings()
                state = "open" if self.demo else engine.market_state()
                interval = max(20, int(settings.get("live_interval") or 60))
                period = interval if state in ("pre", "open", "post") else 900
                if self.force_live or time.time() - last >= period:
                    self.force_live = False
                    self.refresh_live()
                    last = time.time()
                opp_period = interval if (settings.get("auto_sync") and state in ("pre", "open", "post")) else 1800
                if self.force_opps or time.time() - last_opp >= opp_period:
                    self.force_opps = False
                    self.refresh_opps()
                    last_opp = time.time()
                    if settings.get("auto_sync"):
                        self.last_sync = time.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception as e:
                log(f"monitor: {type(e).__name__}: {e}")
            time.sleep(3)

    def auto_scan_check(self):
        s = self.store.settings()
        if not s.get("auto_scan") or self.demo:
            return
        il, ny = engine.now_tz("Asia/Jerusalem"), engine.now_tz("America/New_York")
        if not il or not ny:
            return
        try:
            hh, mm = (int(x) for x in str(s.get("auto_scan_time", "23:15")).split(":")[:2])
        except ValueError:
            return
        if (il.hour, il.minute) < (hh, mm):
            return
        hm = (ny.hour, ny.minute)
        if engine.is_trading_day(ny.date()) and hm >= (16, 5):
            session = ny.date()
        elif hm < (9, 0):
            session = np.busday_offset(np.datetime64(ny.date(), "D"), -1, roll="backward",
                                       holidays=engine.NYSE_HOLIDAYS).astype(object)
        else:
            return
        key = str(session)
        if self.store.load("auto_scan_last", {}).get("session") == key:
            return
        if self.start_scan(None, auto=True):
            self.store.save("auto_scan_last", {"session": key, "at": il.isoformat()})
            log(f"auto scan started for session {key}")


def scan_title(res):
    return f"SwingDesk: סריקה של {res['last_bar']}" + (" (דמו)" if res.get("demo") else "")


def scan_summary(res, header=True):
    sel = [p for p in res["plans"] if p.get("selected")]
    lines = ([scan_title(res)] if header else []) + [f"מועמדות: {sum(len(x) for x in res['candidates'].values())}"]
    if not sel:
        lines.append("אין עסקאות שעומדות בכל התנאים.")
    else:
        lines.append(f"התוכנית ל-{res['entry_date']}:")
        for p in sel:
            lines.append(f"{p['ticker']} ({STRAT_HE.get(p['strategy'], p['strategy'])}): Buy Stop {p['entry']:.2f}, "
                         f"SL {p['sl']:.2f}, TP {p['tp']:.2f}, כמות {p['qty']}, ביטול מעל {p['cancel_above']:.2f}, "
                         f"יציאה עד {p['time_exit']}")
    op = res.get("opps") or {}
    ready = [r for r in op.get("rows", []) if r.get("status") == "ready"][:5]
    if ready:
        lines.append(f"הזדמנויות מוכנות ({op['counts']['ready']}):")
        for r in ready:
            p = r["plan"]
            qty = (r.get("size") or {}).get("qty")
            lines.append(f"{r['ticker']}: מ-{r['high']:.2f} ל-{r['close']:.2f} ({r['dd_pct']:.0f}%). כניסה {p['entry']:.2f}, "
                         f"סטופ {p['stop']:.2f} (סיכון {r['risk_pct']:.1f}%), T1 {p['t1']:.2f}"
                         + (f", T2 {p['t2']:.2f}" if p.get("t2") else "") + (f", כמות {qty}" if qty else "")
                         + f", ציון {r['score']:.0f}")
    if res.get("events_window"):
        lines.append("אירועים בחלון: " + ", ".join(f"{e['date'][5:]} {e['title']}" for e in res["events_window"][:4]))
    return "\n".join(lines)


SECRET_KEYS = ("tg_token", "cloud_token", "ntfy_token")


def public_settings(s):
    s = dict(s)
    for k in SECRET_KEYS:
        if s.get(k):
            s[k] = MASK + s[k][-4:]
    return s


# --------------------------------------------------------------------------- http
def make_handler(app, remote=False):
    """remote=True: the listener behind the tunnel. Every request needs the remote password, always."""
    auth = app.remote_auth if remote else app.auth

    class H(BaseHTTPRequestHandler):
        server_version = "SwingDesk/" + VERSION

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False)
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code, msg):
            self._send(code, {"error": msg})

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))

        def _ip(self):
            cf = self.headers.get("Cf-Connecting-Ip") if remote else None
            fwd = self.headers.get("X-Forwarded-For")
            return cf or (fwd.split(",")[0].strip() if fwd else self.client_address[0])

        def _authed(self):
            if not auth.enabled:
                return not remote
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == Auth.COOKIE and auth.valid(v):
                    return True
            return False

        def _gate(self, p):
            """True when the request may continue. Cloud mode and the tunnel: password required for everything."""
            if p in ("/healthz", "/login", "/api/login") or (p == "/api/push-scan" and not remote):
                return True
            if remote and not auth.enabled:
                self._send(503, REMOTE_NOT_CONFIGURED_HTML, "text/html; charset=utf-8")
                return False
            if app.cloud and not auth.enabled:
                self._send(503, NOT_CONFIGURED_HTML, "text/html; charset=utf-8")
                return False
            if self._authed():
                return True
            if p.startswith("/api/"):
                self._err(401, "login required")
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return False

        def _cookie(self, value, max_age):
            secure = "; Secure" if (remote or app.cloud or self.headers.get("X-Forwarded-Proto") == "https") else ""
            return f"{Auth.COOKIE}={value}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Lax{secure}"

        def _static(self, rel):
            rel = rel.lstrip("/") or "index.html"
            path = os.path.normpath(os.path.join(WEB_DIR, rel))
            if not path.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(path):
                return self._err(404, "not found")
            with open(path, "rb") as f:
                self._send(200, f.read(), MIME.get(os.path.splitext(path)[1], "application/octet-stream"))

        def do_GET(self):
            try:
                u = urlparse(self.path)
                p, q = u.path, parse_qs(u.query)
                if p == "/healthz":
                    return self._send(200, "ok", "text/plain; charset=utf-8")
                if p == "/login":
                    if not auth.enabled:
                        return self._send(302, b"", "text/plain", {"Location": "/"})
                    return self._send(200, LOGIN_HTML, "text/html; charset=utf-8")
                if not self._gate(p):
                    return
                if not p.startswith("/api/"):
                    return self._static("index.html" if p == "/" else p)
                st = app.store
                if p == "/api/state":
                    s = st.settings()
                    return self._send(200, {
                        "version": VERSION, "demo": app.demo, "source": app.source.name, "lan_url": app.lan_url,
                        "settings": public_settings(s), "defaults": engine.DEFAULTS,
                        "tg_ready": bool(s.get("tg_token") and s.get("tg_chat")),
                        "positions": st.load("positions", []), "journal": st.load("journal", []),
                        "events": st.load("events", []), "scans": st.load("scan_index", []),
                        "watchlist": st.load("watchlist", []), "job": app.job,
                        "cloud": app.cloud, "auth": auth.enabled, "remote_session": remote,
                        "remote": app.remote_status(), "ntfy_ready": bool(s.get("ntfy_topic")),
                        "start_cmd": None if (remote or app.cloud) else start_command(app.demo),
                        "auto_scan_last": st.load("auto_scan_last", {})})
                if p == "/api/job":
                    return self._send(200, app.job)
                if p == "/api/remote":
                    return self._send(200, app.remote_status())
                if p == "/api/live":
                    return self._send(200, dict(app.live, alerts=st.load("alerts", [])[:60]))
                if p == "/api/sync":
                    rs = app.tunnel.status()
                    return self._send(200, dict(app.sync_status(), alerts=st.load("alerts", [])[:10],
                                                remote={"state": rs["state"], "url": rs["url"]}))
                if p == "/api/oppquotes":
                    return self._send(200, app.opp_quotes)
                if p == "/api/market":
                    return self._send(200, app.market(force="r" in q))
                if p == "/api/news":
                    t = (q.get("t") or [""])[0].upper().strip()
                    return self._send(200, app.news(t) if t else [])
                if p == "/api/chart":
                    t = (q.get("t") or [""])[0].upper().strip()
                    data = engine.chart_for(app.source, t) if t else None
                    return self._send(200, data) if data else self._err(404, "no data for " + t)
                if p == "/api/quotes":
                    tick = [t.strip().upper() for t in (q.get("t") or [""])[0].split(",") if t.strip()]
                    return self._send(200, app.source.quotes(tick) if tick else {})
                if p.startswith("/api/scans/"):
                    parts = p.split("/")
                    sid = parts[3]
                    res = st.load_scan(sid)
                    if res is None:
                        return self._err(404, "scan not found")
                    if len(parts) == 4:
                        return self._send(200, res)
                    if parts[4] == "report.md":
                        return self._send(200, engine.report_md(res), "text/markdown; charset=utf-8",
                                          {"Content-Disposition": f'attachment; filename="swingdesk_{sid}.md"'})
                    if parts[4] == "plans.csv":
                        buf = io.StringIO()
                        cols = ["ticker", "name", "strategy", "selected", "score", "entry", "sl", "tp", "qty",
                                "exposure", "risk_usd", "reward_usd", "cancel_above", "entry_date", "time_exit"]
                        w = csv.writer(buf)
                        w.writerow(cols + ["flags", "blocking"])
                        for pl in res["plans"]:
                            w.writerow([pl.get(c) for c in cols] +
                                       ["; ".join(engine.flag_text(f) for f in pl["flags"]),
                                        "; ".join(engine.flag_text(f) for f in pl["blocking"])])
                        return self._send(200, "﻿" + buf.getvalue(), "text/csv; charset=utf-8",
                                          {"Content-Disposition": f'attachment; filename="swingdesk_{sid}_plans.csv"'})
                return self._err(404, "unknown endpoint")
            except Exception as e:
                return self._err(500, f"{type(e).__name__}: {e}")

        def do_POST(self):
            try:
                p = urlparse(self.path).path
                st = app.store
                if p == "/api/login":
                    body = self._body()
                    if not auth.enabled:
                        return self._err(400, "not_configured")
                    if not auth.allow_attempt(self._ip()):
                        return self._err(429, "too_many")
                    if not auth.check_password(str(body.get("password", ""))):
                        time.sleep(0.8)
                        if remote:
                            log(f"remote login failed from {self._ip()}")
                        return self._err(401, "wrong")
                    if remote:
                        log(f"remote login from {self._ip()}")
                    return self._send(200, {"ok": True}, extra={"Set-Cookie": self._cookie(auth.token(), Auth.DAYS * 86400)})
                if p == "/api/push-scan" and not remote:
                    if not app.auth.push_ok(self.headers.get("Authorization")):
                        return self._err(401, "bad token")
                    n = int(self.headers.get("Content-Length") or 0)
                    if n <= 0 or n > 80_000_000:
                        return self._err(400, "bad size")
                    raw = self.rfile.read(n)
                    if self.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    res = json.loads(raw.decode("utf-8"))
                    if not all(k in res for k in ("id", "last_bar", "candidates", "plans")):
                        return self._err(400, "not a scan")
                    res["pushed"] = True
                    st.save_scan(res)
                    app.force_live = app.force_opps = True
                    log(f"scan {res['id']} received from home computer")
                    return self._send(200, {"ok": True, "id": res["id"]})
                if not self._gate(p):
                    return
                body = self._body()
                if p == "/api/logout":
                    return self._send(200, {"ok": True}, extra={"Set-Cookie": self._cookie("", 0)})
                if p == "/api/cloud/push":
                    idx = st.load("scan_index", [])
                    res = st.load_scan(idx[0]["id"]) if idx else None
                    if not res:
                        return self._err(404, "no scan yet")
                    ok, msg = push_scan(st.settings(), res)
                    return self._send(200 if ok else 400, {"ok": ok, "error": msg})
                if p == "/api/scan":
                    ok = app.start_scan(body.get("settings"))
                    return self._send(200 if ok else 409, app.job)
                if p == "/api/scan/cancel":
                    app.cancel_flag = True
                    return self._send(200, app.job)
                if p == "/api/sync":
                    return self._send(200, app.sync(full=bool(body.get("full"))))
                if p == "/api/live/refresh":
                    live = app.refresh_live()
                    return self._send(200, dict(live, alerts=st.load("alerts", [])[:60]))
                if p.startswith("/api/remote/") and remote:
                    return self._err(403, "local only")
                if p == "/api/remote/password":
                    pw = str(body.get("password", ""))
                    if len(pw) < 8:
                        return self._err(400, "short")
                    app.remote_auth.set_password(pw)
                    log("remote password set (existing remote sessions signed out)")
                    return self._send(200, app.remote_status())
                if p == "/api/remote/start":
                    ok, msg = app.remote_start()
                    return self._send(200 if ok else 400, dict(app.remote_status(), error=msg or None))
                if p == "/api/remote/stop":
                    app.remote_stop()
                    return self._send(200, app.remote_status())
                if p == "/api/remote/notify":
                    s = st.settings()
                    url = app.tunnel.url if app.tunnel.state == "on" else None
                    if not url:
                        return self._err(400, "remote access is off")
                    if not s.get("ntfy_topic"):
                        return self._err(400, "no ntfy topic")
                    ok, msg = phone.ntfy_publish(s.get("ntfy_server"), s["ntfy_topic"], app.link_message(url),
                                                 title="SwingDesk זמין מכל מקום", tags=["link"], click=url,
                                                 token=s.get("ntfy_token"))
                    return self._send(200 if ok else 400, {"ok": ok, "error": msg})
                if p == "/api/ntfy/topic":
                    s = st.settings()
                    s["ntfy_topic"] = phone.new_topic()
                    st.save("settings", s)
                    return self._send(200, {"ok": True, "topic": s["ntfy_topic"]})
                if p == "/api/ntfy/test":
                    s = st.settings()
                    if not s.get("ntfy_topic"):
                        return self._err(400, "no ntfy topic")
                    base = app.public_url()
                    ok, msg = phone.ntfy_publish(
                        s.get("ntfy_server"), s["ntfy_topic"],
                        "הודעת בדיקה. ההתראות של SwingDesk יגיעו לכאן, גם כשהטלפון מחוץ לבית."
                        + ("" if base else "\nכדי שלחיצה על הודעה תפתח את הדשבורד, מפעילים גישה מבחוץ בהגדרות."),
                        title="SwingDesk: בדיקה", tags=["bell"], click=(base + "/#opps") if base else None,
                        token=s.get("ntfy_token"))
                    return self._send(200 if ok else 400, {"ok": ok, "error": msg})
                if p == "/api/telegram/test":
                    ok, msg = tg_send(st.settings(), "SwingDesk: הודעת בדיקה. ההתראות יגיעו לכאן.")
                    return self._send(200 if ok else 400, {"ok": ok, "error": msg})
                if p == "/api/telegram/detect":
                    s = st.settings()
                    if not s.get("tg_token"):
                        return self._err(400, "missing token")
                    r = tg_call(s["tg_token"], "getUpdates")
                    chats = [(u.get("message") or u.get("channel_post") or {}).get("chat", {}) for u in r.get("result", [])]
                    chats = [c for c in chats if c.get("id")]
                    if not chats:
                        return self._err(404, "no messages yet")
                    s["tg_chat"] = str(chats[-1]["id"])
                    st.save("settings", s)
                    return self._send(200, {"ok": True, "chat": s["tg_chat"],
                                            "name": chats[-1].get("first_name") or chats[-1].get("title") or ""})
                if p == "/api/positions":
                    pos = st.load("positions", [])
                    item = _position_from(body)
                    item["id"] = uuid.uuid4().hex[:8]
                    item["status"] = "open"
                    pos.append(item)
                    st.save("positions", pos)
                    app.force_live = True
                    return self._send(200, item)
                if p.startswith("/api/positions/") and p.endswith("/close"):
                    pid = p.split("/")[3]
                    pos = st.load("positions", [])
                    item = next((x for x in pos if x["id"] == pid), None)
                    if not item:
                        return self._err(404, "position not found")
                    exit_px = float(body["exit"])
                    rps = item["entry"] - item["sl"]
                    j = dict(item)
                    j.update(exit=exit_px, closed=body.get("date") or time.strftime("%Y-%m-%d"),
                             pnl=round((exit_px - item["entry"]) * item["qty"], 2),
                             r=round((exit_px - item["entry"]) / rps, 3) if rps > 0 else None,
                             exit_reason=body.get("reason", ""), status="closed")
                    journal = st.load("journal", [])
                    journal.insert(0, j)
                    st.save("journal", journal)
                    st.save("positions", [x for x in pos if x["id"] != pid])
                    app.force_live = True
                    return self._send(200, j)
                return self._err(404, "unknown endpoint")
            except (KeyError, ValueError, TypeError) as e:
                return self._err(400, f"bad input: {e}")
            except Exception as e:
                return self._err(500, f"{type(e).__name__}: {e}")

        def do_PUT(self):
            try:
                p = urlparse(self.path).path
                if not self._gate(p):
                    return
                body = self._body()
                st = app.store
                if p == "/api/settings":
                    old = st.settings()
                    clean = {k: v for k, v in body.items() if k in engine.DEFAULTS}
                    clean.pop("remote_on", None)   # switched only through /api/remote/start|stop
                    for k in SECRET_KEYS:
                        if str(clean.get(k, "")).startswith(MASK):
                            clean[k] = old.get(k, "")
                    merged = dict(old)
                    merged.update(clean)
                    ns = engine.settings_ns(merged)
                    st.save("settings", {k: getattr(ns, k) for k in engine.DEFAULTS})
                    if "auto_sync" in clean:
                        app.force_opps = True
                    return self._send(200, public_settings(st.settings()))
                if p == "/api/events":
                    ev = [{"date": str(e["date"])[:10], "title": str(e.get("title", ""))[:120],
                           "impact": e.get("impact", "medium")} for e in body if e.get("date")]
                    ev.sort(key=lambda e: e["date"])
                    st.save("events", ev)
                    return self._send(200, ev)
                if p == "/api/watchlist":
                    wl = []
                    for t in body:
                        t = str(t).upper().strip().replace(".", "-")
                        if t and t not in wl and len(t) <= 10:
                            wl.append(t)
                    st.save("watchlist", wl[:40])
                    app.force_live = True
                    return self._send(200, wl[:40])
                if p.startswith("/api/positions/"):
                    pid = p.split("/")[3]
                    pos = st.load("positions", [])
                    for i, x in enumerate(pos):
                        if x["id"] == pid:
                            upd = _position_from({**x, **body})
                            upd.update(id=pid, status="open")
                            pos[i] = upd
                            st.save("positions", pos)
                            app.force_live = True
                            return self._send(200, upd)
                    return self._err(404, "position not found")
                return self._err(404, "unknown endpoint")
            except (KeyError, ValueError, TypeError) as e:
                return self._err(400, f"bad input: {e}")
            except Exception as e:
                return self._err(500, f"{type(e).__name__}: {e}")

        def do_DELETE(self):
            p = urlparse(self.path).path
            if not self._gate(p):
                return
            st = app.store
            parts = p.split("/")
            if p.startswith("/api/positions/"):
                st.save("positions", [x for x in st.load("positions", []) if x["id"] != parts[3]])
                app.force_live = True
                return self._send(200, {"ok": True})
            if p.startswith("/api/journal/"):
                st.save("journal", [x for x in st.load("journal", []) if x["id"] != parts[3]])
                return self._send(200, {"ok": True})
            if p.startswith("/api/scans/"):
                st.delete_scan(parts[3])
                return self._send(200, {"ok": True})
            if p == "/api/alerts":
                st.save("alerts", [])
                return self._send(200, {"ok": True})
            return self._err(404, "unknown endpoint")

    return H


def _position_from(b):
    entry, sl, qty = float(b["entry"]), float(b["sl"]), int(float(b["qty"]))
    if qty <= 0 or entry <= 0:
        raise ValueError("entry and qty must be positive")
    tp = float(b["tp"]) if b.get("tp") not in (None, "") else None
    return {"ticker": str(b["ticker"]).upper().strip(), "entry": entry, "sl": sl, "tp": tp, "qty": qty,
            "opened": str(b.get("opened") or time.strftime("%Y-%m-%d"))[:10],
            "time_exit": str(b.get("time_exit") or "")[:10], "setup": b.get("setup", ""),
            "notes": str(b.get("notes", ""))[:500], "scan_id": b.get("scan_id", "")}


def start_command(demo=False):
    """The exact Terminal line that starts this copy with remote access and without the Mac falling asleep."""
    import shlex
    home = os.path.expanduser("~")
    d = APP_DIR
    if d.startswith(home + os.sep) and shlex.quote(d[len(home) + 1:]) == d[len(home) + 1:]:
        d = "~/" + d[len(home) + 1:]
    else:
        d = shlex.quote(d)
    flags = " --demo" if demo else ""
    if os.name == "nt":
        return f'cd /d "{APP_DIR}" && .venv\\Scripts\\python swingdesk.py{flags}'
    py = ".venv/bin/python" if os.path.exists(os.path.join(APP_DIR, ".venv", "bin", "python")) else "python3"
    return f"cd {d} && SSL_CERT_FILE=$({py} -m certifi) {py} swingdesk.py{flags}"


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="SwingDesk local dashboard")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lan", action="store_true", help="listen on the home network (phone access)")
    ap.add_argument("--demo", action="store_true", help="synthetic data, no internet")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--cloud", action="store_true", help="website mode: 0.0.0.0:$PORT, password required")
    ap.add_argument("--remote", action="store_true", help="access from anywhere through a Cloudflare tunnel")
    ap.add_argument("--set-remote-password", action="store_true", help="choose a new password for remote access")
    a = ap.parse_args()
    cloud = a.cloud or os.environ.get("SWINGDESK_CLOUD") == "1"
    if cloud:
        a.no_browser = True
        a.port = int(os.environ.get("PORT") or 8080)

    try:  # macOS allows only 256 open files per process by default
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = 8192 if hard == resource.RLIM_INFINITY else min(8192, hard)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except Exception:
        pass
    if not a.demo and engine.yf is None:
        print("yfinance is missing. Run:  pip install -U yfinance pandas numpy tzdata")
        input("Press Enter to exit")
        sys.exit(1)

    configure_dirs(a.demo)
    os.makedirs(DATA_DIR, exist_ok=True)
    password = os.environ.get("APP_PASSWORD", "")
    if not password and not cloud and a.lan:
        password = os.environ.get("SWINGDESK_PASSWORD", "")
    auth = Auth(password)
    auth.load_secret()
    if cloud and not os.path.exists(os.path.join(DATA_DIR, "settings.json")):
        first = dict(engine.DEFAULTS)
        first.update(auto_scan=1, auto_sync=1)
        with open(os.path.join(DATA_DIR, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(first, f, ensure_ascii=False, indent=1)
    host = "0.0.0.0" if (a.lan or cloud) else "127.0.0.1"
    server, port = None, a.port
    for port in range(a.port, a.port + (1 if cloud else 20)):
        try:
            server = ThreadingHTTPServer((host, port), None)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("No free port found.")
    lan_url = f"http://{lan_ip()}:{port}" if a.lan and lan_ip() else None
    app = App(a.demo, lan_url, cloud=cloud, auth=auth)
    server.RequestHandlerClass = make_handler(app)
    url = f"http://127.0.0.1:{port}"
    want_remote = False
    if not cloud:
        # the tunnel's own listener: localhost only, every request needs the remote password
        rserver = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, remote=True))
        app.remote_port = rserver.server_address[1]
        threading.Thread(target=rserver.serve_forever, daemon=True, name="remote-http").start()
        want_remote = a.remote or bool(app.store.settings().get("remote_on"))
        if (a.set_remote_password or (want_remote and not app.remote_auth.enabled)) and sys.stdin.isatty():
            print("Remote access needs a password (8+ characters). You will type it on the phone once.")
            for _ in range(3):
                pw = getpass.getpass("  New remote password: ")
                if len(pw) < 8:
                    print("  Too short.")
                    continue
                if getpass.getpass("  Again: ") != pw:
                    print("  Not the same.")
                    continue
                app.remote_auth.set_password(pw)
                print("  Saved.")
                break
        atexit.register(app.tunnel.stop)
        app.keep_awake_check(app.store.settings())
    print(f"SwingDesk {VERSION} {'(DEMO DATA)' if a.demo else '(live data: Yahoo Finance)'}"
          f"{' - website mode on port ' + str(port) if cloud else ''}")
    if cloud and not auth.enabled:
        print("  APP_PASSWORD is not set: the website stays locked until you set it.")
    print(f"  Dashboard: {url}")
    if lan_url:
        print(f"  Phone (same Wi-Fi): {lan_url}")
    print(f"  Data folder: {DATA_DIR}")
    if want_remote:
        ok, why = app.remote_start()
        if ok:
            print("  From anywhere: starting a secure tunnel. The address appears here and on the phone (ntfy).")
        else:
            print("  From anywhere: off. Set a remote password in Settings, then switch it on there.")
    print("  Keep this window open: it runs the scans, the live monitor and the alerts.")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    import signal
    for sig in ("SIGTERM", "SIGHUP"):   # closing the Terminal window or `kill`: still close the tunnel
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.tunnel.stop()


if __name__ == "__main__":
    main()

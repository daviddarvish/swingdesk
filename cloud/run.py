# -*- coding: utf-8 -*-
"""
SwingDesk in the cloud (GitHub Actions). The computer at home is not needed.

  python cloud/run.py scan     full scan after the US close: plan, opportunities, summary to the phone
  python cloud/run.py watch    price check during the session: triggers, cancels, broken supports -> phone
  add --demo for synthetic data (local testing without internet)

Everything stored in the public repository is AES-256-GCM encrypted with a key derived from the
DASH_PASSWORD secret: the run state (state.enc on the `data` branch) and the phone dashboard data
(site/data.bin on GitHub Pages). The phone derives the same key from the same password.

Environment: DASH_PASSWORD (required, 10+ chars), NTFY_TOPIC, NTFY_SERVER, NTFY_TOKEN, TG_TOKEN, TG_CHAT,
SETTINGS_JSON (optional overrides, e.g. {"opp_min_drop": 20}), PAGES_URL, GITHUB_REPOSITORY.
"""
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

STATE_DIR = os.path.join(ROOT, "_state")
SITE_DIR = os.path.join(ROOT, "site")
STATE_FILE = os.path.join(ROOT, "state.enc")
ITER = 310_000
MAGIC = b"SDK1"
KEEP_SCANS = 3


def say(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def notice(title, msg, level="notice"):
    """Shows up on the run page in GitHub (annotations), so a failure is readable without opening logs."""
    msg = str(msg).replace("\n", " ").replace("\r", " ")[:900]
    print(f"::{level} title={title}::{msg}", flush=True)


def out(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    say(f"output {name}={value}")


# --------------------------------------------------------------------------- crypto
def salt_for_repo():
    repo = os.environ.get("GITHUB_REPOSITORY") or "local/swingdesk"
    return hashlib.sha256(("swingdesk|" + repo.lower()).encode("utf-8")).digest()[:16]


def derive_key(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITER, dklen=32)


def encrypt(key, data):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, None)


def decrypt(key, blob):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if blob[:4] != MAGIC:
        raise ValueError("not a SwingDesk file")
    return AESGCM(key).decrypt(blob[4:16], blob[16:], None)


# --------------------------------------------------------------------------- state
def restore_state(key):
    """Returns True when an earlier state was restored."""
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    os.makedirs(STATE_DIR)
    if not os.path.exists(STATE_FILE) or os.path.getsize(STATE_FILE) == 0:
        say("no saved state: first run")
        return False
    try:
        raw = decrypt(key, open(STATE_FILE, "rb").read())
    except Exception as e:
        say(f"saved state could not be decrypted ({type(e).__name__}); the password changed? starting fresh")
        return False
    base = os.path.realpath(STATE_DIR)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for m in tf.getmembers():
            target = os.path.realpath(os.path.join(STATE_DIR, m.name))
            if not (m.isfile() or m.isdir()) or not target.startswith(base + os.sep):
                continue
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    say("state restored")
    return True


def save_state(key):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for dirpath, dirnames, filenames in os.walk(STATE_DIR):
            dirnames[:] = [d for d in dirnames if d != "bin"]
            for fn in filenames:
                if fn in ("secret.key", "remote_auth.json") or fn.endswith(".tmp"):
                    continue
                full = os.path.join(dirpath, fn)
                tf.add(full, arcname=os.path.relpath(full, STATE_DIR))
    with open(STATE_FILE, "wb") as f:
        f.write(encrypt(key, buf.getvalue()))
    say(f"state saved ({os.path.getsize(STATE_FILE) // 1024} KB)")


# --------------------------------------------------------------------------- app
def make_app(demo):
    os.environ["DATA_DIR"] = STATE_DIR
    import engine
    import swingdesk as sd
    sd.configure_dirs(demo)
    os.makedirs(sd.SCAN_DIR, exist_ok=True)
    path = os.path.join(sd.DATA_DIR, "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        s = {}
    merged = dict(engine.DEFAULTS)
    merged.update({k: v for k, v in s.items() if k in engine.DEFAULTS})
    try:
        extra = json.loads(os.environ.get("SETTINGS_JSON") or "{}")
        merged.update({k: v for k, v in extra.items() if k in engine.DEFAULTS})
    except json.JSONDecodeError:
        say("SETTINGS_JSON is not valid JSON: ignored")
    env = os.environ.get
    merged.update(ntfy_topic=(env("NTFY_TOPIC") or "").strip(), ntfy_server=(env("NTFY_SERVER") or "").strip() or "https://ntfy.sh",
                  ntfy_token=(env("NTFY_TOKEN") or "").strip(), tg_token=(env("TG_TOKEN") or "").strip(),
                  tg_chat=(env("TG_CHAT") or "").strip(), remote_on=0, keep_awake=0, auto_scan=0, cloud_url="")
    ns = engine.settings_ns(merged)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: getattr(ns, k) for k in engine.DEFAULTS}, f, ensure_ascii=False, indent=1)

    pages = (env("PAGES_URL") or "").strip().rstrip("/") or None
    repo = (env("GITHUB_REPOSITORY") or "").strip()
    if not pages and "/" in repo:   # the default GitHub Pages address of this repository
        owner, name = repo.split("/", 1)
        pages = f"https://{owner.lower()}.github.io/{name}"

    class CloudApp(sd.App):
        def monitor(self):          # no background loop: one pass per workflow run
            return

        def public_url(self):
            return pages

    return CloudApp(demo), sd, engine


def prune_scans(app):
    for old in app.store.load("scan_index", [])[KEEP_SCANS:]:
        app.store.delete_scan(old["id"])


def do_scan(app):
    settings = app.store.settings()
    app.job = {"state": "running", "pct": 0, "msg": "start", "id": None, "error": None, "started": time.time()}
    done = threading.Event()

    def ticker():
        while not done.wait(30):
            say(f"scan {app.job.get('pct', 0)}% {app.job.get('msg', '')}")
    threading.Thread(target=ticker, daemon=True).start()
    t0 = time.time()
    app._run(settings, app.store.load("positions", []), app.store.load("events", []))
    done.set()
    say(f"scan {app.job['state']} in {time.time() - t0:.0f}s" + (f": {app.job.get('error')}" if app.job.get("error") else ""))
    if app.job["state"] != "done":
        notice("Scan failed", app.job.get("error") or app.job["state"], "error")
        return False
    sc = app.store.load_scan(app.job["id"]) or {}
    oc = ((sc.get("opps") or {}).get("counts") or {})
    notice("Scan", f"done in {time.time() - t0:.0f}s. bar {sc.get('last_bar')}, universe {sc.get('universe_n')} "
                   f"(usable {sc.get('usable_n')}), candidates {sum(len(x) for x in (sc.get('candidates') or {}).values())}, "
                   f"selected {sc.get('selected')}, opportunities ready {oc.get('ready')} / watch {oc.get('watch')} / risky {oc.get('risky')}")
    prune_scans(app)
    try:
        app.market(force=True)
    except Exception as e:
        say(f"market: {e}")
    app.refresh_live()
    app.refresh_opps()
    return True


def do_watch(app, engine, force=False):
    if not app.latest_scan_meta():
        say("no scan yet: running a full scan first")
        return do_scan(app)
    state = "open" if app.demo else engine.market_state()
    if state == "closed" and not force:
        say("US market closed (weekend, holiday or outside 04:00-20:00 New York): nothing to check")
        return False
    try:
        app.market(force=True)
    except Exception as e:
        say(f"market: {e}")
    app.refresh_live()
    app.refresh_opps()
    msg = (f"checked {len(app.opp_quotes.get('quotes') or {})} opportunity quotes and {len(app.live.get('items') or [])} plans; "
           f"market {state}" + (f"; quote error: {app.opp_quotes.get('error')}" if app.opp_quotes.get("error") else ""))
    say(msg)
    notice("Watch", msg, "warning" if app.opp_quotes.get("error") else "notice")
    return True


# --------------------------------------------------------------------------- phone dashboard
def build_site(app, sd, engine, key, salt, mode):
    st = app.store
    s = st.settings()
    meta = app.latest_scan_meta()
    scan = st.load_scan(meta["id"]) if meta else None
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    state = {
        "version": sd.VERSION, "demo": app.demo, "source": "Yahoo Finance · ענן", "lan_url": None,
        "settings": dict(sd.public_settings(s), live_interval=900, auto_sync=0), "defaults": engine.DEFAULTS,
        "tg_ready": bool(s.get("tg_token") and s.get("tg_chat")), "positions": st.load("positions", []), "journal": [],
        "events": st.load("events", []), "scans": [meta] if meta else [], "watchlist": st.load("watchlist", []),
        "job": {"state": "idle", "pct": 0, "msg": ""}, "cloud": True, "auth": True, "remote_session": False,
        "remote": {"state": "off", "cloud": True}, "ntfy_ready": bool(s.get("ntfy_topic")), "start_cmd": None,
        "static": True, "built": now, "mode": mode, "auto_scan_last": {},
        "repo": os.environ.get("GITHUB_REPOSITORY") or "",
    }
    sync = app.sync_status()
    sync.update(last_sync=now, auto_sync=False, job={"state": "idle"})
    bundle = {"v": 1, "built": now, "state": state, "scan": scan, "sync": sync,
              "alerts": st.load("alerts", [])[:60], "opp_quotes": app.opp_quotes,
              "market": app.market_cache[1] or (scan or {}).get("market"),
              "live": app.live}
    raw = gzip.compress(json.dumps(engine.clean(bundle), ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)

    shutil.rmtree(SITE_DIR, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "web"), SITE_DIR)
    for fn in ("cloud.js", "icon-180.png", "icon-512.png", "manifest.webmanifest"):
        src = os.path.join(HERE, fn)
        if os.path.exists(src):
            shutil.copy(src, SITE_DIR)
    idx = os.path.join(SITE_DIR, "index.html")
    html = open(idx, encoding="utf-8").read()
    for asset in ("app.css", "lightweight-charts.js", "app.js", "opps.js"):
        html = html.replace(f'"/{asset}"', f'"{asset}"')
    html = html.replace('<script src="opps.js"></script>', '<script src="opps.js"></script>\n<script src="cloud.js"></script>')
    html = html.replace('<title>SwingDesk</title>', '<title>SwingDesk</title>\n<meta name="robots" content="noindex,nofollow">\n'
                        '<link rel="manifest" href="manifest.webmanifest">\n<link rel="apple-touch-icon" href="icon-180.png">\n'
                        '<meta name="apple-mobile-web-app-capable" content="yes">\n<meta name="mobile-web-app-capable" content="yes">\n'
                        '<meta name="apple-mobile-web-app-title" content="SwingDesk">\n<meta name="theme-color" content="#142431">')
    open(idx, "w", encoding="utf-8").write(html)
    with open(os.path.join(SITE_DIR, "data.bin"), "wb") as f:
        f.write(encrypt(key, raw))
    with open(os.path.join(SITE_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"v": 1, "iter": ITER, "salt": salt.hex(), "updated": now, "ts": int(time.time())}, f)
    open(os.path.join(SITE_DIR, ".nojekyll"), "w").close()
    open(os.path.join(SITE_DIR, "robots.txt"), "w").write("User-agent: *\nDisallow: /\n")
    say(f"site built: data {len(raw) // 1024} KB compressed, scan {meta['id'] if meta else 'none'}")


# --------------------------------------------------------------------------- main
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = (args[0] if args else "watch").lower()
    demo = "--demo" in sys.argv or os.environ.get("SWINGDESK_DEMO") == "1"
    force = "--force" in sys.argv
    pw = os.environ.get("DASH_PASSWORD") or ""
    if len(pw) < 10:
        print("::error::The DASH_PASSWORD secret is missing or shorter than 10 characters. "
              "Set it in the repository: Settings > Secrets and variables > Actions.")
        sys.exit(1)
    if not (os.environ.get("NTFY_TOPIC") or "").strip():
        print("::warning::NTFY_TOPIC secret is not set: no phone notifications will be sent.")
    salt = salt_for_repo()
    key = derive_key(pw, salt)
    had_state = restore_state(key)
    app, sd, engine = make_app(demo)
    changed = False
    if mode == "status":   # report only: no notifications, nothing saved or published
        meta = app.latest_scan_meta()
        sc = app.store.load_scan(meta["id"]) if meta else None
        if not sc:
            notice("Status", "no scan saved yet", "warning")
        else:
            oc = ((sc.get("opps") or {}).get("counts") or {})
            notice("Status", f"latest scan {sc['id']} (bar {sc.get('last_bar')}, took {sc.get('runtime_s')}s): universe "
                             f"{sc.get('universe_n')} from {sc.get('universe_source')}, usable {sc.get('usable_n')}, candidates "
                             f"{sum(len(x) for x in (sc.get('candidates') or {}).values())}, selected {len(sc.get('selected') or [])}, "
                             f"opportunities {(sc.get('opps') or {}).get('total')}: ready {oc.get('ready')} / watch {oc.get('watch')} / "
                             f"risky {oc.get('risky')}. alerts stored {len(app.store.load('alerts', []))}")
            for line in (sc.get("log") or [])[-6:]:
                notice("Scan log", line)
        out("save", "false")
        out("deploy", "false")
        return
    try:
        if not had_state and app.store.settings().get("ntfy_topic"):
            app.notify("SwingDesk רץ עכשיו בענן, בלי המחשב בבית. סריקה מלאה כל ערב אחרי סגירת השוק, ובדיקת מחירים כל רבע שעה "
                       "בזמן מסחר. לחיצה על ההודעה פותחת את הדשבורד.", "link", "link", title="SwingDesk בענן פעיל")
        if mode == "scan":
            changed = do_scan(app)
        elif mode == "watch":
            changed = do_watch(app, engine, force)
        elif mode == "site":
            changed = True
        else:
            print(f"::error::unknown mode {mode}")
            sys.exit(2)
    finally:
        if not app.notifier.flush(240):
            say("some notifications were still pending at exit")
    for ch, stt in app.notifier.status.items():
        say(f"{ch}: last send {'ok' if stt['ok'] else 'FAILED ' + str(stt['error'])}")
        if not stt["ok"]:
            notice(f"{ch} failed", stt["error"], "warning")
    if changed or not had_state:
        save_state(key)
        out("save", "true")
    else:
        out("save", "false")
    if changed or not had_state:
        build_site(app, sd, engine, key, salt, mode)
        out("deploy", "true")
    else:
        out("deploy", "false")


if __name__ == "__main__":
    main()

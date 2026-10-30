# -*- coding: utf-8 -*-
"""
Phone features for SwingDesk.

ntfy      push notifications to the ntfy app (iOS/Android). No account, works anywhere the phone has internet.
Tunnel    a Cloudflare quick tunnel: an https address on the internet that leads to this computer,
          without an account and without opening router ports. The address changes on every restart,
          so the app pushes the new one to the phone.
Password  PBKDF2 hash on disk for the remote login. The password itself is never stored.
"""
import collections
import hashlib
import hmac
import json
import os
import platform
import queue
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request


def tls_context():
    """certifi when available: python.org builds on macOS ship without system certificates."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fit_bytes(text, limit):
    raw = (text or "").encode("utf-8")
    if len(raw) <= limit:
        return text or ""
    return raw[:limit - 3].decode("utf-8", "ignore") + "…"


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------- ntfy
NTFY_DEFAULT = "https://ntfy.sh"
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOPIC_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alikes: l/1, o/0, i


def new_topic():
    """swingdesk-k7m2-p9qa-x3fh: 31^12 combinations. On ntfy.sh the topic name is the only secret."""
    parts = ["".join(secrets.choice(_TOPIC_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "swingdesk-" + "-".join(parts)


def ntfy_server(url):
    url = (url or NTFY_DEFAULT).strip().rstrip("/")
    return url if url.startswith(("http://", "https://")) else "https://" + url


def ntfy_publish(server, topic, message, title=None, priority=3, tags=None, click=None, actions=None,
                 token=None, timeout=12):
    """JSON publish (UTF-8 safe, unlike headers). Returns (ok, error_text)."""
    topic = (topic or "").strip()
    if not TOPIC_RE.match(topic):
        return False, "bad topic"
    body = {"topic": topic, "message": fit_bytes(message, 3900)}
    if title:
        body["title"] = fit_bytes(title, 200)
    if priority and int(priority) != 3:
        body["priority"] = int(priority)
    if tags:
        body["tags"] = list(tags)
    if click:
        body["click"] = click
    if actions:
        body["actions"] = actions
    headers = {"Content-Type": "application/json", "User-Agent": "SwingDesk"}
    if token:
        headers["Authorization"] = "Bearer " + token.strip()
    req = urllib.request.Request(ntfy_server(server) + "/", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=tls_context()) as r:
            return 200 <= r.status < 300, ""
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
        except Exception:
            detail = ""
        return False, f"HTTP {e.code}" + (f": {detail}" if detail else "")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


class Notifier:
    """One background sender: a slow or offline network never stalls the live monitor, order is kept,
    and a failed send is retried twice (15s, 60s) before it is dropped."""
    RETRY = (15, 60)

    def __init__(self, log):
        self.log = log
        self.q = queue.Queue(maxsize=300)
        self.status = {}
        threading.Thread(target=self._run, daemon=True, name="notify").start()

    def submit(self, channel, fn, *a, **k):
        try:
            self.q.put_nowait((channel, fn, a, k))
        except queue.Full:
            self.log(f"{channel}: queue full, message dropped")

    def _run(self):
        while True:
            channel, fn, a, k = self.q.get()
            ok, msg = False, ""
            for attempt, wait in enumerate((0,) + self.RETRY):
                if wait:
                    time.sleep(wait)
                try:
                    ok, msg = fn(*a, **k)
                except Exception as e:
                    ok, msg = False, f"{type(e).__name__}: {e}"
                # 4xx (bad topic, auth) will not heal by retrying; 429 might
                if ok or (msg.startswith("HTTP 4") and not msg.startswith("HTTP 429")):
                    break
            self.status[channel] = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok, "error": msg or None}
            if not ok:
                self.log(f"{channel}: send failed: {msg}")
            self.q.task_done()

    def flush(self, timeout=240):
        """Wait until every queued message was sent or given up (used by short-lived cloud runs)."""
        end = time.time() + timeout
        while self.q.unfinished_tasks and time.time() < end:
            time.sleep(0.5)
        return not self.q.unfinished_tasks


# --------------------------------------------------------------------------- remote password
class PasswordFile:
    ITER = 240_000

    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    @property
    def is_set(self):
        return bool(self.data.get("hash"))

    def _hash(self, pw, salt, it):
        return hashlib.pbkdf2_hmac("sha256", (pw or "").encode("utf-8"), salt, it)

    def set(self, pw):
        salt = secrets.token_bytes(16)
        self.data = {"v": 1, "iter": self.ITER, "salt": salt.hex(), "hash": self._hash(pw, salt, self.ITER).hex(),
                     "set": time.strftime("%Y-%m-%dT%H:%M:%S")}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def check(self, pw):
        if not self.is_set:
            return False
        got = self._hash(pw, bytes.fromhex(self.data["salt"]), int(self.data.get("iter", self.ITER)))
        return hmac.compare_digest(got, bytes.fromhex(self.data["hash"]))

    def material(self):
        return bytes.fromhex(self.data.get("hash", ""))


# --------------------------------------------------------------------------- tunnel
class Tunnel:
    """Runs `cloudflared tunnel --url http://127.0.0.1:<port>` and keeps it alive.

    state: off | installing | starting | on | error
    The quick-tunnel address is read from cloudflared's log. Health comes from cloudflared's own
    /ready endpoint on a private metrics port: 10 straight minutes without an edge connection,
    or 3 minutes without an address, restart the process (which yields a new address)."""
    URL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com")
    BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"

    def __init__(self, bin_dir, log, on_url=None):
        self.bin_dir, self.log, self.on_url = bin_dir, log, on_url
        self.lock = threading.Lock()
        self.want = False
        self.proc = None
        self.port = None
        self.thread = None
        self.state, self.url, self.error, self.since = "off", None, None, None
        self.install_pct = None
        self.metrics_port = None
        self.tail = collections.deque(maxlen=40)
        self._announced = None
        self._wake = threading.Event()

    # ---- binary
    def exe_name(self):
        return "cloudflared.exe" if os.name == "nt" else "cloudflared"

    def binary(self):
        local = os.path.join(self.bin_dir, self.exe_name())
        if os.path.isfile(local) and os.access(local, os.X_OK):
            return local
        return shutil.which("cloudflared")

    @staticmethod
    def asset():
        sysname, mach = platform.system(), platform.machine().lower()
        arm = mach in ("arm64", "aarch64")
        if sysname == "Darwin":
            return f"cloudflared-darwin-{'arm64' if arm else 'amd64'}.tgz"
        if sysname == "Linux":
            return f"cloudflared-linux-{'arm64' if arm else 'amd64'}"
        if sysname == "Windows":
            return "cloudflared-windows-amd64.exe"
        return None

    def install(self):
        name = self.asset()
        if not name:
            raise RuntimeError(f"no cloudflared build for {platform.system()} {platform.machine()}")
        os.makedirs(self.bin_dir, exist_ok=True)
        exe = os.path.join(self.bin_dir, self.exe_name())
        dl, tmp = exe + ".download", exe + ".part"
        self.install_pct = 0
        req = urllib.request.Request(self.BASE + name, headers={"User-Agent": "SwingDesk"})
        with urllib.request.urlopen(req, timeout=60, context=tls_context()) as r, open(dl, "wb") as f:
            total, got = int(r.headers.get("Content-Length") or 0), 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    self.install_pct = int(got * 100 / total)
        if name.endswith(".tgz"):
            with tarfile.open(dl, "r:gz") as tf:
                member = next((m for m in tf.getmembers()
                               if m.isfile() and os.path.basename(m.name) == "cloudflared"), None)
                if member is None:
                    raise RuntimeError("cloudflared is missing from the download")
                with tf.extractfile(member) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out)
            os.remove(dl)
        else:
            os.replace(dl, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, exe)
        self.install_pct = 100
        return exe

    # ---- lifecycle
    def start(self, port):
        with self.lock:
            self.port = port
            self.want = True
            if self.thread and self.thread.is_alive():
                return
            if self.state == "off":
                self.state, self.error = "starting", None
            self.thread = threading.Thread(target=self._loop, daemon=True, name="tunnel")
            self.thread.start()

    def stop(self):
        with self.lock:
            self.want = False
            proc = self.proc
        self._wake.set()
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.state, self.url, self.since = "off", None, None

    def status(self):
        return {"state": self.state, "url": self.url if self.state == "on" else None, "error": self.error,
                "since": self.since, "installed": bool(self.binary()), "install_pct": self.install_pct,
                "log": list(self.tail)[-8:]}

    def _sleep(self, sec):
        self._wake.clear()
        self._wake.wait(sec)

    def _loop(self):
        backoff = 15
        while self.want:
            exe = self.binary()
            if not exe:
                self.state, self.error = "installing", None
                try:
                    exe = self.install()
                    self.log(f"cloudflared installed: {exe}")
                except Exception as e:
                    self.state, self.error = "error", f"install failed: {type(e).__name__}: {e}"
                    self.log("tunnel: " + self.error)
                    self._sleep(120)
                    continue
            if not self.want:
                break
            self.state, self.error, self.url, self.since = "starting", None, None, None
            self.metrics_port = free_port()
            cmd = [exe, "tunnel", "--no-autoupdate", "--metrics", f"127.0.0.1:{self.metrics_port}",
                   "--url", f"http://127.0.0.1:{self.port}"]
            started = time.time()
            try:
                kw = {"creationflags": 0x08000000} if os.name == "nt" else {}   # CREATE_NO_WINDOW
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1, **kw)
            except Exception as e:
                self.state, self.error = "error", f"cannot start cloudflared: {type(e).__name__}: {e}"
                self.log("tunnel: " + self.error)
                self._sleep(60)
                continue
            with self.lock:
                self.proc = proc
            threading.Thread(target=self._watch, args=(proc,), daemon=True, name="tunnel-health").start()
            for line in proc.stdout:
                self._line(line.rstrip())
            proc.wait()
            if not self.want:
                break
            backoff = 15 if time.time() - started > 900 else min(backoff * 2, 600)
            self.state = "error"
            self.error = self.error or f"cloudflared stopped (exit {proc.returncode})"
            self.log(f"tunnel: {self.error}. retrying in {backoff}s")
            self._sleep(backoff)
        self.state = "off"

    def _line(self, line):
        if not line:
            return
        self.tail.append(line[-300:])
        m = self.URL_RE.search(line)
        if m and m.group(1) != "api":
            self.url = m.group(0)
        low = line.lower()
        if "registered tunnel connection" in low and self.url:
            if self.state != "on":
                self.state, self.error, self.since = "on", None, time.strftime("%Y-%m-%dT%H:%M:%S")
                self.log(f"remote access on: {self.url}")
            if self._announced != self.url:
                self._announced = self.url
                if self.on_url:
                    threading.Thread(target=self.on_url, args=(self.url,), daemon=True).start()
        elif "failed to request quick tunnel" in low or " err " in f" {low} ":
            self.error = line.split(" ERR ", 1)[-1].strip()[:240]

    def _ready(self):
        """True / False from cloudflared's /ready; None when the metrics port does not answer."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.metrics_port}/ready", timeout=5) as r:
                return r.status == 200
        except urllib.error.HTTPError:
            return False
        except Exception:
            return None

    def _watch(self, proc):
        bad, t0 = 0, time.time()
        while proc.poll() is None and self.want:
            time.sleep(60)
            if proc.poll() is not None or not self.want:
                return
            if self.state == "starting" and time.time() - t0 > 180:
                self.error = "no address from Cloudflare after 3 minutes"
                self.log("tunnel: " + self.error + ", restarting")
                proc.terminate()
                return
            ok = self._ready()
            bad = bad + 1 if ok is False else 0
            if bad >= 10:
                self.error = "no connection to Cloudflare for 10 minutes"
                self.log("tunnel: " + self.error + ", restarting")
                proc.terminate()
                return

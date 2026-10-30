# -*- coding: utf-8 -*-
"""
SwingDesk engine - swing scan (2-5 trading-day holds), three strategies.

continuation  Unfilled gap 5-10 sessions ago, gap-day volume >= 1.5x, close > SMA50 > SMA150
              with SMA150 rising, higher highs + higher lows, RSI 40-70, MACD up.
gapfill       Unfilled DOWN gap 1-10 sessions ago (2-10%), longer-term uptrend, bounce under
              way. Target thesis = gap fill. Fill probability measured from history.
quality       Quality company down 25-60% from its recent high, stabilising (oversold
              recovery, higher low, momentum turn), fundamentals checked (ROE, margins,
              growth, FCF, debt, EPS outlook).
Common        Earnings inside the hold window disqualify. Buy Stop entry, SL below
              structure and >= 1.2 ATR, TP = 1.5R, size from $ risk, cancel if the open
              is > 1% above entry, time exit. Gap statistics are computed on the whole
              downloaded universe.
"""
import datetime as dt
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:  # handled by the caller
    yf = None

DEFAULTS = {
    # account
    "capital": 20000.0, "total_risk": 200.0, "risk_per_trade": 100.0, "max_trades": 2,
    "rr": 1.5, "hold_days": 5, "cancel_gap_pct": 1.0,
    # universe
    "min_mcap": 3e9, "min_avgvol": 500000.0, "custom_tickers": "", "history_years": 2,
    # strategies on/off
    "use_continuation": 1, "use_gapfill": 1, "use_quality": 1,
    # continuation
    "gap_min_age": 5, "gap_max_age": 10, "gap_vol_mult": 1.5,
    "sma150_slope_days": 20, "hh_hl_days": 42, "rsi_min": 40.0, "rsi_max": 70.0,
    # gap fill
    "gf_min_age": 1, "gf_max_age": 10, "gf_min_gap": 2.0, "gf_max_gap": 10.0,
    "gf_need_trend": 1, "gf_min_prob": 0.0, "gf_min_room_atr": 2.0,
    # quality pullback
    "q_lookback": 120, "q_min_dd": 25.0, "q_max_dd": 60.0, "q_min_stab": 3, "q_min_quality": 4,
    # execution
    "min_stop_atr": 1.2, "max_tp_atr": 2.5, "max_tp_atr_block": 4.0, "trigger_bars": 3, "swing_bars": 5,
    "corr_max": 0.75,
    # backtest
    "use_backtest": 1, "bt_cost_r": 0.05,
    # opportunities
    "use_opps": 1, "opp_lookback": 126, "opp_min_drop": 15.0, "opp_gap_days": 20, "opp_min_gap": 3.0,
    "opp_min_price": 5.0, "opp_stop_bars": 10, "opp_min_stop_atr": 1.0, "opp_min_stab": 3, "opp_min_rr": 2.0,
    "opp_max_risk": 10.0, "opp_hold": 20, "opp_max": 60, "opp_alert_top": 5,
    # live, alerts, schedule
    "live_interval": 60, "auto_scan": 0, "auto_scan_time": "23:15", "auto_sync": 0,
    "tg_token": "", "tg_chat": "", "tg_on_scan": 1, "tg_alerts": 1,
    # website relay: push every scan from this computer to the SwingDesk website
    "cloud_url": "", "cloud_token": "",
    # phone: ntfy push (works anywhere) and remote access to this computer through a tunnel
    "ntfy_topic": "", "ntfy_server": "https://ntfy.sh", "ntfy_token": "", "ntfy_alerts": 1, "ntfy_on_scan": 1,
    "remote_on": 0, "keep_awake": 1,
}
STR_KEYS = {"custom_tickers", "auto_scan_time", "tg_token", "tg_chat", "cloud_url", "cloud_token",
            "ntfy_topic", "ntfy_server", "ntfy_token"}
INT_KEYS = {"max_trades", "hold_days", "gap_min_age", "gap_max_age", "sma150_slope_days",
            "hh_hl_days", "trigger_bars", "swing_bars", "history_years", "use_continuation",
            "use_gapfill", "use_quality", "gf_min_age", "gf_max_age", "gf_need_trend",
            "q_lookback", "q_min_stab", "q_min_quality", "use_backtest", "live_interval", "auto_scan",
            "tg_on_scan", "tg_alerts", "use_opps", "opp_lookback", "opp_gap_days", "opp_stop_bars", "opp_min_stab",
            "opp_hold", "opp_max", "opp_alert_top", "auto_sync", "ntfy_alerts", "ntfy_on_scan", "remote_on",
            "keep_awake"}

NYSE_HOLIDAYS = np.array([
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
], dtype="datetime64[D]")

US_EXCHANGES = ["NMS", "NYQ", "NGM", "NCM", "ASE"]
INDEX_TICKERS = ["SPY", "QQQ", "IWM", "DIA", "^VIX", "^TNX"]
CHART_BARS = 200
STRATS = ("continuation", "gapfill", "quality")
SIZE_BUCKETS = [(1, 3, "1-3%"), (3, 5, "3-5%"), (5, 8, "5-8%"), (8, 1e9, "8%+")]
AGE_BUCKETS = [(1, 2, "1-2"), (3, 5, "3-5"), (6, 10, "6-10")]
QUALITY_MAX_META = 150


class Cancelled(Exception):
    pass


# Used only when the Yahoo screener is unreachable and no earlier universe is cached.
FALLBACK_UNIVERSE = """
AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B JPM LLY V UNH XOM MA JNJ PG HD COST ABBV WMT NFLX MRK CVX KO PEP ADBE
CRM AMD ORCL BAC TMO ACN LIN MCD CSCO ABT WFC DHR DIS INTU TXN QCOM VZ PM AMGN IBM CAT GE NOW UNP NEE SPGI ISRG RTX
HON LOW GS AMAT BKNG PFE T ELV UBER BLK PLD SYK LMT DE MDT ADP TJX MS C SCHW CB VRTX REGN MMC BSX PANW ADI GILD LRCX
MU KLAC SBUX ETN CI BA SNPS CDNS MO SO DUK ZTS MDLZ CME ICE PGR AON SHW CL ITW APD EQIX MCO WM CSX NOC GD FDX EMR PH
CMG ORLY MAR HCA USB PNC TGT NSC ECL APH MSI PSX MPC VLO OXY COP EOG SLB HAL KMI WMB OKE AIG MET PRU AFL ALL TRV HUM
CVS MCK COR CAH BDX EW IDXX DXCM A IQV RMD ZBH BAX STE WAT ALGN PODD INCY BIIB MRNA ANET CRWD FTNT ZS DDOG NET SNOW
MDB TEAM WDAY ADSK PLTR SHOP SQ PYPL COIN HOOD ABNB DASH RBLX TTD APP MELI SE PDD BABA JD TSM ASML ARM SMCI DELL HPQ
HPE NTAP WDC STX MRVL ON NXPI MCHP SWKS QRVO TER ENTG MPWR FSLR ENPH NEE AES EXC D AEP SRE XEL PCG ED PEG WEC ES EIX
AMT CCI SBAC PSA O SPG WELL DLR AVB EQR VTR ARE INVH MAA ESS UDR CPT KIM REG FRT BXP HST VICI GLPI IRM EXR CBRE
F GM RIVN LCID STLA TM HMC APTV BWA LKQ GPC AZO AAP TSCO DG DLTR BBY ROST BURL ULTA LULU NKE DECK CROX TPR RL PVH
YUM DPZ QSR WING CAVA DRI TXRH HLT H WYNN LVS MGM CZR DKNG RCL CCL NCLH EXPE TRIP DAL UAL AAL LUV ALK JBLU UPS
CHRW EXPD JBHT ODFL SAIA XPO KNX R URI PWR FAST GWW MSM CTAS PAYX ROK AME DOV XYL IR TT CARR OTIS JCI LII MAS
HUBB GNRC ROP TDG HWM TXT LHX HII LDOS BAH KTOS AXON TDY FTV KEYS GRMN ZBRA TRMB IT CTSH EPAM ACN INFY WIT GDDY
AKAM FFIV JNPR CIEN ZM DOCU TWLO OKTA HUBS VEEV PAYC PCTY TOST AFRM SOFI UPST NU XP ALLY COF SYF AXP DFS FITB KEY
RF HBAN CFG MTB ZION CMA WAL EWBC NTRS STT BK RJF LPLA IBKR NDAQ CBOE MKTX MSCI FDS TROW BEN IVZ KKR BX APO ARES
CG OWL BRO AJG WTW MKL ACGL RE HIG CINF L PFG LNC UNM GL AMP VOYA EQH CRBG RGA ERIE KMB CHD CLX EL COTY KVUE HSY
GIS K CPB CAG SJM HRL TSN KHC MKC STZ TAP BF-B MNST CELH KDP SYY ADM BG CTVA FMC MOS CF NTR DOW DD LYB EMN CE ALB
PPG RPM SHW VMC MLM NUE STLD CLF X AA FCX NEM GOLD SCCO RS ATI CMC IP PKG WRK AMCR BALL SEE AVY WY LPX OC BLDR
DHI LEN PHM NVR TOL KBH MHO TMHC MTH LGIH ZG Z RDFN OPEN EBAY ETSY W CHWY CVNA KMX LAD AN PAG SAH ABG GPI MUSA
CASY KR ACI SFM BJ PSMT OLLI FIVE WSM RH ARHS HD LOW FND SN WHR TPX LEG MHK SWK SNA TTC DE AGCO CNHI PCAR CMI
ALSN OSK TEX MTW HLMN DCI NDSN IEX GGG MIDD WWD CW MOG-A HEI SPR ERJ CAE EH JOBY ACHR RKLB ASTS IRDM VSAT GSAT
""".split()


# --------------------------------------------------------------------------- utils
def settings_ns(s):
    merged = dict(DEFAULTS)
    for k, v in (s or {}).items():
        if k in DEFAULTS and v is not None and v != "":
            if k in STR_KEYS:
                merged[k] = str(v).strip()
            else:
                try:
                    merged[k] = int(float(v)) if k in INT_KEYS else float(v)
                except (TypeError, ValueError):
                    pass
    return type("S", (), merged)


def trading_day(d, k):
    d64 = np.datetime64(pd.Timestamp(d).date(), "D")
    return pd.Timestamp(np.busday_offset(d64, k, roll="forward", holidays=NYSE_HOLIDAYS)).date()


def now_tz(name):
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(name))
    except Exception:
        return None


def is_trading_day(d):
    return bool(np.is_busday(np.datetime64(d, "D"), holidays=NYSE_HOLIDAYS))


def sessions_between(a, b):
    """Trading sessions after date a up to and including date b."""
    return int(np.busday_count(np.datetime64(a, "D") + 1, np.datetime64(b, "D") + 1, holidays=NYSE_HOLIDAYS))


def latest_closed_session(ny=None):
    """The most recent US session whose daily bar should be final."""
    ny = ny or now_tz("America/New_York")
    if ny is None:
        return None
    d = np.datetime64(ny.date(), "D")
    if is_trading_day(ny.date()):
        if (ny.hour, ny.minute) >= (16, 10):
            return ny.date()
        return np.busday_offset(d, -1, holidays=NYSE_HOLIDAYS).astype(object)
    return np.busday_offset(d, 0, roll="backward", holidays=NYSE_HOLIDAYS).astype(object)


def market_state(ny=None):
    ny = ny or now_tz("America/New_York")
    if ny is None or not is_trading_day(ny.date()):
        return "closed"
    hm = (ny.hour, ny.minute)
    if (4, 0) <= hm < (9, 30):
        return "pre"
    if (9, 30) <= hm < (16, 0):
        return "open"
    if (16, 0) <= hm < (20, 0):
        return "post"
    return "closed"


def clean(o):
    """Make results JSON-safe."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    if isinstance(o, (pd.Timestamp, dt.datetime)):
        return o.isoformat()
    if isinstance(o, dt.date):
        return o.isoformat()
    return o


def num(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def retry(fn, log, what, tries=3):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            wait = 4 * (i + 1)
            log(f"{what}: {type(e).__name__}: {str(e)[:100]} (retry in {wait}s)")
            time.sleep(wait)
    return None


def size_bucket(size):
    for lo, hi, lab in SIZE_BUCKETS:
        if lo <= size < hi:
            return lab
    return None


# --------------------------------------------------------------------------- indicators
def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def wilder(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    return 100 - 100 / (1 + wilder(up, n) / wilder(dn, n))


def true_range(df):
    pc = df["Close"].shift(1)
    return pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()],
                     axis=1).max(axis=1)


def atr(df, n=14):
    return wilder(true_range(df), n)


def macd(close, fast=12, slow=26, sig=9):
    m = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    s = m.ewm(span=sig, adjust=False).mean()
    return m, s, m - s


class Ind:
    """Indicators computed once per ticker and shared by the strategies."""

    def __init__(self, df):
        self.df = df
        c = df["Close"]
        self.o, self.h, self.l = df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy()
        self.c, self.v = c.to_numpy(), df["Volume"].to_numpy()
        self.i = len(df) - 1
        self.s50, self.s150 = sma(c, 50).to_numpy(), sma(c, 150).to_numpy()
        self.e10 = ema(c, 10).to_numpy()
        self.r = rsi(c).to_numpy()
        m, sg, hist = macd(c)
        self.m, self.sg, self.hist = m.to_numpy(), sg.to_numpy(), hist.to_numpy()
        self.atr = atr(df).to_numpy()
        self.avgvol50 = float(np.nanmean(self.v[-50:]))

    def base(self, ticker):
        i, c, h = self.i, self.c, self.h
        hi52 = float(h[-252:].max())
        return {"ticker": ticker, "date": self.df.index[i].date(), "close": c[i],
                "chg_1d": (c[i] / c[i - 1] - 1) * 100, "sma50": self.s50[i], "sma150": self.s150[i],
                "rsi": self.r[i], "macd": self.m[i], "macd_signal": self.sg[i], "macd_hist": self.hist[i],
                "macd_state": "above" if self.m[i] > self.sg[i] else (
                    "turning" if self.hist[i] > self.hist[i - 1] > self.hist[i - 2] else "below"),
                "atr": self.atr[i], "atr_pct": self.atr[i] / c[i] * 100, "avgvol50": self.avgvol50,
                "high52": hi52, "dist_52wh_pct": (c[i] / hi52 - 1) * 100}


# --------------------------------------------------------------------------- data sources
FUND_KEYS = {"roe": "returnOnEquity", "op_margin": "operatingMargins", "gross_margin": "grossMargins",
             "rev_growth": "revenueGrowth", "earn_growth": "earningsGrowth", "fcf": "freeCashflow",
             "de": "debtToEquity", "pe": "trailingPE", "fpe": "forwardPE", "eps": "trailingEps",
             "feps": "forwardEps", "target": "targetMeanPrice", "n_analysts": "numberOfAnalystOpinions",
             "rec": "recommendationMean", "mcap": "marketCap", "short_pct": "shortPercentOfFloat", "beta": "beta",
             "target_low": "targetLowPrice", "target_high": "targetHighPrice"}


# --------------------------------------------------------------------------- Yahoo plumbing
# yfinance opens per-thread resources (HTTP handles, SQLite cache connections). Creating a new
# thread for every ticker or every web request leaks them until the process hits the open-files
# limit (256 on macOS). Every Yahoo call therefore runs on this small pool of long-lived threads.
# Two pools: "yfpool" for bulk scan work, "yflive" for interactive calls (live prices, news, one
# chart), so a running scan never blocks the dashboard.
_POOLS = {}
_YF_LOCK = threading.Lock()


def yf_pool(kind="yfpool"):
    with _YF_LOCK:
        if kind not in _POOLS:
            _POOLS[kind] = ThreadPoolExecutor(max_workers=4 if kind == "yfpool" else 2, thread_name_prefix=kind)
        return _POOLS[kind]


def _in_yf_thread():
    return threading.current_thread().name.startswith(("yfpool", "yflive"))


def in_pool(fn, *args, **kwargs):
    if _in_yf_thread():
        return fn(*args, **kwargs)
    return yf_pool("yfpool").submit(fn, *args, **kwargs).result()


def in_live(fn, *args, **kwargs):
    if _in_yf_thread():
        return fn(*args, **kwargs)
    return yf_pool("yflive").submit(fn, *args, **kwargs).result()


def quiet_yfinance():
    import logging
    import warnings
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    warnings.filterwarnings("ignore", module="yfinance")
    warnings.filterwarnings("ignore", message=".*inplace keyword.*")


def _frame_for(df, t, n):
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        return df[t] if t in set(df.columns.get_level_values(0)) else None
    return df if n == 1 else None


def _clean_bars(sub, min_bars):
    if sub is None:
        return None
    sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
    sub = sub[(sub["High"] > 0) & (sub["Low"] > 0)]
    if len(sub) < min_bars:
        return None
    sub = sub.copy()
    sub.index = pd.to_datetime(sub.index).tz_localize(None)
    return sub.astype(float)


def quote_from_yahoo(q, today):
    """Map one row of Yahoo's batch quote endpoint to SwingDesk's live quote."""
    def ts(key):
        v = q.get(key)
        try:
            return dt.datetime.fromtimestamp(int(v), dt.timezone.utc) if v else None
        except (TypeError, ValueError):
            return None

    def ny_date(t):
        try:
            from zoneinfo import ZoneInfo
            return t.astimezone(ZoneInfo("America/New_York")).date() if t else None
        except Exception:
            return t.date() if t else None

    state = (q.get("marketState") or "").upper()
    reg_t, pre_t, post_t = ts("regularMarketTime"), ts("preMarketTime"), ts("postMarketTime")
    reg_today = ny_date(reg_t) == today and state != "PRE"
    reg = num(q.get("regularMarketPrice"))
    pre = num(q.get("preMarketPrice")) if ny_date(pre_t) == today else None
    post = num(q.get("postMarketPrice")) if ny_date(post_t) == today else None
    if state == "PRE" and pre:
        price = pre
    elif state.startswith("POST") and post and reg_today:
        price = post
    else:
        price = reg
    if state == "PRE" or not reg_today:
        prev = reg if state == "PRE" else num(q.get("regularMarketPreviousClose"))
        session_open = day_high = day_low = None
    else:
        prev = num(q.get("regularMarketPreviousClose"))
        session_open, day_high, day_low = (num(q.get(k)) for k in
                                           ("regularMarketOpen", "regularMarketDayHigh", "regularMarketDayLow"))
    times = [t for t in (reg_t, pre_t, post_t) if t]
    last = max(times) if times else None
    try:
        from zoneinfo import ZoneInfo
        last_ny = last.astimezone(ZoneInfo("America/New_York")) if last else None
    except Exception:
        last_ny = last
    out = {"price": price, "time": last_ny.isoformat() if last_ny else None, "session_open": session_open,
           "day_high": day_high, "day_low": day_low, "pre": pre, "prev_close": prev,
           "chg_pct": (price / prev - 1) * 100 if (price and prev) else None,
           "stale": ny_date(last) != today, "market_state": state}
    return out


class YahooSource:
    name = "Yahoo Finance via yfinance"
    demo = False

    def __init__(self):
        if yf is None:
            raise RuntimeError("yfinance is not installed. Run: pip install -U yfinance")
        quiet_yfinance()

    # ---- universe
    def universe(self, a, log):
        if a.custom_tickers.strip():
            tick = sorted({t.strip().upper().replace(".", "-")
                           for t in a.custom_tickers.replace("\n", ",").split(",") if t.strip()})
            return pd.DataFrame({"ticker": tick, "name": "", "mcap": np.nan}), "custom list"
        return in_pool(self._universe, a, log)

    def _universe(self, a, log):
        from yfinance import EquityQuery as Q
        query = Q("and", [Q("eq", ["region", "us"]), Q("is-in", ["exchange"] + US_EXCHANGES),
                          Q("gt", ["intradaymarketcap", a.min_mcap]),
                          Q("gt", ["avgdailyvol3m", a.min_avgvol])])
        rows, offset, total = [], 0, None
        while offset < 6000:
            resp = retry(lambda: yf.screen(query, offset=offset, size=250,
                                           sortField="intradaymarketcap", sortAsc=False), log, "screener")
            quotes = resp.get("quotes", []) if isinstance(resp, dict) else []
            total = resp.get("total", total) if isinstance(resp, dict) else total
            for q in quotes:
                if q.get("quoteType", "EQUITY") == "EQUITY" and q.get("symbol"):
                    rows.append({"ticker": q["symbol"], "name": q.get("longName") or q.get("shortName") or "",
                                 "mcap": q.get("marketCap")})
            offset += len(quotes)
            if not quotes or (total is not None and offset >= total):
                break
        df = pd.DataFrame(rows, columns=["ticker", "name", "mcap"]).drop_duplicates("ticker")
        return df, "Yahoo equity screener (US, NYSE/Nasdaq/AMEX)"

    # ---- daily bars
    def _download(self, chunk, period, interval="1d", prepost=False):
        return yf.download(chunk, period=period, interval=interval, auto_adjust=False, actions=False,
                           group_by="ticker", threads=False, progress=False, prepost=prepost)

    def bars(self, tickers, log, progress=None, period="1y", batch=20, min_bars=175):
        from concurrent.futures import as_completed
        tickers = list(tickers)
        chunks = [tickers[i:i + batch] for i in range(0, len(tickers), batch)]
        out, done = {}, 0

        def job(chunk):
            df = retry(lambda: self._download(chunk, period), log, f"download {chunk[0]}")
            res = {}
            for t in chunk:
                try:
                    sub = _clean_bars(_frame_for(df, t, len(chunk)), min_bars)
                    if sub is not None:
                        res[t] = sub
                except Exception:
                    continue
            return chunk, res

        if _in_yf_thread():
            results = (job(c) for c in chunks)
        elif len(chunks) == 1:
            results = [in_live(job, chunks[0])]
        else:
            futs = [yf_pool().submit(job, c) for c in chunks]
            results = (f.result() for f in as_completed(futs))
        for chunk, res in results:
            out.update(res)
            done += len(chunk)
            if progress:
                progress(done, len(tickers), len(out))
        return out

    # ---- company data
    def info(self, ticker):
        return in_pool(self._info, ticker)

    def _info(self, ticker):
        try:
            inf = yf.Ticker(ticker).info or {}
        except Exception:
            inf = {}
        return {"sector": inf.get("sector", "") or "", "industry": inf.get("industry", "") or "",
                "name": inf.get("longName") or inf.get("shortName") or "",
                "fund": {k: num(inf.get(src)) for k, src in FUND_KEYS.items()}}

    def earnings(self, ticker, past=True):
        return in_pool(self._earnings, ticker, past)

    def _earnings(self, ticker, past=True):
        tk = yf.Ticker(ticker)
        dates = []
        try:
            cal = tk.calendar
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed is not None:
                dates += list(ed) if isinstance(ed, (list, tuple, np.ndarray)) else [ed]
        except Exception:
            pass
        if past:
            try:
                ed = tk.get_earnings_dates(limit=8)
                if ed is not None and len(ed):
                    dates += list(ed.index)
            except Exception:
                pass
        out = set()
        for d in dates:
            try:
                out.add(pd.Timestamp(d).date())
            except Exception:
                pass
        return sorted(out)

    def news(self, ticker, limit=8):
        return in_live(self._news, ticker, limit)

    def _news(self, ticker, limit=8):
        try:
            items = yf.Ticker(ticker).news or []
        except Exception:
            return []
        out = []
        for it in items[:limit]:
            ct = it.get("content") if isinstance(it.get("content"), dict) else it
            link = ((ct.get("canonicalUrl") or {}).get("url") or (ct.get("clickThroughUrl") or {}).get("url")
                    or ct.get("link") or it.get("link"))
            when = ct.get("pubDate") or ct.get("displayTime")
            if not when and it.get("providerPublishTime"):
                when = dt.datetime.fromtimestamp(it["providerPublishTime"], dt.timezone.utc).isoformat()
            title = ct.get("title") or it.get("title")
            if title:
                out.append({"title": title, "link": link,
                            "publisher": (ct.get("provider") or {}).get("displayName") or it.get("publisher") or "",
                            "time": when})
        return out

    # ---- live prices
    def live(self, tickers):
        """Last price incl. pre/post market, today's regular open/high/low, previous close."""
        tickers = [t for t in dict.fromkeys(tickers) if t]
        if not tickers:
            return {}
        return in_live(self._live, tickers)

    def _live(self, tickers):
        ny = now_tz("America/New_York")
        today = ny.date() if ny else dt.date.today()
        out = {}
        try:
            from yfinance.data import YfData
            data = YfData()
            for i in range(0, len(tickers), 50):
                chunk = tickers[i:i + 50]
                j = data.get_raw_json("https://query1.finance.yahoo.com/v7/finance/quote",
                                      params={"symbols": ",".join(chunk), "formatted": "false",
                                              "lang": "en-US", "region": "US"}, timeout=20)
                for q in ((j or {}).get("quoteResponse") or {}).get("result") or []:
                    sym = q.get("symbol")
                    if sym in chunk:
                        live = quote_from_yahoo(q, today)
                        if live.get("price"):
                            out[sym] = live
        except Exception:
            pass
        missing = [t for t in tickers if t not in out]
        if missing:  # batch endpoint unavailable: per-ticker charts (slower, more requests)
            try:
                m1 = self._download(missing, "1d", "1m", prepost=True)
                d1 = self._download(missing, "5d")
                for t in missing:
                    out[t] = live_from_frames(_frame_for(m1, t, len(missing)), _frame_for(d1, t, len(missing)))
            except Exception:
                pass
        return out

    def quotes(self, tickers):
        live = self.live(list(tickers))
        today = (now_tz("America/New_York") or dt.datetime.now()).date().isoformat()
        return {t: {"price": q["price"], "date": (q.get("time") or today)[:10], "prev": q.get("prev_close")}
                for t, q in live.items() if q.get("price")}


class DemoSource:
    """Synthetic market so the dashboard can be explored without internet. Not real data."""
    name = "DEMO - synthetic data"
    demo = True

    def __init__(self):
        self._cache = None

    def _idx(self, n=500):
        end = pd.Timestamp(dt.date.today())
        if end.weekday() >= 5:
            end = end - pd.offsets.BDay(1)
        return pd.bdate_range(end=end, periods=n)

    def _frame(self, rng, c, idx):
        n = len(c)
        o = np.r_[c[0], c[:-1]] * (1 + 0.002 * rng.standard_normal(n))
        h = np.maximum(o, c) * (1 + 0.012 * rng.random(n))
        lo = np.minimum(o, c) * (1 - 0.012 * rng.random(n))
        base = float(rng.uniform(0.8e6, 6e6))
        v = base * (1 + 0.25 * rng.standard_normal(n)).clip(0.4)
        return o, h, lo, c, v, base

    def _make(self, seed, drift, vol, gap_age=None, gap_size=0.0, gap_vol=3.0, flat=False, start=100.0,
              random_gaps=True, tail=None):
        rng = np.random.default_rng(seed)
        idx = self._idx()
        n = len(idx)
        rets = drift + vol * rng.standard_normal(n - 1)
        if tail:  # (bars, drift) segments applied at the end: e.g. sell-off then stabilisation
            pos = n - 1
            for bars, d in reversed(tail):
                rets[pos - bars:pos] = d + vol * 1.2 * rng.standard_normal(bars)
                pos -= bars
        if random_gaps:  # sprinkle historical gaps so the gap study has material
            for k in rng.choice(np.arange(160, n - 30), size=8, replace=False):
                rets[k - 1] += rng.choice([-1, 1]) * rng.uniform(0.02, 0.07)
        c = start * np.cumprod(np.r_[1.0, 1 + rets])
        o, h, lo, c, v, base = self._frame(rng, c, idx)
        if random_gaps:  # make the sprinkled moves true gaps
            jumps = np.where(np.abs(np.diff(c) / c[:-1]) > 0.02)[0] + 1
            for g in jumps:
                if c[g] > c[g - 1]:
                    o[g] = max(o[g], h[g - 1] * 1.004); lo[g] = max(min(lo[g], o[g], c[g]), h[g - 1] * 1.002)
                else:
                    o[g] = min(o[g], lo[g - 1] * 0.996); h[g] = min(max(h[g], o[g], c[g]), lo[g - 1] * 0.998)
                v[g] = base * rng.uniform(1.2, 3.5)
        if gap_age:
            g = n - 1 - gap_age
            f = 1 + gap_size
            o[g:] *= f; h[g:] *= f; lo[g:] *= f; c[g:] *= f
            v[g] = base * gap_vol
            if gap_size > 0:
                lo[g] = max(lo[g], h[g - 1] * 1.005); o[g] = max(o[g], lo[g])
            else:
                h[g] = min(h[g], lo[g - 1] * 0.995); o[g] = min(o[g], h[g])
            if flat:
                p = c[g]
                for k in range(g + 1, n):
                    o[k] = p; c[k] = p * (1 + 0.0004 * rng.standard_normal())
                    h[k] = max(o[k], c[k]) * 1.0012; lo[k] = min(o[k], c[k]) * 0.9988
        df = pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": v}, index=idx)
        df["High"] = df[["Open", "High", "Close"]].max(axis=1)
        df["Low"] = df[["Open", "Low", "Close"]].min(axis=1)
        return df

    def _build(self):
        if self._cache:
            return self._cache
        a = settings_ns({})
        data, kinds = {}, {}

        def add(df, kind):
            t = f"DMO{len(data) + 1:02d}"
            data[t] = df; kinds[t] = kind

        found = {"cont": 0, "corr": 0, "gf": 0, "q": 0}
        seed = 1000
        while seed < 20000 and (found["cont"] < 5 or found["corr"] < 1 or found["gf"] < 4 or found["q"] < 4):
            k = seed % 4
            if k == 0 and found["cont"] < 5:
                df = self._make(seed, 0.0015, 0.011, gap_age=5 + seed % 6, gap_size=0.05, start=30 + seed % 250)
                if analyze_cont("X", Ind(df), a)["passed"]:
                    add(df, "cont"); found["cont"] += 1
            elif k == 1 and found["corr"] < 1:
                df = self._make(seed, 0.0015, 0.011, gap_age=5 + seed % 6, gap_size=-0.03, start=30 + seed % 250)
                if analyze_cont("X", Ind(df), a)["passed"]:
                    add(df, "corr"); found["corr"] += 1
            elif k == 2 and found["gf"] < 4:
                df = self._make(seed, 0.0012, 0.011, gap_age=1 + seed % 8, gap_size=-(0.025 + (seed % 5) * 0.01),
                                start=30 + seed % 250, tail=None)
                if analyze_gapfill("X", Ind(df), a, None)["passed"]:
                    add(df, "gf"); found["gf"] += 1
            elif k == 3 and found["q"] < 4:
                df = self._make(seed, 0.0013, 0.012, start=40 + seed % 300,
                                tail=[(45, -0.0095), (18, 0.0045)])
                if analyze_quality_price("X", Ind(df), a)["passed"]:
                    add(df, "q"); found["q"] += 1
            seed += 1
        k = 0
        while len(data) < 70:
            k += 1
            df = self._make(20000 + k, 0.0012 if k % 3 else -0.001, 0.012,
                            gap_age=(1 + k % 10) if k % 2 else None, gap_size=0.05 if k % 4 else -0.04,
                            gap_vol=1.1 if k % 5 == 0 else 2.6, start=20 + (k * 37) % 300,
                            tail=[(40, -0.008), (10, 0.0)] if k % 7 == 0 else None)
            add(df, "other")
        add(self._make(77, 0.0012, 0.011, gap_age=6, gap_size=0.22, flat=True), "frozen")
        sectors = ["Technology", "Healthcare", "Industrials", "Financial Services", "Consumer Cyclical", "Energy"]
        meta = {}
        last = data[next(iter(data))].index[-1].date()
        for i, t in enumerate(data):
            rng = np.random.default_rng(i + 3)
            ed = []
            if i % 4 == 0:
                ed.append(trading_day(last, 2 + i % 3))
            if i % 3 == 0:
                ed.append(data[t].index[-(5 + i % 6)].date())
            good = kinds[t] == "q" and i % 5 != 0
            px = float(data[t]["Close"].iloc[-1])
            eps = float(rng.uniform(1, 8))
            fund = {"roe": float(rng.uniform(0.14, 0.35) if good else rng.uniform(-0.05, 0.18)),
                    "op_margin": float(rng.uniform(0.12, 0.35) if good else rng.uniform(-0.1, 0.15)),
                    "gross_margin": float(rng.uniform(0.3, 0.7)),
                    "rev_growth": float(rng.uniform(0.02, 0.25) if good else rng.uniform(-0.15, 0.1)),
                    "earn_growth": float(rng.uniform(-0.1, 0.3)),
                    "fcf": float(rng.uniform(2e8, 3e9) if good else rng.uniform(-5e8, 1e9)),
                    "de": float(rng.uniform(10, 120) if good else rng.uniform(40, 300)),
                    "pe": px / eps, "fpe": px / (eps * (1.15 if good else 0.95)), "eps": eps,
                    "feps": eps * (1.15 if good else 0.95), "target": px * float(rng.uniform(1.1, 1.6)),
                    "n_analysts": float(rng.integers(4, 30)), "rec": float(rng.uniform(1.6, 3.0)), "mcap": 5e9 + i * 1e9,
                    "short_pct": float(rng.uniform(0.01, 0.18)), "beta": float(rng.uniform(0.6, 1.8)),
                    "target_low": px * 0.9, "target_high": px * 1.9}
            meta[t] = {"earnings": sorted(ed), "sector": sectors[i % len(sectors)],
                       "industry": f"Demo industry {i % 9}", "name": f"Demo Company {t[3:]}", "fund": fund}
        idx = {t: self._make(500 + j, 0.0006, 0.009, start=100 + 50 * j, random_gaps=False)
               for j, t in enumerate(INDEX_TICKERS)}
        idx["^VIX"] = self._make(901, 0.0, 0.03, start=17, random_gaps=False)
        idx["^TNX"] = self._make(902, 0.0, 0.01, start=4.2, random_gaps=False)
        self._cache = (data, meta, idx)
        return self._cache

    def universe(self, a, log):
        data, meta, _ = self._build()
        return pd.DataFrame({"ticker": list(data), "name": [meta[t]["name"] for t in data], "mcap": 5e9}), "demo universe"

    def bars(self, tickers, log, progress=None, period="1y", **kw):
        data, _, idx = self._build()
        out = {}
        for i, t in enumerate(tickers):
            if t in data:
                out[t] = data[t]
            elif t in idx:
                out[t] = idx[t]
            if progress and (i % 10 == 0 or i == len(tickers) - 1):
                progress(i + 1, len(tickers), len(out))
                time.sleep(0.02)
        return out

    def info(self, ticker):
        _, meta, _ = self._build()
        m = meta.get(ticker, {})
        return {"sector": m.get("sector", ""), "industry": m.get("industry", ""), "name": m.get("name", ""),
                "fund": m.get("fund", {})}

    def earnings(self, ticker, past=True):
        _, meta, _ = self._build()
        return meta.get(ticker, {}).get("earnings", [])

    def news(self, ticker, limit=8):
        return [{"title": f"Demo headline {k + 1} for {ticker} (synthetic)", "link": None, "publisher": "Demo",
                 "time": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=7 * k)).isoformat()} for k in range(3)]

    def live(self, tickers):
        data, _, idx = self._build()
        ny = now_tz("America/New_York") or dt.datetime.now()
        rng = np.random.default_rng(int(ny.timestamp() // 60))
        out = {}
        for t in tickers:
            df = data.get(t) if t in data else idx.get(t)
            if df is None:
                continue
            last = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            px = last * (1 + 0.012 * rng.standard_normal())
            op = last * (1 + 0.006 * rng.standard_normal())
            out[t] = {"price": px, "time": ny.isoformat(), "session_open": op, "day_high": max(px, op) * 1.004,
                      "day_low": min(px, op) * 0.996, "pre": last * (1 + 0.004 * rng.standard_normal()),
                      "prev_close": last, "chg_pct": (px / last - 1) * 100, "stale": False, "prev_prev": prev}
        return out

    def quotes(self, tickers):
        data, _, idx = self._build()
        out = {}
        for t in tickers:
            df = data.get(t) if t in data else idx.get(t)
            if df is None:
                df = self._make(abs(hash(t)) % 10000, 0.001, 0.012, start=50, random_gaps=False)
            out[t] = {"price": float(df["Close"].iloc[-1]), "date": df.index[-1].date().isoformat(),
                      "prev": float(df["Close"].iloc[-2])}
        return out


def live_from_frames(m1, d1):
    """Turn 1-minute (with pre/post) and daily frames into a live quote."""
    out = {"price": None, "time": None, "session_open": None, "day_high": None, "day_low": None,
           "pre": None, "prev_close": None, "chg_pct": None, "stale": True}
    ny_today = (now_tz("America/New_York") or dt.datetime.now()).date()
    if m1 is not None and not m1.empty:
        m = m1.dropna(subset=["Close"])
        if not m.empty:
            idx = pd.DatetimeIndex(m.index)
            idx = idx.tz_localize("America/New_York") if idx.tz is None else idx.tz_convert("America/New_York")
            m = m.set_axis(idx)
            mins = idx.hour * 60 + idx.minute
            reg = m[(mins >= 570) & (mins < 960)]
            pre = m[mins < 570]
            out.update(price=float(m["Close"].iloc[-1]), time=idx[-1].isoformat(),
                       stale=idx[-1].date() != ny_today)
            if len(reg):
                out.update(session_open=float(reg["Open"].iloc[0]), day_high=float(reg["High"].max()),
                           day_low=float(reg["Low"].min()))
            if len(pre):
                out["pre"] = float(pre["Close"].iloc[-1])
    if d1 is not None and not d1.empty:
        d = d1.dropna(subset=["Close"])
        if len(d):
            didx = pd.DatetimeIndex(d.index)
            last_day = (didx.tz_convert("America/New_York") if didx.tz is not None else didx)[-1].date()
            if last_day == ny_today and len(d) > 1 and out["session_open"] is not None:
                out["prev_close"] = float(d["Close"].iloc[-2])
            else:
                out["prev_close"] = float(d["Close"].iloc[-1])
            if out["price"] is None:
                out["price"] = float(d["Close"].iloc[-1])
    if out["price"] is not None and out["prev_close"]:
        out["chg_pct"] = (out["price"] / out["prev_close"] - 1) * 100
    return out


# --------------------------------------------------------------------------- gap study
class GapStudy:
    """Every full gap (>= 1%) in the downloaded history: did it fill, and how fast."""

    def __init__(self):
        self.rows = []

    def add(self, ind, follow=30):
        o, h, l, c, v, s150 = ind.o, ind.h, ind.l, ind.c, ind.v, ind.s150
        n = len(c)
        if n < 170:
            return
        cand = np.where((l[1:] > h[:-1]) | (h[1:] < l[:-1]))[0] + 1
        for t in cand:
            if t < 151 or t >= n - 1:
                continue
            size = abs(o[t] / c[t - 1] - 1) * 100
            if size < 1 or not s150[t - 1] == s150[t - 1]:
                continue
            up = l[t] > h[t - 1]
            level = h[t - 1] if up else l[t - 1]
            end = min(n, t + 1 + follow)
            seg = l[t + 1:end] <= level if up else h[t + 1:end] >= level
            hit = np.flatnonzero(seg)
            f = int(hit[0]) + 1 if len(hit) else 999
            avg_v = v[t - 50:t].mean()
            fr = c[t + 10] / c[t + 5] - 1 if (t + 10 < n and f > 5) else np.nan
            self.rows.append((1 if up else 0, size, v[t] / avg_v if avg_v > 0 else np.nan,
                              1 if c[t - 1] > s150[t - 1] else 0, f, end - t - 1, fr))

    def finish(self):
        a = np.array(self.rows, dtype=float) if self.rows else np.zeros((0, 7))
        self.up, self.size, self.vr, self.trend, self.f, self.obs, self.fr = (a[:, k] for k in range(7))
        return self

    def _sel(self, up, size_lo, size_hi, trend):
        m = (self.up == up) & (self.size >= size_lo) & (self.size < size_hi)
        if trend is not None:
            m &= self.trend == trend
        return m

    def cond_fill(self, up, size, trend, age, horizon=5):
        """P(gap fills within the next `horizon` sessions | still open after `age` sessions)."""
        lo, hi, lab = next(((a, b, c) for a, b, c in SIZE_BUCKETS if a <= size < b), SIZE_BUCKETS[-1])
        attempts = [(lo, hi, trend, f"{'up' if up else 'down'} {lab}, {'uptrend' if trend else 'no uptrend'}"),
                    (lo, hi, None, f"{'up' if up else 'down'} {lab}"),
                    (0, 1e9, None, f"all {'up' if up else 'down'} gaps")]
        for slo, shi, tr, label in attempts:
            m = self._sel(up, slo, shi, tr) & (self.obs >= age + horizon) & (self.f > age)
            nn = int(m.sum())
            if nn >= 30 or label.startswith("all"):
                p = float(((self.f <= age + horizon) & m).sum() / nn) if nn else None
                return {"p": p, "n": nn, "basis": label, "age": int(age), "horizon": horizon}
        return {"p": None, "n": 0, "basis": "", "age": int(age), "horizon": horizon}

    def summary(self):
        out = {"n_events": int(len(self.f)), "fill": [], "cond": [], "cont": None, "cont_by_size": []}
        for up in (0, 1):
            for lo, hi, lab in SIZE_BUCKETS:
                for tr in (1, 0):
                    m = self._sel(up, lo, hi, tr)
                    row = {"dir": "up" if up else "down", "size": lab, "trend": bool(tr), "n": int(m.sum())}
                    for k in (5, 10, 20):
                        mk = m & (self.obs >= k)
                        row[f"p{k}"] = float(((self.f <= k) & mk).sum() / mk.sum()) if mk.sum() else None
                    out["fill"].append(row)
                    for alo, ahi, alab in AGE_BUCKETS:
                        tot = suc = 0
                        for age in range(alo, ahi + 1):
                            mm = m & (self.obs >= age + 5) & (self.f > age)
                            tot += int(mm.sum()); suc += int(((self.f <= age + 5) & mm).sum())
                        out["cond"].append({"dir": row["dir"], "size": lab, "trend": bool(tr), "age": alab,
                                            "n": tot, "p": suc / tot if tot else None})
        m = (self.up == 1) & (self.trend == 1) & (self.vr >= 1.5) & ~np.isnan(self.fr)
        if m.sum():
            fr = self.fr[m]
            out["cont"] = {"n": int(m.sum()), "median": float(np.median(fr)), "mean": float(fr.mean()),
                           "win": float((fr > 0).mean())}
            for lo, hi, lab in SIZE_BUCKETS:
                mm = m & (self.size >= lo) & (self.size < hi)
                if mm.sum():
                    f2 = self.fr[mm]
                    out["cont_by_size"].append({"size": lab, "n": int(mm.sum()), "median": float(np.median(f2)),
                                                "win": float((f2 > 0).mean())})
        return out


# --------------------------------------------------------------------------- shared helpers
def find_gaps(ind, min_age, max_age, vol_lb=50):
    o, h, l, c, v = ind.o, ind.h, ind.l, ind.c, ind.v
    last = len(c) - 1
    gaps = []
    for age in range(min_age, max_age + 1):
        g = last - age
        if g - vol_lb < 1:
            continue
        up, down = l[g] > h[g - 1], h[g] < l[g - 1]
        if not (up or down):
            continue
        avg_v = v[g - vol_lb:g].mean()
        rec = {"g": g, "gap_date": ind.df.index[g].date(), "age": age, "dir": "up" if up else "down",
               "gap_pct": (o[g] / c[g - 1] - 1) * 100,
               "vol_ratio": v[g] / avg_v if avg_v > 0 else np.nan,
               "trend_at_gap": bool(c[g - 1] > ind.s150[g - 1])}
        if up:
            rec.update(zone_lo=h[g - 1], zone_hi=l[g], fill_level=h[g - 1],
                       filled=bool(l[g + 1:].min() <= h[g - 1]) if g < last else False)
        else:
            rec.update(zone_lo=h[g], zone_hi=l[g - 1], fill_level=l[g - 1],
                       filled=bool(h[g + 1:].max() >= l[g - 1]) if g < last else False)
        gaps.append(rec)
    return sorted(gaps, key=lambda r: r["age"])


def detect_pattern(ind, g, gap_dir):
    df, at = ind.df, ind.atr[ind.i]
    post = df.iloc[g + 1:]
    if len(post) < 4 or not at:
        return "none"
    x = np.arange(len(post))
    hs = np.polyfit(x, post["High"].values, 1)[0]
    ls = np.polyfit(x, post["Low"].values, 1)[0]
    cs = np.polyfit(x, post["Close"].values, 1)[0]
    rng = post["High"].max() - post["Low"].min()
    tr = true_range(df)
    atr5, atr20 = tr.iloc[-5:].mean(), tr.iloc[-20:].mean()
    if hs < 0 < ls:
        return "triangle"
    if gap_dir == "up" and rng <= 2.5 * at and cs <= 0.15 * at:
        return "flag"
    if atr5 < 0.75 * atr20 and rng <= 3 * at:
        return "consolidation"
    return "none"


def frozen_after_gap(ind, g):
    df = ind.df
    post, pre = df.iloc[g + 1:], df.iloc[max(0, g - 20):g]
    if len(post) < 3 or len(pre) < 10:
        return False
    dr_post = ((post["High"] - post["Low"]) / post["Close"]).mean()
    dr_pre = ((pre["High"] - pre["Low"]) / pre["Close"]).mean()
    drift = post["Close"].max() / post["Close"].min() - 1
    return bool(dr_post < 0.35 * dr_pre and dr_post < 0.01 and drift < 0.015)


def finish_row(row, checks, order):
    row["checks"] = checks
    row["fails"] = [k for k in order if not checks[k]]
    row["passed"] = not row["fails"]
    return row


# --------------------------------------------------------------------------- strategy 1: continuation
CONT_ORDER = ["liquidity_50d", "gap_in_window", "gap_unfilled", "gap_volume", "close_gt_sma50",
              "close_gt_sma150", "sma50_gt_sma150", "sma150_rising", "hh_hl", "rsi_range", "macd"]


def analyze_cont(ticker, ind, a):
    i, h, l, c = ind.i, ind.h, ind.l, ind.c
    gaps = find_gaps(ind, a.gap_min_age, a.gap_max_age)
    unfilled = [g for g in gaps if not g["filled"]]
    qual = [g for g in unfilled if g["vol_ratio"] >= a.gap_vol_mult]
    gap = qual[0] if qual else (unfilled[0] if unfilled else (gaps[0] if gaps else None))
    lb = a.hh_hl_days
    half = lb // 2
    h1, h2, l1, l2 = h[-lb:-half].max(), h[-half:].max(), l[-lb:-half].min(), l[-half:].min()
    s150_prev = ind.s150[i - a.sma150_slope_days]
    row = ind.base(ticker)
    row.update(strategy="continuation", sma150_slope_pct=(ind.s150[i] / s150_prev - 1) * 100,
               pattern="none", frozen=False, dir=None, setup=None)
    checks = {
        "liquidity_50d": ind.avgvol50 >= a.min_avgvol, "gap_in_window": bool(gaps),
        "gap_unfilled": bool(unfilled), "gap_volume": bool(qual),
        "close_gt_sma50": bool(c[i] > ind.s50[i]), "close_gt_sma150": bool(c[i] > ind.s150[i]),
        "sma50_gt_sma150": bool(ind.s50[i] > ind.s150[i]), "sma150_rising": bool(ind.s150[i] > s150_prev),
        "hh_hl": bool(h2 > h1 and l2 > l1), "rsi_range": bool(a.rsi_min <= ind.r[i] <= a.rsi_max),
        "macd": bool(ind.m[i] > ind.sg[i] or ind.hist[i] > ind.hist[i - 1] > ind.hist[i - 2]),
    }
    if gap:
        row.update({k: gap[k] for k in ("gap_date", "age", "dir", "gap_pct", "vol_ratio",
                                        "zone_lo", "zone_hi", "fill_level", "filled")})
        row["setup"] = "continuation" if gap["dir"] == "up" else "correction"
        row["pattern"] = detect_pattern(ind, gap["g"], gap["dir"])
        row["frozen"] = frozen_after_gap(ind, gap["g"])
    return finish_row(row, checks, CONT_ORDER)


# --------------------------------------------------------------------------- strategy 2: gap fill
GF_ORDER = ["liquidity_50d", "gf_gap", "gf_room", "gf_trend", "gf_bounce", "gf_momentum", "gf_prob"]


def analyze_gapfill(ticker, ind, a, study):
    i, l, c = ind.i, ind.l, ind.c
    at = ind.atr[i]
    gaps = [g for g in find_gaps(ind, a.gf_min_age, a.gf_max_age)
            if g["dir"] == "down" and not g["filled"] and a.gf_min_gap <= -g["gap_pct"] <= a.gf_max_gap]
    row = ind.base(ticker)
    row.update(strategy="gapfill", setup="gapfill", pattern="none", frozen=False, dir=None)
    long_trend = bool(c[i] > ind.s150[i] or ind.s150[i] > ind.s150[i - 20])
    checks = {"liquidity_50d": ind.avgvol50 >= a.min_avgvol, "gf_gap": bool(gaps), "gf_room": False,
              "gf_trend": long_trend or not a.gf_need_trend, "gf_bounce": False, "gf_momentum": False,
              "gf_prob": True}
    row["long_trend"] = long_trend
    if gaps:
        g = gaps[0]
        post_low = float(l[g["g"]:].min())
        row.update({k: g[k] for k in ("gap_date", "age", "dir", "gap_pct", "vol_ratio", "zone_lo",
                                      "zone_hi", "fill_level", "filled", "trend_at_gap")})
        row.update(post_low=post_low, to_fill_pct=(g["fill_level"] / c[i] - 1) * 100,
                   to_fill_atr=(g["fill_level"] - c[i]) / at if at else None)
        checks["gf_room"] = bool(at and (g["fill_level"] - c[i]) / at >= a.gf_min_room_atr)
        checks["gf_bounce"] = bool(c[i] - post_low >= 0.5 * at and c[i] > c[i - 1])
        checks["gf_momentum"] = bool(ind.hist[i] > ind.hist[i - 1])
        row["frozen"] = frozen_after_gap(ind, g["g"])
        if study is not None:
            prob = study.cond_fill(0, -g["gap_pct"], 1 if g["trend_at_gap"] else 0, g["age"], a.hold_days)
            row["fill_prob"] = prob
            checks["gf_prob"] = prob["p"] is None or prob["p"] >= a.gf_min_prob
    return finish_row(row, checks, GF_ORDER)


# --------------------------------------------------------------------------- strategy 3: quality pullback
Q_ORDER = ["liquidity_50d", "q_drawdown", "q_stab", "q_quality"]
STAB_KEYS = ["s_rsi", "s_ema", "s_hl", "s_macd", "s_upvol"]
QUAL_KEYS = ["f_roe", "f_margin", "f_growth", "f_fcf", "f_debt", "f_eps"]


def analyze_quality_price(ticker, ind, a):
    i, h, l, c, v = ind.i, ind.h, ind.l, ind.c, ind.v
    lb = min(a.q_lookback, len(h) - 1)
    k_hi = int(np.argmax(h[-lb:]))
    hi = float(h[-lb:][k_hi])
    dd = (c[i] / hi - 1) * 100
    up_days = np.diff(c[-11:]) > 0
    vol10 = v[-10:]
    stab = {
        "s_rsi": bool(np.nanmin(ind.r[-15:]) < 35 and ind.r[i] > 40),
        "s_ema": bool(c[i] > ind.e10[i]),
        "s_hl": bool(l[-10:].min() > l[-20:-10].min()),
        "s_macd": bool(ind.m[i] > ind.sg[i] or ind.hist[i] > ind.hist[i - 1] > ind.hist[i - 2]),
        "s_upvol": bool(vol10[up_days].sum() > vol10[~up_days].sum()),
    }
    row = ind.base(ticker)
    row.update(strategy="quality", setup="quality", pattern="none", frozen=False, dir=None,
               recent_high=hi, recent_high_date=ind.df.index[len(h) - lb + k_hi].date(),
               dd_pct=dd, stab=stab, stab_n=sum(stab.values()), low_10=float(l[-10:].min()),
               to_high_pct=(hi / c[i] - 1) * 100, to_sma50_pct=(ind.s50[i] / c[i] - 1) * 100)
    checks = {"liquidity_50d": ind.avgvol50 >= a.min_avgvol,
              "q_drawdown": bool(-a.q_max_dd <= dd <= -a.q_min_dd),
              "q_stab": row["stab_n"] >= a.q_min_stab, "q_quality": True}
    return finish_row(row, checks, Q_ORDER)


def apply_fundamentals(row, fund, a):
    f = fund or {}
    roe, om, rg, fcf, de, eps, feps = (f.get(k) for k in ("roe", "op_margin", "rev_growth", "fcf", "de", "eps", "feps"))
    sig = {"f_roe": None if roe is None else roe >= 0.12,
           "f_margin": None if om is None else om >= 0.10,
           "f_growth": None if rg is None else rg > 0,
           "f_fcf": None if fcf is None else fcf > 0,
           "f_debt": None if de is None else de < 150,
           "f_eps": None if (eps is None or feps is None) else feps > eps}
    avail = [x for x in sig.values() if x is not None]
    row["fund"] = f
    row["qual"] = sig
    row["qual_n"], row["qual_avail"] = sum(avail), len(avail)
    mcap = f.get("mcap")
    row["p_fcf"] = mcap / fcf if (mcap and fcf and fcf > 0) else None
    row["to_target_pct"] = ((f["target"] / row["close"] - 1) * 100
                            if f.get("target") and (f.get("n_analysts") or 0) >= 3 else None)
    ok = len(avail) >= 4 and row["qual_n"] >= a.q_min_quality
    row["checks"]["q_quality"] = ok
    row["fails"] = [k for k in Q_ORDER if not row["checks"][k]]
    row["passed"] = not row["fails"]


# --------------------------------------------------------------------------- plans
def build_plan(row, ind, a, risk_budget, entry_date, exit_date):
    h, l, c = ind.h, ind.l, ind.c
    at = row["atr"]
    strat = row["strategy"]
    if strat == "continuation":
        trigger = h[-a.trigger_bars:].max() if row["dir"] == "up" else h[-2:].max()
        struct, struct_label = l[-a.swing_bars:].min(), f"swing_{a.swing_bars}"
    elif strat == "gapfill":
        trigger = h[-2:].max()
        struct, struct_label = row["post_low"], "post_gap_low"
    else:
        trigger = h[-3:].max()
        struct, struct_label = l[-a.swing_bars:].min(), f"swing_{a.swing_bars}"
    entry = round(trigger + max(0.02, round(0.001 * trigger, 2)), 2)
    sl_struct, sl_atr = struct - 0.1 * at, entry - a.min_stop_atr * at
    sl = round(min(sl_struct, sl_atr), 2)
    basis = struct_label if sl_struct <= sl_atr else "atr_floor"
    rps = entry - sl
    tp = round(entry + a.rr * rps, 2)
    max_exp = a.capital / max(1, a.max_trades)
    qty = int(risk_budget // rps) if rps > 0 and risk_budget > 0 else 0
    capped = qty * entry > max_exp
    if capped:
        qty = int(max_exp // entry)

    flags, blocking = [], []
    stop_atr, tp_atr = rps / at, (tp - entry) / at
    if stop_atr > 3:
        flags.append({"code": "wide_stop", "v": round(stop_atr, 1)})
    if tp_atr > a.max_tp_atr_block:
        blocking.append({"code": "tp_unrealistic", "v": round(tp_atr, 1), "days": a.hold_days})
    elif tp_atr > a.max_tp_atr:
        flags.append({"code": "tp_far", "v": round(tp_atr, 1)})
    if entry < row["high52"] < tp:
        flags.append({"code": "high52_between", "v": round(row["high52"], 2)})
    if (entry / c[-1] - 1) * 100 > 3:
        flags.append({"code": "trigger_far", "v": round((entry / c[-1] - 1) * 100, 1)})
    if capped:
        flags.append({"code": "qty_capped", "v": round(max_exp)})
    rr_fill = None
    if row.get("dir") == "down":
        rr_fill = (row["fill_level"] - entry) / rps if rps > 0 else None
        if row["fill_level"] < tp:
            item = {"code": "gapfill_below_tp", "v": round(row["fill_level"], 2), "rr": round(rr_fill or 0, 2)}
            (blocking if (rr_fill or 0) < a.rr else flags).append(item)
    if strat == "gapfill" and row.get("catalyst") == "earnings":
        flags.append({"code": "earnings_gap_drift"})
    if strat == "quality" and row.get("close", 0) < row.get("sma150", 0):
        flags.append({"code": "below_sma150"})
    if qty <= 0:
        blocking.append({"code": "qty_zero"})
    if row.get("frozen"):
        blocking.append({"code": "frozen"})
    if row.get("earn_in_window"):
        blocking.append({"code": "earnings_in_window", "v": row["earn_in_window"]})
    if not row.get("earnings_known", True):
        flags.append({"code": "earnings_unknown"})
    if row.get("earn_before_entry"):
        flags.append({"code": "earnings_before_entry", "v": row["earn_before_entry"]})

    return {
        "key": f"{strat}:{row['ticker']}", "ticker": row["ticker"], "strategy": strat, "setup": row["setup"],
        "trigger": round(trigger, 2), "entry": entry, "sl": sl, "sl_basis": basis, "struct_low": round(struct, 2),
        "tp": tp, "risk_ps": round(rps, 2), "stop_atr": stop_atr, "tp_atr": tp_atr, "qty": qty,
        "exposure": qty * entry, "risk_usd": qty * rps, "reward_usd": qty * (tp - entry),
        "cancel_above": round(entry * (1 + a.cancel_gap_pct / 100), 2),
        "alt_limit": round(row["zone_hi"], 2) if (strat == "continuation" and row.get("dir") == "up") else None,
        "fill_level": round(row["fill_level"], 2) if row.get("fill_level") is not None else None,
        "rr_to_fill": rr_fill, "entry_date": entry_date, "time_exit": exit_date, "atr": at,
        "per_trade_risk": risk_budget, "max_exposure": max_exp, "rr": a.rr,
        "flags": flags, "blocking": blocking, "ok": not blocking,
    }


def score(row, plan):
    s = row["strategy"]
    if s == "continuation":
        v = min(row.get("vol_ratio") or 0, 4) + (2 if row.get("pattern", "none") != "none" else 0)
        v += (1 if 45 <= row["rsi"] <= 65 else 0) + (1 if row["macd_state"] == "above" else 0)
        v += 1 if row["dir"] == "up" else 0
        v += ((row.get("rs_rank") or 50) - 50) / 25
    elif s == "gapfill":
        p = (row.get("fill_prob") or {}).get("p") or 0
        v = 6 * p + (2 if row.get("long_trend") else 0) + max(0.0, min(2.0, (plan.get("rr_to_fill") or 0) - 1.5))
    else:
        v = 5 * row.get("qual_n", 0) / max(1, row.get("qual_avail", 1)) + 3 * row.get("stab_n", 0) / 5
        v += 2 * min(1.0, max(0.0, (row.get("to_high_pct") or 0) / 50))
    v -= len(plan["flags"])
    return round(max(0.0, min(10.0, v)), 2)


def returns_corr(data, t1, t2, n=60):
    rets = pd.concat([data[t1]["Close"].pct_change(), data[t2]["Close"].pct_change()], axis=1).iloc[-n:].dropna()
    return float(rets.corr().iloc[0, 1]) if len(rets) > 20 else None


def select(plans, rows_by_key, data, slots, corr_max):
    chosen, notes, used = [], {}, set()
    for p in sorted(plans, key=lambda p: -p["score"]):
        if not p["ok"]:
            continue
        if p["ticker"] in used:
            notes[p["key"]] = {"code": "same_ticker"}
            continue
        if len(chosen) >= slots:
            notes.setdefault(p["key"], {"code": "no_slots"})
            continue
        clash = None
        for q in chosen:
            ri, rq = rows_by_key[p["key"]], rows_by_key[q["key"]]
            if ri.get("industry") and ri.get("industry") == rq.get("industry"):
                clash = {"code": "same_industry", "v": q["ticker"]}
                break
            cr = returns_corr(data, p["ticker"], q["ticker"])
            if cr is not None and cr > corr_max:
                clash = {"code": "high_corr", "v": q["ticker"], "corr": round(cr, 2)}
                break
        if clash:
            notes[p["key"]] = clash
            continue
        chosen.append(p)
        used.add(p["ticker"])
    return chosen, notes


# --------------------------------------------------------------------------- charts / market
def chart_series(df, n=CHART_BARS):
    c = df["Close"]
    s50, s150, s200, r = sma(c, 50), sma(c, 150), sma(c, 200), rsi(c)
    tail = df.iloc[-n:]
    times = [d.strftime("%Y-%m-%d") for d in tail.index]

    def line(s, nd=3):
        s = s.iloc[-n:]
        return [{"time": t, "value": round(float(v), nd)} for t, v in zip(times, s.values) if v == v]

    bars = [{"time": t, "open": round(float(o), 3), "high": round(float(h), 3), "low": round(float(l), 3),
             "close": round(float(cl), 3), "volume": float(vo) if vo == vo else 0.0}
            for t, o, h, l, cl, vo in zip(times, tail["Open"], tail["High"], tail["Low"], tail["Close"], tail["Volume"])]
    return {"bars": bars, "sma50": line(s50), "sma150": line(s150), "sma200": line(s200), "rsi": line(r, 1)}


def market_rows(idx_data):
    rows = []
    for t in INDEX_TICKERS:
        df = idx_data.get(t)
        if df is None or len(df) < 160:
            rows.append({"ticker": t})
            continue
        c = df["Close"]
        s50, s150 = sma(c, 50).iloc[-1], sma(c, 150).iloc[-1]
        rows.append({"ticker": t, "date": df.index[-1].date(), "close": c.iloc[-1],
                     "chg_1d": (c.iloc[-1] / c.iloc[-2] - 1) * 100, "chg_5d": (c.iloc[-1] / c.iloc[-6] - 1) * 100,
                     "vs_sma50": (c.iloc[-1] / s50 - 1) * 100, "vs_sma150": (c.iloc[-1] / s150 - 1) * 100,
                     "rsi": rsi(c).iloc[-1], "spark": [round(float(x), 4) for x in c.iloc[-30:].values]})
    return rows


def regime(rows):
    by = {r["ticker"]: r for r in rows if r.get("close") is not None}
    out = []
    for t in ("SPY", "QQQ", "IWM"):
        r = by.get(t)
        if not r:
            continue
        a50, a150 = r["vs_sma50"] > 0, r["vs_sma150"] > 0
        out.append({"key": f"{t}_trend", "ticker": t, "state": "ok" if (a50 and a150) else ("warn" if a150 else "bad"),
                    "above50": a50, "above150": a150})
    v = by.get("^VIX")
    if v:
        out.append({"key": "vix", "ticker": "^VIX", "value": v["close"],
                    "state": "ok" if v["close"] < 20 else ("warn" if v["close"] < 25 else "bad")})
    return out


def fetch_market(source, log=lambda m: None):
    idx = source.bars(INDEX_TICKERS, log, period="1y", batch=10, min_bars=150)
    rows = market_rows(idx)
    return {"rows": rows, "regime": regime(rows), "pulled_utc": dt.datetime.now(dt.timezone.utc)}


def chart_for(source, ticker, log=lambda m: None):
    data = source.bars([ticker], log, period="2y", batch=1, min_bars=30)
    df = data.get(ticker)
    return chart_series(df) if df is not None else None


def parallel(fn, items, step=None, workers=6):
    out = {}
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for k, fut in enumerate(futs):
            try:
                out[futs[fut]] = fut.result()
            except Exception:
                out[futs[fut]] = None
            if step:
                step(k + 1, len(items))
    return out


# --------------------------------------------------------------------------- scan
def run_scan(settings, positions, events, source, progress=None, cancel=None, fallback_universe=None):
    """progress(pct, message) ; cancel() -> bool"""
    a = settings_ns(settings)
    logs = []

    def log(msg):
        logs.append(msg)

    def step(pct, msg):
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(pct, msg)

    t0 = time.time()
    pulled_utc = dt.datetime.now(dt.timezone.utc)
    il, ny = now_tz("Asia/Jerusalem"), now_tz("America/New_York")

    open_pos = [p for p in (positions or []) if p.get("status", "open") == "open"]
    open_risk = sum(max(0.0, (float(p["entry"]) - float(p["sl"])) * float(p["qty"])) for p in open_pos)
    slots = max(0, a.max_trades - len(open_pos))
    budget = max(0.0, a.total_risk - open_risk)
    per_trade = min(a.risk_per_trade, budget / slots) if slots else 0.0
    held = {str(p["ticker"]).upper() for p in open_pos}

    step(2, "universe")
    try:
        uni, uni_src = source.universe(a, log)
    except Exception as e:
        log(f"universe: {type(e).__name__}: {e}")
        uni, uni_src = pd.DataFrame(columns=["ticker", "name", "mcap"]), ""
    if uni.empty and not a.custom_tickers.strip():
        cached = [t for t in (fallback_universe or []) if t]
        tick = cached if len(cached) >= 100 else sorted(set(FALLBACK_UNIVERSE))
        uni = pd.DataFrame({"ticker": tick, "name": "", "mcap": np.nan})
        uni_src = ("saved universe from an earlier scan" if len(cached) >= 100 else "built-in large-cap list") + \
                  " (Yahoo screener unavailable)"
        log(uni_src)
    if uni.empty:
        raise RuntimeError("universe_empty")
    step(4, f"universe:{len(uni)}")

    def dl_progress(done, total, usable):
        step(4 + int(56 * done / max(1, total)), f"download:{done}/{total}")

    period = {1: "1y", 2: "2y"}.get(a.history_years, "5y")
    data = source.bars(uni["ticker"].tolist(), log, progress=dl_progress, period=period)
    if not data:
        raise RuntimeError("no_data")
    last_bar = max(df.index[-1] for df in data.values()).date()
    data = {t: df for t, df in data.items() if df.index[-1].date() == last_bar}
    partial = bool(not source.demo and ny and ny.date() == last_bar and ny.weekday() < 5
                   and (ny.hour, ny.minute) < (16, 0))
    entry_date = trading_day(last_bar, 1)
    exit_date = trading_day(entry_date, a.hold_days - 1)

    step(61, "indicators")
    inds = {}
    study = GapStudy()
    for t, df in data.items():
        try:
            ind = Ind(df)
            inds[t] = ind
            study.add(ind)
        except Exception as e:
            log(f"skip {t}: {type(e).__name__}")
    study.finish()

    rs_raw = {}
    for t, ind in inds.items():
        c = ind.c
        if len(c) > 253:
            rs_raw[t] = 0.4 * (c[-1] / c[-64] - 1) + 0.2 * (c[-1] / c[-127] - 1) + 0.2 * (c[-1] / c[-190] - 1) + 0.2 * (c[-1] / c[-253] - 1)
    rs_rank = {}
    if rs_raw:
        ser = pd.Series(rs_raw).rank(pct=True)
        rs_rank = {t: int(max(1, min(99, round(v * 99)))) for t, v in ser.items()}
    breadth = None
    if inds:
        arr = lambda f: np.array([f(ind) for ind in inds.values()], dtype=float)
        last = arr(lambda d: d.c[-1]); prev = arr(lambda d: d.c[-2])
        breadth = {"n": len(inds),
                   "above50": float(np.nanmean(last > arr(lambda d: d.s50[-1]))),
                   "above150": float(np.nanmean(last > arr(lambda d: d.s150[-1]))),
                   "rsi_gt50": float(np.nanmean(arr(lambda d: d.r[-1]) > 50)),
                   "adv": int((last > prev).sum()), "dec": int((last < prev).sum()),
                   "new_highs": int((arr(lambda d: d.h[-1]) >= arr(lambda d: d.h[-252:].max())).sum()),
                   "new_lows": int((arr(lambda d: d.l[-1]) <= arr(lambda d: d.l[-252:].min())).sum())}

    step(66, "stage1")
    names = dict(zip(uni["ticker"], uni["name"]))
    enabled = {"continuation": a.use_continuation, "gapfill": a.use_gapfill, "quality": a.use_quality}
    rows = {s: [] for s in STRATS}
    for t, ind in inds.items():
        try:
            if enabled["continuation"]:
                rows["continuation"].append(analyze_cont(t, ind, a))
            if enabled["gapfill"]:
                rows["gapfill"].append(analyze_gapfill(t, ind, a, study))
            if enabled["quality"]:
                rows["quality"].append(analyze_quality_price(t, ind, a))
        except Exception as e:
            log(f"analyze {t}: {type(e).__name__}: {e}")
    for s in STRATS:
        for r in rows[s]:
            r["name"] = names.get(r["ticker"], "") or ""
            r["key"] = f"{s}:{r['ticker']}"
            r["rs_rank"] = rs_rank.get(r["ticker"])

    bt = None
    if a.use_backtest:
        import backtest
        step(67, "backtest")
        try:
            bt = backtest.run(inds, a, lambda k, n: step(67 + int(3 * k / max(1, n)), f"backtest:{k}/{n}"))
        except Cancelled:
            raise
        except Exception as e:
            log(f"backtest: {type(e).__name__}: {e}")

    # opportunities: everything that fell hard or gapped down
    opp_rows, opp_base, opp_deep, spy_close = [], None, [], None
    if a.use_opps:
        import opps
        step(70, "opps")
        try:
            spy_df = source.bars(["SPY"], log, period=period, batch=1, min_bars=150).get("SPY")
            spy_close = spy_df["Close"] if spy_df is not None else None
        except Exception as e:
            log(f"spy: {e}")
        try:
            opp_base = opps.backtest(inds, a, lambda k, n: step(70 + int(4 * k / max(1, n)), f"opps_bt:{k}/{n}"))
        except Cancelled:
            raise
        except Exception as e:
            log(f"opp backtest: {type(e).__name__}: {e}")
        for t, ind in inds.items():
            try:
                r = opps.analyze(t, ind, a, opp_base, study, spy_close)
            except Exception as e:
                log(f"opp {t}: {type(e).__name__}: {e}")
                continue
            if r:
                r["name"] = names.get(t, "") or ""
                r["rs_rank"] = rs_rank.get(t)
                r["held"] = t in held
                opp_rows.append(r)
        opp_rows.sort(key=lambda r: -r["score"])
        opp_deep = [r["ticker"] for r in opp_rows[:a.opp_max]]

    # quality: fundamentals for the price-qualified names (capped)
    q_price = sorted([r for r in rows["quality"] if r["passed"] and r["ticker"] not in held],
                     key=lambda r: (-r["stab_n"], r["dd_pct"]))
    q_meta, q_skipped = q_price[:QUALITY_MAX_META], q_price[QUALITY_MAX_META:]
    for r in q_skipped:
        r["checks"]["q_quality"] = False
        r["fails"], r["passed"], r["fund_skipped"] = ["q_quality"], False, True

    passed = [r for s in ("continuation", "gapfill") for r in rows[s] if r["passed"] and r["ticker"] not in held]
    need_info = sorted({r["ticker"] for r in passed} | {r["ticker"] for r in q_meta} | set(opp_deep))
    step(75, f"stage2:0/{len(need_info)}")
    infos = parallel(lambda t: retry(lambda: source.info(t), log, f"info {t}") or {}, need_info,
                     lambda k, n: step(75 + int(8 * k / max(1, n)), f"stage2:{k}/{n}"))
    for r in q_meta:
        inf = infos.get(r["ticker"]) or {}
        apply_fundamentals(r, inf.get("fund"), a)
    passed += [r for r in q_meta if r["passed"]]
    need_earn = sorted({r["ticker"] for r in passed} | set(opp_deep))
    earn = parallel(lambda t: retry(lambda: source.earnings(t), log, f"earnings {t}") or [], need_earn,
                    lambda k, n: step(83 + int(9 * k / max(1, n)), f"earn:{k}/{n}"))
    for r in passed:
        inf = infos.get(r["ticker"]) or {}
        r["sector"], r["industry"] = inf.get("sector", ""), inf.get("industry", "")
        r["name"] = r["name"] or inf.get("name", "")
        ed = earn.get(r["ticker"]) or []
        r["earnings_dates"] = ed[-4:]
        r["earnings_known"] = bool(ed)
        r["earn_in_window"] = [d for d in ed if entry_date <= d <= exit_date]
        r["earn_before_entry"] = [d for d in ed if last_bar <= d < entry_date]
        if r.get("gap_date"):
            r["catalyst"] = "earnings" if any(abs((d - r["gap_date"]).days) <= 1 for d in ed) else "other"
        if a.custom_tickers.strip():
            mc = (inf.get("fund") or {}).get("mcap")
            if mc and mc < a.min_mcap:
                r["fails"].append("mcap"); r["passed"] = False
    passed = [r for r in passed if r["passed"]]

    step(93, "stage3")
    plans = []
    for r in passed:
        pl = build_plan(r, inds[r["ticker"]], a, per_trade, entry_date, exit_date)
        b = (bt or {}).get(r["strategy"]) or {}
        if b.get("trades", 0) >= 50 and (b.get("avg_r") or 0) < 0:
            pl["flags"].append({"code": "strategy_negative", "v": round(b["avg_r"], 2), "n": b["trades"]})
        pl["score"] = score(r, pl)
        pl["name"] = r["name"]
        r["score"] = pl["score"]
        plans.append(pl)
    rows_by_key = {r["key"]: r for r in passed}
    chosen, sel_notes = select(plans, rows_by_key, data, slots, a.corr_max)
    chosen_k = {p["key"] for p in chosen}
    for pl in plans:
        pl["selected"] = pl["key"] in chosen_k
        pl["not_selected"] = sel_notes.get(pl["key"])

    ready = []
    for p in sorted(plans, key=lambda p: -p["score"]):
        if p["ok"] and p["ticker"] not in ready:
            ready.append(p["ticker"])
    ready = ready[:10]
    corr = None
    if len(ready) >= 2:
        cm = pd.concat({t: data[t]["Close"].pct_change() for t in ready}, axis=1).iloc[-60:].corr()
        corr = {"tickers": ready, "matrix": [[float(cm.loc[x, y]) for y in ready] for x in ready]}

    step(95, "market")
    try:
        market = fetch_market(source, log)
    except Exception as e:
        log(f"market: {e}")
        market = {"rows": [], "regime": []}

    ev_window, ev_before = [], []
    for e in events or []:
        try:
            d = pd.Timestamp(e["date"]).date()
        except Exception:
            continue
        if entry_date <= d <= exit_date:
            ev_window.append(e)
        elif last_bar <= d < entry_date:
            ev_before.append(e)

    fail_counts = {s: {} for s in STRATS}
    near = {s: [] for s in STRATS}
    primary = {"continuation": ("gap_volume", "gap_unfilled"), "gapfill": ("gf_gap",), "quality": ("q_drawdown",)}
    for s in STRATS:
        for r in rows[s]:
            if r["fails"]:
                fail_counts[s][r["fails"][0]] = fail_counts[s].get(r["fails"][0], 0) + 1
                if not any(k in r["fails"] for k in primary[s]) and "liquidity_50d" not in r["fails"]:
                    near[s].append(r)
        near[s] = sorted(near[s], key=lambda r: len(r["fails"]))[:40]

    opp_out = None
    if a.use_opps:
        import opps
        spy_row = next((x for x in (market.get("rows") or []) if x.get("ticker") == "SPY"), {})
        market_weak = bool(spy_row and (spy_row.get("vs_sma150") or 0) < 0)
        deep = set(opp_deep)
        max_exp = a.capital / max(1, a.max_trades)
        risk_each = per_trade if slots else a.risk_per_trade
        for r in opp_rows:
            inf = infos.get(r["ticker"]) or {}
            if r["ticker"] in deep:
                r["sector"], r["industry"] = inf.get("sector", ""), inf.get("industry", "")
                r["name"] = r["name"] or inf.get("name", "")
            try:
                opps.finalize(r, inds[r["ticker"]], a, inf.get("fund") if r["ticker"] in deep else None,
                              earn.get(r["ticker"]) if r["ticker"] in deep else None, entry_date, last_bar,
                              risk_each, max_exp, market_weak)
            except Exception as e:
                log(f"opp finalize {r['ticker']}: {type(e).__name__}: {e}")
                r["status"], r["why"], r["risks"] = "risky", [], [{"code": "error"}]
        opp_rows.sort(key=lambda r: (-{"ready": 2, "watch": 1, "risky": 0}.get(r.get("status"), 0), -r["score"]))
        counts = {k: sum(1 for r in opp_rows if r.get("status") == k) for k in ("ready", "watch", "risky")}
        opp_out = {"rows": opp_rows[:200], "total": len(opp_rows), "counts": counts, "deep_n": len(deep),
                   "base": opp_base.summary() if opp_base is not None else None,
                   "params": {k: getattr(a, k) for k in DEFAULTS if k.startswith("opp_")},
                   "market_weak": market_weak}

    charts = {}
    for r in (opp_out["rows"][:40] if opp_out else []):
        charts[r["ticker"]] = chart_series(data[r["ticker"]])
    for r in passed + [r for s in STRATS for r in near[s][:12]]:
        if r["ticker"] not in charts:
            charts[r["ticker"]] = chart_series(data[r["ticker"]])
    frozen = sorted({r["ticker"] for s in STRATS for r in rows[s] if r.get("frozen") and r.get("dir")})

    step(99, "saving")
    il_now = il or pulled_utc
    cands = {s: [r for r in passed if r["strategy"] == s] for s in STRATS}
    result = {
        "id": il_now.strftime("%Y%m%d_%H%M%S"), "demo": bool(source.demo), "source": source.name,
        "universe_source": uni_src, "pulled_utc": pulled_utc, "pulled_il": il, "pulled_ny": ny,
        "universe_tickers": sorted(data.keys()),
        "runtime_s": round(time.time() - t0, 1), "last_bar": last_bar, "partial": partial,
        "universe_n": int(len(uni)), "usable_n": len(data), "entry_date": entry_date, "exit_date": exit_date,
        "history": period, "settings": {k: getattr(a, k) for k in DEFAULTS if not k.startswith(("tg_", "cloud_", "ntfy_", "remote_"))},
        "enabled": enabled,
        "budget": {"open_risk": open_risk, "remaining": budget, "slots": slots, "per_trade": per_trade,
                   "open_positions": len(open_pos), "max_exposure": a.capital / max(1, a.max_trades)},
        "market": market, "events_window": ev_window, "events_before_entry": ev_before,
        "candidates": cands, "plans": plans, "selected": [p["key"] for p in chosen], "corr": corr,
        "fail_counts": fail_counts, "near": near, "frozen": frozen, "quality_skipped": len(q_skipped),
        "gap_stats": study.summary(), "charts": charts, "log": logs[-60:],
        "backtest": bt, "breadth": breadth, "opps": opp_out,
    }
    return clean(result)


# --------------------------------------------------------------------------- report
FLAG_TEXT = {
    "wide_stop": "wide stop ({v} ATR)", "tp_far": "TP needs {v} ATR",
    "high52_between": "52w high {v} between entry and TP", "trigger_far": "trigger {v}% above close",
    "qty_capped": "qty capped by ${v} exposure", "gapfill_below_tp": "gap fill {v} below TP (R:R to fill {rr})",
    "qty_zero": "qty 0", "frozen": "possible cash-deal frozen price", "earnings_in_window": "earnings in window {v}",
    "earnings_unknown": "earnings date unknown", "earnings_before_entry": "reports before entry {v}",
    "earnings_gap_drift": "earnings gap: drift risk", "below_sma150": "below SMA150",
    "no_slots": "no free slot", "same_industry": "same industry as {v}", "high_corr": "corr {corr} with {v}",
    "same_ticker": "ticker already selected by another strategy",
    "tp_unrealistic": "TP needs {v} ATR in {days} days - unrealistic",
    "strategy_negative": "strategy lost in backtest (avg {v}R, N={n})",
}
STRAT_NAME = {"continuation": "Gap continuation", "gapfill": "Gap fill (down gaps)", "quality": "Quality pullback"}


def flag_text(f):
    if not f:
        return ""
    try:
        return FLAG_TEXT.get(f["code"], f["code"]).format(**{k: (", ".join(v) if isinstance(v, list) else v)
                                                              for k, v in f.items()})
    except Exception:
        return f.get("code", "")


def _f(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return str(x).replace("|", "/")


def _table(rows, cols):
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(h for h, _, _ in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_f(fn(r) if callable(fn) else r.get(fn), nd) for _, fn, nd in cols) + " |")
    return "\n".join(out) + "\n"


def _pct(x):
    return None if x is None else round(100 * x, 1)


def report_md(res):
    L = [f"# SwingDesk scan - {res['last_bar']}" + (" (DEMO DATA)" if res.get("demo") else ""), "",
         "## 0. Data", "",
         f"- Pulled: {res['pulled_utc'][:16].replace('T', ' ')} UTC | {(res.get('pulled_il') or '')[:16].replace('T', ' ')} Israel | "
         f"{(res.get('pulled_ny') or '')[:16].replace('T', ' ')} New York",
         f"- Last daily bar: {res['last_bar']}" + (" (PARTIAL session)" if res["partial"] else " (complete)"),
         f"- Source: {res['source']}; universe: {res['universe_source']} ({res['universe_n']}, usable {res['usable_n']}); history {res.get('history')}",
         f"- Entry {res['entry_date']} | time exit {res['exit_date']}", "", "## 1. Market", ""]
    L.append(_table(res["market"]["rows"], [("Ticker", "ticker", 0), ("Close", "close", 2), ("1d %", "chg_1d", 2),
                                            ("5d %", "chg_5d", 2), ("vs SMA50 %", "vs_sma50", 1),
                                            ("vs SMA150 %", "vs_sma150", 1), ("RSI", "rsi", 1)]))
    L.append("Events in window: " + (", ".join(f"{e['date']} {e['title']}" for e in res["events_window"]) or "none"))
    L.append("Events before entry: " + (", ".join(f"{e['date']} {e['title']}" for e in res["events_before_entry"]) or "none"))
    b = res["budget"]
    L += ["", "## 2. Risk budget", "",
          f"- Open risk ${b['open_risk']:,.2f} | remaining ${b['remaining']:,.2f} | slots {b['slots']} | per trade ${b['per_trade']:,.2f}", ""]
    c = res["candidates"]
    earn = ("Earnings", lambda r: ", ".join(r.get("earnings_dates") or []) or "unknown", 0)
    L += [f"## 3a. {STRAT_NAME['continuation']} ({len(c['continuation'])})", ""]
    L.append(_table(c["continuation"], [
        ("Ticker", "ticker", 0), ("Name", "name", 0), ("Setup", "setup", 0), ("Gap date", "gap_date", 0),
        ("Gap %", "gap_pct", 2), ("Vol x", "vol_ratio", 2), ("Close", "close", 2), ("SMA50", "sma50", 2),
        ("SMA150", "sma150", 2), ("RSI", "rsi", 1), ("MACD", "macd_state", 0), ("Pattern", "pattern", 0),
        ("Industry", "industry", 0), ("Catalyst", "catalyst", 0), earn]))
    L += [f"## 3b. {STRAT_NAME['gapfill']} ({len(c['gapfill'])})", ""]
    L.append(_table(c["gapfill"], [
        ("Ticker", "ticker", 0), ("Name", "name", 0), ("Gap date", "gap_date", 0), ("Gap %", "gap_pct", 2),
        ("Age", "age", 0), ("Fill level", "fill_level", 2), ("To fill %", "to_fill_pct", 1),
        ("P(fill next 5d)", lambda r: _pct((r.get("fill_prob") or {}).get("p")), 1),
        ("N", lambda r: (r.get("fill_prob") or {}).get("n"), 0), ("Uptrend", "long_trend", 0),
        ("RSI", "rsi", 1), ("Catalyst", "catalyst", 0), earn]))
    L += [f"## 3c. {STRAT_NAME['quality']} ({len(c['quality'])})", ""]
    L.append(_table(c["quality"], [
        ("Ticker", "ticker", 0), ("Name", "name", 0), ("From high %", "dd_pct", 1), ("Quality", "qual_n", 0),
        ("Stab /5", "stab_n", 0), ("ROE %", lambda r: _pct((r.get("fund") or {}).get("roe")), 1),
        ("Op margin %", lambda r: _pct((r.get("fund") or {}).get("op_margin")), 1),
        ("Rev growth %", lambda r: _pct((r.get("fund") or {}).get("rev_growth")), 1),
        ("Fwd P/E", lambda r: (r.get("fund") or {}).get("fpe"), 1), ("P/FCF", "p_fcf", 1),
        ("To prior high %", "to_high_pct", 1), ("To analyst target %", "to_target_pct", 1),
        ("Industry", "industry", 0), earn]))
    L += ["## 4. Plans", ""]
    L.append(_table(sorted(res["plans"], key=lambda p: -p["score"]), [
        ("Sel", lambda p: "YES" if p["selected"] else "", 0), ("Ticker", "ticker", 0), ("Strategy", "strategy", 0),
        ("Score", "score", 1), ("Entry", "entry", 2), ("SL", "sl", 2), ("TP", "tp", 2), ("Qty", "qty", 0),
        ("Exposure", "exposure", 0), ("Risk $", "risk_usd", 2), ("Reward $", "reward_usd", 2),
        ("Cancel >", "cancel_above", 2), ("Time exit", "time_exit", 0),
        ("Flags", lambda p: "; ".join(flag_text(f) for f in p["flags"]), 0),
        ("Disqualified", lambda p: "; ".join(flag_text(f) for f in p["blocking"]), 0),
        ("Not selected", lambda p: flag_text(p.get("not_selected")), 0)]))
    gs = res.get("gap_stats") or {}
    L += ["## 5. Gap statistics (this universe, history " + str(res.get("history")) + f", {gs.get('n_events', 0)} gaps)", ""]
    L.append(_table([r for r in gs.get("fill", []) if r["n"]], [
        ("Dir", "dir", 0), ("Size", "size", 0), ("Uptrend", "trend", 0), ("N", "n", 0),
        ("Fill<=5d %", lambda r: _pct(r["p5"]), 1), ("Fill<=10d %", lambda r: _pct(r["p10"]), 1),
        ("Fill<=20d %", lambda r: _pct(r["p20"]), 1)]))
    if gs.get("cont"):
        cc = gs["cont"]
        L.append(f"Up gaps in uptrend, vol>=1.5x, still open on day 5: next 5 sessions median {100 * cc['median']:.2f}%, "
                 f"positive {100 * cc['win']:.0f}% (N={cc['n']})\n")
    op = res.get("opps")
    if op:
        L += [f"## 5b. Opportunities (ready {op['counts']['ready']}, watch {op['counts']['watch']}, risky {op['counts']['risky']}; "
              f"fundamentals checked for top {op['deep_n']})", ""]
        L.append(_table(op["rows"][:30], [
            ("Ticker", "ticker", 0), ("Status", "status", 0), ("Score", "score", 1), ("Tags", lambda r: ",".join(r["tags"]), 0),
            ("From", "high", 2), ("High date", "high_date", 0), ("Now", "close", 2), ("Drop %", "dd_pct", 1),
            ("Low", "low", 2), ("Entry", lambda r: r["plan"]["entry"], 2), ("Stop", lambda r: r["plan"]["stop"], 2),
            ("Risk %", "risk_pct", 1), ("T1", lambda r: r["plan"]["t1"], 2), ("T2", lambda r: r["plan"]["t2"], 2),
            ("R:R2", lambda r: r["plan"]["rr2"], 2), ("Stab", "stab_n", 0),
            ("Quality", lambda r: f"{r.get('qual_n', 0)}/{r.get('qual_avail', 0)}", 0),
            ("Base avg R", lambda r: (r.get("base") or {}).get("avg_r"), 2), ("Base N", lambda r: (r.get("base") or {}).get("n"), 0),
            ("Next earnings", "next_earnings", 0), ("SPY since high %", "spy_since_high", 1)]))
        ob = op.get("base") or {}
        if ob.get("n"):
            c1, c2 = ob.get("confirmed") or {}, ob.get("early") or {}
            L.append(f"Opportunity base rate (T1 exit, {res['settings'].get('opp_hold')} sessions max): confirmed "
                     f"avg {c1.get('avg_r', 0):.3f}R win {100 * (c1.get('win') or 0):.0f}% N={c1.get('n', 0)} | early "
                     f"avg {c2.get('avg_r', 0):.3f}R win {100 * (c2.get('win') or 0):.0f}% N={c2.get('n', 0)}\n")
    bt = res.get("backtest") or {}
    if bt:
        p = bt.get("params", {})
        L += [f"## 6. Backtest ({p.get('first')} to {p.get('last')}, cost {p.get('cost_r')}R/trade, no earnings filter)", ""]
        L.append(_table([dict(b, strategy=STRAT_NAME[s]) for s, b in bt.items() if s in STRAT_NAME], [
            ("Strategy", "strategy", 0), ("Signals", "signals", 0), ("Trades", "trades", 0),
            ("Win %", lambda b: _pct(b.get("win")), 1), ("Avg R", "avg_r", 3), ("Median R", "median_r", 3),
            ("PF", "pf", 2), ("Last 6m avg R", "recent_avg_r", 3), ("Cancelled", "cancelled", 0),
            ("No fill", "nofill", 0), ("Blocked", "blocked", 0)]))
    br = res.get("breadth")
    if br:
        L.append(f"Breadth: {100 * br['above50']:.0f}% above SMA50, {100 * br['above150']:.0f}% above SMA150, "
                 f"adv/dec {br['adv']}/{br['dec']}, 52w highs/lows {br['new_highs']}/{br['new_lows']}\n")
    L += ["## 7. Rejected", ""]
    for s in STRATS:
        fc = res["fail_counts"].get(s, {})
        L.append(f"{STRAT_NAME[s]}: " + ", ".join(f"{k} {v}" for k, v in sorted(fc.items(), key=lambda kv: -kv[1])))
    if res["frozen"]:
        L.append("Possible frozen (cash deal): " + ", ".join(res["frozen"]))
    return "\n".join(L) + "\n"

# -*- coding: utf-8 -*-
"""
Opportunities: stocks that fell hard or gapped down, ranked by how little you risk against how
much potential there is, whether the selling has stopped, how healthy the business is, what
caused the drop, and how the same setup behaved in this universe's own history.

Plan (same in the live scan and in the base-rate backtest):
  entry   Buy Stop just above today's high (the stock has to prove itself first)
  stop    below the lowest low of the last opp_stop_bars sessions, at least opp_min_stop_atr ATR
  T1      the nearest resistance (38.2% / 50% / 61.8% retracement of the drop, SMA50, SMA200,
          gap fill, prior high) that pays at least 1R
  T2      61.8% retracement or gap fill above T1 (full mean reversion)
  exit    T1, stop, or time exit after opp_hold sessions (base rate uses T1 only: conservative)
"""
import numpy as np
import pandas as pd

from backtest import gap_list, _shift

WARMUP = 200
DD_BUCKETS = [(0, 15, "<15%"), (15, 25, "15-25%"), (25, 40, "25-40%"), (40, 1e9, "40%+")]
STAB_KEYS = ["s_rsi", "s_ema", "s_hl", "s_macd", "s_upvol"]
TAGS = ["drop", "gap", "oversold", "capitulation", "support200", "reversal"]


def dd_bucket(dd):
    x = -dd
    for lo, hi, lab in DD_BUCKETS:
        if lo <= x < hi:
            return lab
    return DD_BUCKETS[0][2]


def _roll(a, w, fn):
    return getattr(pd.Series(a).rolling(w, min_periods=w), fn)().to_numpy()


def _f(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- features
def arrays(ind, a):
    o, h, l, c, v = ind.o, ind.h, ind.l, ind.c, ind.v
    with np.errstate(invalid="ignore", divide="ignore"):
        avgv = _roll(v, 50, "mean")
        hiL = _roll(h, a.opp_lookback, "max")
        dd = (c / hiL - 1) * 100
        r = ind.r
        lmin10 = _roll(l, 10, "min")
        hist, hist1, hist2 = ind.hist, _shift(ind.hist, 1), _shift(ind.hist, 2)
        up = np.r_[False, c[1:] > c[:-1]]
        vu = pd.Series(np.where(up, v, 0.0)).rolling(10).sum().to_numpy()
        vd = pd.Series(np.where(~up, v, 0.0)).rolling(10).sum().to_numpy()
        out = {
            "s_rsi": (_roll(r, 15, "min") < 35) & (r > 40),
            "s_ema": c > ind.e10,
            "s_hl": lmin10 > _shift(lmin10, 10),
            "s_macd": (ind.m > ind.sg) | ((hist > hist1) & (hist1 > hist2)),
            "s_upvol": vu > vd,
        }
        out["stab"] = sum(out[k].astype(int) for k in STAB_KEYS)
        rng = h - l
        pos = np.where(rng > 0, (c - l) / np.where(rng > 0, rng, 1), np.nan)
        prev_c = _shift(c, 1)
        cap_day = (v >= 2.5 * _shift(avgv, 1)) & (c < prev_c) & (pos >= 0.5)
        sma200 = _roll(c, 200, "mean")
        prev_low10 = _shift(lmin10, 1)
        hammer = (l < prev_low10) & (pos >= 0.6)
        engulf = (c > o) & (_shift(c, 1) < _shift(o, 1)) & (o <= _shift(c, 1)) & (c >= _shift(o, 1))
        out.update(
            avgv=avgv, liq=avgv >= a.min_avgvol, hiL=hiL, dd=dd, sma200=sma200,
            sma200_rising=sma200 > _shift(sma200, 20),
            oversold=_roll(r, 5, "min") < 30,
            capitulation=_roll(cap_day.astype(float), 10, "max") > 0,
            support200=(np.abs(c / sma200 - 1) <= 0.03) & (sma200 > _shift(sma200, 20)),
            reversal=hammer | engulf,
            new_low=l <= lmin10,
            lmin_stop=_roll(l, a.opp_stop_bars, "min"),
        )
    return out


def open_down_gap(gaps, t, a):
    best = None
    for g, up, size, vr, f, level in gaps:
        if g > t:
            break
        if up or t - g >= a.opp_gap_days or -size < a.opp_min_gap or f <= t - g:
            continue
        best = (g, size, vr, level)
    return best


def levels_at(ind, A, t, a, gap):
    h, l = ind.h, ind.l
    s = max(0, t - a.opp_lookback + 1)
    k = s + int(np.argmax(h[s:t + 1]))
    hi = float(h[k])
    j = k + int(np.argmin(l[k:t + 1]))
    lo = float(l[j])
    span = hi - lo
    lv = {"r382": lo + 0.382 * span, "r50": lo + 0.5 * span, "r618": lo + 0.618 * span,
          "sma50": float(ind.s50[t]), "sma200": float(A["sma200"][t]), "high": hi}
    if gap is not None:
        lv["gap"] = float(gap[3])
    return k, hi, j, lo, lv


def plan_at(ind, A, t, a, lv):
    at = float(ind.atr[t])
    if not at or at != at:
        return None
    hi_t = float(ind.h[t])
    entry = round(hi_t + max(0.02, round(0.001 * hi_t, 2)), 2)
    stop = round(min(float(A["lmin_stop"][t]) - 0.1 * at, entry - a.opp_min_stop_atr * at), 2)
    risk = entry - stop
    if risk <= 0 or stop <= 0:
        return None
    above = sorted((v, k) for k, v in lv.items() if v == v and v > entry * 1.003)
    t1 = next(((v, k) for v, k in above if (v - entry) / risk >= 1.0), None)
    t2 = None
    if t1:
        mr = [(v, k) for v, k in above if v > t1[0] and k in ("r618", "gap")]
        rest = [(v, k) for v, k in above if v > t1[0] and k != "high"]
        t2 = max(mr) if mr else (rest[0] if rest else None)
    p = {"entry": entry, "stop": stop, "risk": round(risk, 4), "atr": at,
         "t1": round(t1[0], 2) if t1 else None, "t1_kind": t1[1] if t1 else None,
         "t2": round(t2[0], 2) if t2 else None, "t2_kind": t2[1] if t2 else None}
    p["rr1"] = (p["t1"] - entry) / risk if t1 else None
    p["rr2"] = (p["t2"] - entry) / risk if t2 else p["rr1"]
    return p


# --------------------------------------------------------------------------- base rates
def simulate(ind, t, p, a, cost):
    o, h, l, c = ind.o, ind.h, ind.l, ind.c
    d, last = t + 1, t + a.opp_hold
    if last >= len(c):
        return None
    entry, stop, tp = p["entry"], p["stop"], p["t1"]
    if o[d] > entry * (1 + a.cancel_gap_pct / 100):
        return ("cancelled",)
    if h[d] < entry:
        return ("nofill",)
    fill = max(entry, o[d])
    risk = fill - stop
    if risk <= 0 or tp <= fill:
        return ("blocked",)
    mfe = (h[d:last + 1].max() / fill - 1) * 100
    mae = (l[d:last + 1].min() / fill - 1) * 100
    for k in range(d, last + 1):
        if l[k] <= stop:
            px = stop if k == d else min(stop, o[k])
            return ("trade", (px - fill) / risk - cost, k, mfe, mae)
        if h[k] >= tp:
            px = tp if k == d else max(tp, o[k])
            return ("trade", (px - fill) / risk - cost, k, mfe, mae)
    return ("trade", (c[last] - fill) / risk - cost, last, mfe, mae)


class BaseRates:
    COLS = ["ddb", "gap", "stab", "above200", "r", "mfe", "mae", "hold"]

    def __init__(self, recs, counts):
        self.df = pd.DataFrame(recs, columns=self.COLS)
        self.counts = counts

    def _stats(self, m, basis):
        d = self.df[m] if m is not None else self.df
        if not len(d):
            return {"n": 0, "basis": basis}
        return {"n": int(len(d)), "win": float((d.r > 0).mean()), "avg_r": float(d.r.mean()),
                "median_r": float(d.r.median()), "mfe": float(d.mfe.median()), "mae": float(d.mae.median()),
                "mfe75": float(d.mfe.quantile(0.75)), "hold": float(d.hold.mean()), "basis": basis}

    def lookup(self, ddb, gap, stab, above200, min_n=30):
        df = self.df
        if df.empty:
            return {"n": 0, "basis": "none"}
        tries = [((df.ddb == ddb) & (df.gap == gap) & (df.stab == stab) & (df.above200 == above200), "full"),
                 ((df.ddb == ddb) & (df.gap == gap) & (df.stab == stab), "no_trend"),
                 ((df.gap == gap) & (df.stab == stab), "type"),
                 ((df.stab == stab), "stab")]
        for m, b in tries:
            if int(m.sum()) >= min_n:
                return self._stats(m, b)
        return self._stats(tries[-1][0], "stab")

    def summary(self):
        df = self.df
        out = {"n": int(len(df)), "counts": self.counts, "overall": self._stats(None, "all"),
               "confirmed": self._stats(df.stab == True, "stab") if len(df) else {"n": 0},  # noqa: E712
               "early": self._stats(df.stab == False, "stab") if len(df) else {"n": 0},  # noqa: E712
               "table": []}
        if df.empty:
            return out
        for _, _, lab in DD_BUCKETS:
            for gap in (False, True):
                for st in (True, False):
                    m = (df.ddb == lab) & (df.gap == gap) & (df.stab == st)
                    if int(m.sum()):
                        out["table"].append(dict(self._stats(m, "cell"), ddb=lab, gap=gap, stab=st))
        for ab in (True, False):
            out["above200_" + ("yes" if ab else "no")] = self._stats((df.stab == True) & (df.above200 == ab), "trend")  # noqa: E712
        return out


def backtest(inds, a, progress=None):
    cost = float(getattr(a, "bt_cost_r", 0.05))
    recs, counts = [], {"signals": 0, "cancelled": 0, "nofill": 0, "blocked": 0}
    items = list(inds.items())
    for idx, (ticker, ind) in enumerate(items):
        n = len(ind.c)
        if n < WARMUP + a.opp_hold + 5:
            continue
        try:
            A = arrays(ind, a)
            gaps = gap_list(ind)
        except Exception:
            continue
        ok = A["liq"] & (ind.c >= a.opp_min_price)
        days = set(np.flatnonzero(ok & (A["dd"] <= -a.opp_min_drop)).tolist())
        for g, up, size, vr, f, level in gaps:
            if up or -size < a.opp_min_gap:
                continue
            for t in range(g, min(n, g + a.opp_gap_days)):
                if f <= t - g:
                    break
                if ok[t]:
                    days.add(t)
        busy = -1
        for t in sorted(days):
            if t < WARMUP or t <= busy or t + a.opp_hold >= n:
                continue
            gap = open_down_gap(gaps, t, a)
            _, _, _, _, lv = levels_at(ind, A, t, a, gap)
            p = plan_at(ind, A, t, a, lv)
            if not p or p["t1"] is None:
                continue
            counts["signals"] += 1
            out = simulate(ind, t, p, a, cost)
            if out is None:
                continue
            if out[0] != "trade":
                counts[out[0]] += 1
                continue
            _, r, e, mfe, mae = out
            busy = e
            recs.append((dd_bucket(A["dd"][t]), gap is not None, bool(A["stab"][t] >= a.opp_min_stab),
                         bool(ind.c[t] > A["sma200"][t]), float(r), float(mfe), float(mae), int(e - t)))
        if progress and idx % 50 == 0:
            progress(idx + 1, len(items))
    return BaseRates(recs, counts)


# --------------------------------------------------------------------------- live analysis
def analyze(ticker, ind, a, base, study, spy_close):
    c = ind.c
    t = len(c) - 1
    if t < WARMUP:
        return None
    A = arrays(ind, a)
    if not (A["liq"][t] and c[t] >= a.opp_min_price):
        return None
    gaps = gap_list(ind)
    gap = open_down_gap(gaps, t, a)
    dd = float(A["dd"][t])
    is_drop = dd <= -a.opp_min_drop
    if not (is_drop or gap):
        return None
    k, hi, j, lo, lv = levels_at(ind, A, t, a, gap)
    p = plan_at(ind, A, t, a, lv)
    if p is None:
        return None
    dates = ind.df.index
    stab = {key: bool(A[key][t]) for key in STAB_KEYS}
    flags = {"drop": is_drop, "gap": gap is not None, "oversold": bool(A["oversold"][t]),
             "capitulation": bool(A["capitulation"][t]), "support200": bool(A["support200"][t]),
             "reversal": bool(A["reversal"][t])}
    row = {
        "ticker": ticker, "date": dates[t].date(), "close": float(c[t]), "chg_1d": (c[t] / c[t - 1] - 1) * 100,
        "high": hi, "high_date": dates[k].date(), "low": lo, "low_date": dates[j].date(),
        "dd_pct": dd, "max_dd_pct": (lo / hi - 1) * 100, "days_since_high": int(t - k), "days_since_low": int(t - j),
        "rebound_pct": (c[t] / lo - 1) * 100, "tags": [x for x in TAGS if flags[x]],
        "stab": stab, "stab_n": int(A["stab"][t]), "rsi": float(ind.r[t]), "atr": float(ind.atr[t]),
        "atr_pct": float(ind.atr[t] / c[t] * 100), "sma50": float(ind.s50[t]), "sma200": _f(A["sma200"][t]),
        "above200": bool(c[t] > A["sma200"][t]), "sma200_rising": bool(A["sma200_rising"][t]),
        "knife": bool(A["new_low"][t] and A["stab"][t] < 2),
        "ret_12m": (c[t] / c[t - 252] - 1) * 100 if t >= 252 else None,
        "levels": {key: round(v, 2) for key, v in lv.items() if v == v},
        "plan": p, "gap": None, "_g": None,
    }
    row["risk_pct"] = p["risk"] / p["entry"] * 100
    row["trigger_pct"] = (p["entry"] / c[t] - 1) * 100
    for key in ("t1", "t2"):
        row[f"to_{key}_pct"] = (p[key] / p["entry"] - 1) * 100 if p[key] else None
    row["to_high_pct"] = (hi / p["entry"] - 1) * 100
    if gap is not None:
        g, size, vr, level = gap
        row["_g"] = g
        row["gap"] = {"date": dates[g].date(), "pct": float(size), "vol_ratio": float(vr), "level": float(level),
                      "age": int(t - g), "to_fill_pct": (level / c[t] - 1) * 100}
        if study is not None:
            row["gap"]["prob"] = study.cond_fill(0, -size, 1 if c[g - 1] > ind.s150[g - 1] else 0, t - g, 10)
    if spy_close is not None and len(spy_close):
        try:
            s_hi = spy_close.asof(pd.Timestamp(row["high_date"]))
            s_now = spy_close.iloc[-1]
            row["spy_since_high"] = float((s_now / s_hi - 1) * 100) if s_hi == s_hi and s_hi else None
        except Exception:
            row["spy_since_high"] = None
    row["base"] = base.lookup(dd_bucket(dd), gap is not None, row["stab_n"] >= a.opp_min_stab, row["above200"]) \
        if base is not None else {"n": 0}
    row["score"] = score(row, a)
    return row


def qual_signals(f):
    f = f or {}
    roe, om, rg, fcf, de, eps, feps = (f.get(k) for k in ("roe", "op_margin", "rev_growth", "fcf", "de", "eps", "feps"))
    sig = {"f_roe": None if roe is None else roe >= 0.12, "f_margin": None if om is None else om >= 0.10,
           "f_growth": None if rg is None else rg > 0, "f_fcf": None if fcf is None else fcf > 0,
           "f_debt": None if de is None else de < 150,
           "f_eps": None if (eps is None or feps is None) else feps > eps}
    avail = [x for x in sig.values() if x is not None]
    return sig, sum(avail), len(avail)


def finalize(row, ind, a, fund, earnings, entry_date, last_bar, per_trade, max_exp, market_weak):
    """Add fundamentals, earnings context, sizing, score, status and the why / risks lists."""
    import engine  # local import: engine imports this module
    p = row["plan"]
    row["deep"] = fund is not None
    if fund is not None:
        sig, qn, qa = qual_signals(fund)
        row.update(fund=fund, qual=sig, qual_n=qn, qual_avail=qa)
        mcap, fcf = fund.get("mcap"), fund.get("fcf")
        row["p_fcf"] = mcap / fcf if (mcap and fcf and fcf > 0) else None
        row["to_target_pct"] = ((fund["target"] / p["entry"] - 1) * 100
                                if fund.get("target") and (fund.get("n_analysts") or 0) >= 3 else None)
    else:
        row.update(fund=None, qual=None, qual_n=0, qual_avail=0, p_fcf=None, to_target_pct=None)
    row["earnings_known"] = bool(earnings)
    nxt = [d for d in (earnings or []) if d > last_bar]
    row["next_earnings"] = min(nxt) if nxt else None
    row["earnings_sessions"] = (engine.sessions_between(last_bar, row["next_earnings"]) if row["next_earnings"] else None)
    drop_on_earn = None
    if earnings:
        dates = ind.df.index
        for d in earnings:
            if not (row["high_date"] < d <= last_bar):
                continue
            i = int(dates.searchsorted(pd.Timestamp(d)))
            for kk in (i, i + 1):
                if 1 <= kk < len(ind.c) and ind.c[kk] / ind.c[kk - 1] - 1 <= -0.05:
                    drop_on_earn = dates[kk].date()
                    break
            if drop_on_earn:
                break
    row["drop_on_earnings"] = drop_on_earn
    if row.get("gap") and earnings:
        row["gap"]["on_earnings"] = any(abs((d - row["gap"]["date"]).days) <= 1 for d in earnings)

    qty = int(per_trade // p["risk"]) if p["risk"] > 0 and per_trade > 0 else 0
    capped = qty * p["entry"] > max_exp
    if capped:
        qty = int(max_exp // p["entry"])
    row["size"] = {"qty": qty, "exposure": qty * p["entry"], "risk_usd": qty * p["risk"], "capped": capped,
                   "reward1": qty * (p["t1"] - p["entry"]) if p["t1"] else None,
                   "reward2": qty * (p["t2"] - p["entry"]) if p["t2"] else None, "per_trade": per_trade}
    p["entry_date"] = entry_date
    p["time_exit"] = engine.trading_day(entry_date, a.opp_hold - 1)
    p["cancel_above"] = round(p["entry"] * (1 + a.cancel_gap_pct / 100), 2)

    why, risks = [], []
    why.append({"code": "drop", "from": row["high"], "from_date": row["high_date"], "to": row["close"],
                "pct": row["dd_pct"], "days": row["days_since_high"], "low": row["low"], "low_date": row["low_date"],
                "rebound": row["rebound_pct"]})
    if row.get("spy_since_high") is not None:
        why.append({"code": "market", "spy": row["spy_since_high"], "stock": row["dd_pct"]})
    if row.get("gap"):
        why.append(dict({"code": "gap"}, **{k: v for k, v in row["gap"].items()}))
    if p["t1"]:
        why.append({"code": "asym", "risk_pct": row["risk_pct"], "t1": p["t1"], "t1_kind": p["t1_kind"],
                    "to_t1": row["to_t1_pct"], "t2": p["t2"], "t2_kind": p["t2_kind"], "to_t2": row["to_t2_pct"],
                    "rr1": p["rr1"], "rr2": p["rr2"], "to_high": row["to_high_pct"]})
    if row["stab_n"] >= a.opp_min_stab:
        why.append({"code": "stab", "n": row["stab_n"]})
    for tg in ("reversal", "capitulation", "oversold", "support200"):
        if tg in row["tags"]:
            why.append({"code": tg})
    if row["above200"] or row["sma200_rising"]:
        why.append({"code": "long_trend", "above": row["above200"], "rising": row["sma200_rising"]})
    if row["qual_avail"] >= 4 and row["qual_n"] / row["qual_avail"] >= 0.66:
        why.append({"code": "quality", "n": row["qual_n"], "avail": row["qual_avail"]})
    f = row.get("fund") or {}
    if f.get("fpe") and f["fpe"] > 0:
        why.append({"code": "valuation", "fpe": f.get("fpe"), "pe": f.get("pe"), "p_fcf": row.get("p_fcf")})
    if row.get("to_target_pct") is not None and row["to_target_pct"] >= 15:
        why.append({"code": "analyst", "upside": row["to_target_pct"], "n": f.get("n_analysts"), "rec": f.get("rec")})
    b = row.get("base") or {}
    if b.get("n", 0) >= 30:
        (why if (b.get("avg_r") or 0) > 0 else risks).append(dict({"code": "base"}, **b))

    if row["knife"]:
        risks.append({"code": "knife"})
    elif row["stab_n"] < a.opp_min_stab:
        risks.append({"code": "no_stab", "n": row["stab_n"], "need": a.opp_min_stab})
    es = row["earnings_sessions"]
    if es is not None and es <= 5:
        risks.append({"code": "earnings_soon", "date": row["next_earnings"], "sessions": es})
    elif es is not None and es <= a.opp_hold:
        risks.append({"code": "earnings_in_hold", "date": row["next_earnings"], "sessions": es})
    if drop_on_earn or (row.get("gap") or {}).get("on_earnings"):
        risks.append({"code": "earnings_drop", "date": drop_on_earn or row["gap"]["date"]})
    if not row["above200"] and not row["sma200_rising"]:
        risks.append({"code": "below200_falling"})
    if row["deep"] and row["qual_avail"] >= 4 and row["qual_n"] <= 2:
        risks.append({"code": "quality_weak", "n": row["qual_n"], "avail": row["qual_avail"]})
    if not row["deep"] or row["qual_avail"] < 4:
        risks.append({"code": "fund_unknown", "deep": row["deep"]})
    if f.get("de") and f["de"] >= 200:
        risks.append({"code": "debt", "de": f["de"]})
    if f.get("fcf") is not None and f["fcf"] < 0:
        risks.append({"code": "fcf_neg", "fcf": f["fcf"]})
    if row["risk_pct"] > a.opp_max_risk:
        risks.append({"code": "wide_risk", "pct": row["risk_pct"], "max": a.opp_max_risk})
    if not p["t1"]:
        risks.append({"code": "no_room"})
    if row["trigger_pct"] > 3:
        risks.append({"code": "trigger_far", "pct": row["trigger_pct"]})
    if f.get("short_pct") and f["short_pct"] >= 0.10:
        risks.append({"code": "short_high", "pct": f["short_pct"]})
    if market_weak:
        risks.append({"code": "market_weak"})
    if row["deep"] and not row["earnings_known"]:
        risks.append({"code": "earnings_unknown"})
    row["why"], row["risks"] = why, risks

    row["score"] = score(row, a)
    imminent = es is not None and es <= 3
    poor = row["deep"] and row["qual_avail"] >= 4 and row["qual_n"] <= 2
    bad_base = b.get("n", 0) >= 30 and (b.get("avg_r") or 0) < -0.1
    rr_best = p["rr2"] or 0
    if (p["t1"] and row["stab_n"] >= a.opp_min_stab and rr_best >= a.opp_min_rr
            and row["risk_pct"] <= a.opp_max_risk and not row["knife"] and not imminent and not poor):
        row["status"] = "ready"
    elif row["knife"] or imminent or not p["t1"] or row["risk_pct"] > a.opp_max_risk or poor or bad_base:
        row["status"] = "risky"
    else:
        row["status"] = "watch"
    return row


def score(row, a):
    p = row["plan"]
    s = 25 * min(max(p.get("rr2") or 0, 0), 5) / 5
    qa = row.get("qual_avail") or 0
    s += 25 * row["qual_n"] / qa if qa >= 4 else 12
    s += 20 * row["stab_n"] / 5
    b = row.get("base") or {}
    s += 15 * min(1.0, max(0.0, ((b.get("avg_r") or 0) + 0.3) / 0.9)) if b.get("n", 0) >= 30 else 7
    s += 4 if (row["above200"] or row["sma200_rising"]) else 0
    s += 3 if (row.get("ret_12m") or 0) > 0 else 0
    s += 3 if (row.get("to_target_pct") or 0) >= 20 else 0
    pen = 15 if row["knife"] else 0
    es = row.get("earnings_sessions")
    pen += 10 if (es is not None and es <= 5) else 0
    pen += 5 if (row.get("drop_on_earnings") and "reversal" not in row["tags"]) else 0
    pen += 5 if (not row["above200"] and not row["sma200_rising"]) else 0
    pen += 5 if row.get("risk_pct", 0) > a.opp_max_risk else 0
    pen += 10 if not p.get("t1") else 0
    return round(max(0.0, min(100.0, s - pen)), 1)

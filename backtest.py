# -*- coding: utf-8 -*-
"""
Walk-forward backtest of the three SwingDesk strategies on the downloaded daily bars.

For every past session t the price rules are evaluated exactly as in the live scan, then the
plan is simulated on the following sessions:
  - Buy Stop above the trigger, valid for the next session only
  - cancelled if that session opens more than cancel_gap_pct above the entry
  - filled at max(entry, open)
  - SL / TP checked bar by bar; a bar that touches both counts as a stop (conservative)
  - a gap through SL or TP fills at the open
  - time exit at the close of the last holding session
  - one open trade per ticker and strategy at a time
Not modelled: earnings filter (no historical calendar), fundamentals for the quality strategy,
intraday order of prices, liquidity limits. A fixed cost in R is deducted from every trade.
"""
import numpy as np
import pandas as pd

WARMUP = 200


def _rmax(a, w):
    return pd.Series(a).rolling(w).max().to_numpy()


def _rmin(a, w):
    return pd.Series(a).rolling(w).min().to_numpy()


def _shift(a, k):
    out = np.full(len(a), np.nan)
    if k < len(a):
        out[k:] = a[:len(a) - k]
    return out


def gap_list(ind, follow=30):
    o, h, l, c, v = ind.o, ind.h, ind.l, ind.c, ind.v
    n = len(c)
    out = []
    for g in np.flatnonzero((l[1:] > h[:-1]) | (h[1:] < l[:-1])) + 1:
        if g < 51:
            continue
        up = bool(l[g] > h[g - 1])
        level = h[g - 1] if up else l[g - 1]
        end = min(n, g + 1 + follow)
        seg = l[g + 1:end] <= level if up else h[g + 1:end] >= level
        hit = np.flatnonzero(seg)
        f = int(hit[0]) + 1 if len(hit) else 999
        avg_v = v[g - 50:g].mean()
        out.append((int(g), up, (o[g] / c[g - 1] - 1) * 100, v[g] / avg_v if avg_v > 0 else 0.0, f, level))
    return out


def signals(ind, a):
    """{strategy: {t: info}} for every session that met the strategy's price rules."""
    o, h, l, c, v = ind.o, ind.h, ind.l, ind.c, ind.v
    n = len(c)
    out = {"continuation": {}, "gapfill": {}, "quality": {}}
    if n < WARMUP + 10:
        return out
    with np.errstate(invalid="ignore"):
        liq = pd.Series(v).rolling(50).mean().to_numpy() >= a.min_avgvol
        hist, hist1, hist2 = ind.hist, _shift(ind.hist, 1), _shift(ind.hist, 2)
        macd_ok = (ind.m > ind.sg) | ((hist > hist1) & (hist1 > hist2))
        gaps = gap_list(ind)

        if a.use_continuation:
            lb = a.hh_hl_days
            half = lb // 2
            h2, h1 = _rmax(h, half), _shift(_rmax(h, lb - half), half)
            l2, l1 = _rmin(l, half), _shift(_rmin(l, lb - half), half)
            trend = (liq & (c > ind.s50) & (c > ind.s150) & (ind.s50 > ind.s150)
                     & (ind.s150 > _shift(ind.s150, a.sma150_slope_days)) & (h2 > h1) & (l2 > l1)
                     & (ind.r >= a.rsi_min) & (ind.r <= a.rsi_max) & macd_ok)
            for g, up, size, vr, f, level in gaps:
                if vr < a.gap_vol_mult:
                    continue
                for age in range(a.gap_min_age, a.gap_max_age + 1):
                    t = g + age
                    if t >= n - 1:
                        break
                    if f <= age:
                        break
                    if t >= WARMUP and trend[t]:
                        out["continuation"][t] = (g, up, level)

        if a.use_gapfill:
            long_trend = (c > ind.s150) | (ind.s150 > _shift(ind.s150, 20))
            if not a.gf_need_trend:
                long_trend = np.ones(n, dtype=bool)
            for g, up, size, vr, f, level in gaps:
                if up or not (a.gf_min_gap <= -size <= a.gf_max_gap):
                    continue
                for age in range(a.gf_min_age, a.gf_max_age + 1):
                    t = g + age
                    if t >= n - 1 or f <= age:
                        break
                    if t < WARMUP or not (liq[t] and long_trend[t]):
                        continue
                    at = ind.atr[t]
                    if not at or (level - c[t]) / at < a.gf_min_room_atr:
                        continue
                    post_low = l[g:t + 1].min()
                    if c[t] - post_low >= 0.5 * at and c[t] > c[t - 1] and hist[t] > hist1[t]:
                        out["gapfill"][t] = (g, level, post_low)

        if a.use_quality:
            hi = _rmax(h, a.q_lookback)
            dd = (c / hi - 1) * 100
            s1 = (_rmin(ind.r, 15) < 35) & (ind.r > 40)
            s2 = c > ind.e10
            lmin10 = _rmin(l, 10)
            s3 = lmin10 > _shift(lmin10, 10)
            up_day = np.r_[False, c[1:] > c[:-1]]
            vu = pd.Series(v * up_day).rolling(10).sum().to_numpy()
            vd = pd.Series(v * ~up_day).rolling(10).sum().to_numpy()
            stab = s1.astype(int) + s2 + s3 + macd_ok + (vu > vd)
            q = liq & (dd <= -a.q_min_dd) & (dd >= -a.q_max_dd) & (stab >= a.q_min_stab)
            for t in np.flatnonzero(q):
                if WARMUP <= t < n - 1:
                    out["quality"][int(t)] = None
    return out


def simulate(ind, t, strat, info, a, cost):
    o, h, l, c = ind.o, ind.h, ind.l, ind.c
    n = len(c)
    at = ind.atr[t]
    level, check_fill = None, False
    if strat == "continuation":
        g, up, level = info
        trigger = h[t - a.trigger_bars + 1:t + 1].max() if up else h[t - 1:t + 1].max()
        struct = l[t - a.swing_bars + 1:t + 1].min()
        check_fill = not up
    elif strat == "gapfill":
        g, level, struct = info
        trigger = h[t - 1:t + 1].max()
        check_fill = True
    else:
        trigger = h[t - 2:t + 1].max()
        struct = l[t - a.swing_bars + 1:t + 1].min()
    if not at or at != at:
        return ("blocked",)
    entry = trigger + max(0.02, round(0.001 * trigger, 2))
    sl = min(struct - 0.1 * at, entry - a.min_stop_atr * at)
    rps = entry - sl
    if rps <= 0:
        return ("blocked",)
    tp = entry + a.rr * rps
    if (tp - entry) / at > a.max_tp_atr_block or (check_fill and level < tp):
        return ("blocked",)
    d = t + 1
    last = d + a.hold_days - 1
    if last >= n:
        return None
    if o[d] > entry * (1 + a.cancel_gap_pct / 100):
        return ("cancelled",)
    if h[d] < entry:
        return ("nofill",)
    fill = max(entry, o[d])
    risk = fill - sl
    for k in range(d, last + 1):
        if l[k] <= sl:
            px = sl if k == d else min(sl, o[k])
            return ("trade", (px - fill) / risk - cost, "sl", d, k)
        if h[k] >= tp:
            px = tp if k == d else max(tp, o[k])
            return ("trade", (px - fill) / risk - cost, "tp", d, k)
    return ("trade", (c[last] - fill) / risk - cost, "time", d, last)


def _summary(trades, counts, per_trade_risk):
    if not trades:
        return dict(counts, trades=0)
    r = np.array([x["r"] for x in trades])
    wins, losses = r[r > 0], r[r <= 0]
    exits = pd.Series([x["exit"] for x in trades]).value_counts(normalize=True).to_dict()
    df = pd.DataFrame(trades)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    by_year = [{"year": int(y), "n": int(len(g)), "avg_r": float(g["r"].mean()), "win": float((g["r"] > 0).mean())}
               for y, g in df.groupby(df["exit_date"].dt.year)]
    curve = df.groupby(df["exit_date"].dt.strftime("%Y-%m-%d"))["r"].sum().sort_index().cumsum()
    recent = df[df["exit_date"] >= df["exit_date"].max() - pd.Timedelta(days=182)]["r"]
    return dict(counts, trades=int(len(r)), win=float((r > 0).mean()), avg_r=float(r.mean()),
                median_r=float(np.median(r)), total_r=float(r.sum()),
                pf=float(wins.sum() / abs(losses.sum())) if losses.sum() < 0 else None,
                avg_win=float(wins.mean()) if len(wins) else None, avg_loss=float(losses.mean()) if len(losses) else None,
                exits={k: float(v) for k, v in exits.items()}, avg_hold=float(np.mean([x["hold"] for x in trades])),
                usd_per_trade=float(r.mean() * per_trade_risk), recent_n=int(len(recent)),
                recent_avg_r=float(recent.mean()) if len(recent) else None, by_year=by_year,
                curve=[{"time": d, "value": round(float(v), 3)} for d, v in curve.items()],
                best=[dict(x) for x in sorted(trades, key=lambda x: -x["r"])[:5]],
                worst=[dict(x) for x in sorted(trades, key=lambda x: x["r"])[:5]])


def run(inds, a, progress=None):
    cost = float(getattr(a, "bt_cost_r", 0.05))
    res = {}
    trades = {s: [] for s in ("continuation", "gapfill", "quality")}
    counts = {s: {"signals": 0, "blocked": 0, "cancelled": 0, "nofill": 0} for s in trades}
    items = list(inds.items())
    for k, (ticker, ind) in enumerate(items):
        try:
            sig = signals(ind, a)
        except Exception:
            continue
        dates = ind.df.index
        for s, days in sig.items():
            busy = -1
            for t in sorted(days):
                if t <= busy:
                    continue
                counts[s]["signals"] += 1
                out = simulate(ind, t, s, days[t], a, cost)
                if out is None:
                    continue
                if out[0] != "trade":
                    counts[s][out[0]] += 1
                    continue
                _, r, ex, d, e = out
                busy = e
                trades[s].append({"ticker": ticker, "r": round(float(r), 4), "exit": ex,
                                  "entry_date": dates[d].strftime("%Y-%m-%d"),
                                  "exit_date": dates[e].strftime("%Y-%m-%d"), "hold": int(e - d + 1)})
        if progress and k % 50 == 0:
            progress(k + 1, len(items))
    for s in trades:
        res[s] = _summary(trades[s], counts[s], a.risk_per_trade)
    res["params"] = {"cost_r": cost, "hold_days": a.hold_days, "rr": a.rr,
                     "first": min((ind.df.index[WARMUP].strftime("%Y-%m-%d") for ind in inds.values()
                                   if len(ind.df) > WARMUP), default=None),
                     "last": max((ind.df.index[-1].strftime("%Y-%m-%d") for ind in inds.values()), default=None)}
    return res

'use strict';
/* SwingDesk dashboard */

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isNum = x => x !== null && x !== undefined && x !== '' && !isNaN(x);
const fmt = (x, d = 2) => isNum(x) ? Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
const fint = x => isNum(x) ? Math.round(x).toLocaleString('en-US') : '—';
const pct = (x, d = 2) => isNum(x) ? (x > 0 ? '+' : '') + fmt(x, d) + '%' : '—';
const pctOf = (x, d = 0) => isNum(x) ? fmt(x * 100, d) + '%' : '—';
const money = (x, d = 0) => isNum(x) ? (x < 0 ? '-$' : '$') + fmt(Math.abs(x), d) : '—';
const big = x => !isNum(x) ? '—' : Math.abs(x) >= 1e9 ? '$' + fmt(x / 1e9, 1) + 'B' : Math.abs(x) >= 1e6 ? '$' + fmt(x / 1e6, 0) + 'M' : money(x);
const dfull = s => s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.${s.slice(0, 4)}` : '—';
const dshort = s => s ? `${s.slice(8, 10)}.${s.slice(5, 7)}` : '—';
const N = (v, cls = '') => `<span class="num ${cls}">${v}</span>`;
const sgn = x => isNum(x) ? (x > 0 ? 'up' : x < 0 ? 'dn' : '') : '';
const today = () => new Date().toISOString().slice(0, 10);

function weekdaysUntil(dateStr) {
  if (!dateStr) return null;
  const a = new Date(today() + 'T00:00:00'), b = new Date(dateStr + 'T00:00:00');
  let n = 0;
  const sign = b >= a ? 1 : -1;
  for (let d = new Date(a); sign > 0 ? d < b : d > b; d.setDate(d.getDate() + sign)) {
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) n += sign;
  }
  return n;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json' } };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const r = await fetch(path, init);
  if (r.status === 401 && !path.startsWith('/api/login')) { location.href = '/login'; throw new Error('login required'); }
  const t = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(t.error || ('HTTP ' + r.status));
  return t;
}

let toastT;
function toast(msg, err = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(toastT);
  toastT = setTimeout(() => el.className = 'toast', err ? 6000 : 2600);
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    const ok = document.execCommand('copy'); ta.remove(); return ok;
  }
}

// ------------------------------------------------------------------ vocabulary
const STRAT = { continuation: 'המשך גאפ', gapfill: 'סגירת גאפ', quality: 'איכות בירידה' };
const SETUPS = Object.assign({}, STRAT, { opportunity: 'הזדמנות' });
const STRAT_HINT = {
  continuation: 'גאפ פתוח לפני 5–10 ימים במחזור גבוה, בתוך מגמה עולה. התזה: המגמה ממשיכה.',
  gapfill: 'גאפ למטה פתוח, בתוך מגמה ארוכה עולה, עם ריבאונד שכבר התחיל. התזה: הגאפ נסגר. הסיכוי מחושב מהיסטוריה.',
  quality: 'חברה איכותית שירדה חזק מהשיא האחרון ומראה סימני התייצבות. הכניסה לפי כללי הסווינג; הפוטנציאל הארוך מוצג כהקשר.',
};
const checkLabel = (k, st) => ({
  liquidity_50d: 'מחזור 50 יום', gap_in_window: `גאפ לפני ${st.gap_min_age}–${st.gap_max_age} ימים`, gap_unfilled: 'גאפ פתוח',
  gap_volume: `מחזור גאפ ×${st.gap_vol_mult}`, close_gt_sma50: 'מחיר > SMA50', close_gt_sma150: 'מחיר > SMA150',
  sma50_gt_sma150: 'SMA50 > SMA150', sma150_rising: 'SMA150 עולה', hh_hl: 'שיאים ושפלים עולים',
  rsi_range: `RSI ${st.rsi_min}–${st.rsi_max}`, macd: 'MACD חיובי', mcap: 'שווי שוק',
  gf_gap: `גאפ למטה פתוח (${st.gf_min_gap}–${st.gf_max_gap}%)`, gf_room: `לסגירה ≥ ${st.gf_min_room_atr} ATR`,
  gf_trend: 'מגמה ארוכה עולה', gf_bounce: 'ריבאונד התחיל', gf_momentum: 'מומנטום מתהפך', gf_prob: 'סיכוי סגירה מעל הסף',
  q_drawdown: `ירידה של ${st.q_min_dd}–${st.q_max_dd}% מהשיא`, q_stab: `התייצבות ${st.q_min_stab}/5 לפחות`,
  q_quality: `איכות ${st.q_min_quality}/6 לפחות`,
})[k] || k;
const FAILS = {
  liquidity_50d: 'מחזור 50 יום נמוך', gap_in_window: 'אין גאפ בחלון הימים', gap_unfilled: 'הגאפ נסגר',
  gap_volume: 'מחזור ביום הגאפ נמוך', close_gt_sma50: 'מחיר מתחת ל-SMA50', close_gt_sma150: 'מחיר מתחת ל-SMA150',
  sma50_gt_sma150: 'SMA50 מתחת ל-SMA150', sma150_rising: 'SMA150 לא עולה', hh_hl: 'אין שיאים ושפלים עולים',
  rsi_range: 'RSI מחוץ לטווח', macd: 'MACD מתחת לאות', mcap: 'שווי שוק נמוך',
  gf_gap: 'אין גאפ למטה פתוח בגודל שהוגדר', gf_room: 'קרוב מדי לסגירת הגאפ', gf_trend: 'אין מגמה ארוכה עולה',
  gf_bounce: 'עוד אין ריבאונד', gf_momentum: 'המומנטום עוד יורד', gf_prob: 'סיכוי סגירה נמוך מהסף',
  q_drawdown: 'הירידה מהשיא מחוץ לטווח', q_stab: 'מעט סימני התייצבות', q_quality: 'איכות עסקית לא מספיקה',
};
const STAB = { s_rsi: 'RSI חזר מאזור 35 למעל 40', s_ema: 'סגירה מעל EMA10', s_hl: 'שפל 10 ימים גבוה מהקודם', s_macd: 'MACD מתהפך למעלה', s_upvol: 'מחזור בימי עלייה גובר' };
const QUAL = { f_roe: 'ROE ≥ 12%', f_margin: 'מרווח תפעולי ≥ 10%', f_growth: 'הכנסות בצמיחה', f_fcf: 'תזרים חופשי חיובי', f_debt: 'חוב להון < 1.5', f_eps: 'רווח צפוי לגדול' };
const FLAGS = {
  wide_stop: f => `סטופ רחב: ${f.v} ATR`,
  tp_far: f => `היעד דורש תנועה של ${f.v} ATR`,
  tp_unrealistic: f => `היעד דורש ${f.v} ATR בתוך ${f.days} ימים: לא ריאלי לחלון ההחזקה`,
  high52_between: f => `שיא 52 שבועות (${f.v}) בין הכניסה ליעד`,
  trigger_far: f => `הטריגר ${f.v}% מעל הסגירה`,
  qty_capped: f => `הכמות הוגבלה לחשיפה של $${fint(f.v)}`,
  gapfill_below_tp: f => `סגירת הגאפ (${f.v}) נמוכה מהיעד; R:R עד הסגירה ${f.rr}`,
  qty_zero: () => 'כמות 0: תקציב הסיכון קטן מדי לסטופ הזה',
  frozen: () => 'חשד למחיר קפוא אחרי עסקת רכישה במזומן',
  earnings_in_window: f => `דוח בתוך חלון ההחזקה (${(f.v || []).map(dfull).join(', ')})`,
  earnings_unknown: () => 'תאריך הדוח לא ידוע, לאמת לפני כניסה',
  earnings_before_entry: f => `דוח לפני הכניסה (${(f.v || []).map(dfull).join(', ')}), צפוי גאפ בפתיחה`,
  earnings_gap_drift: () => 'הגאפ נוצר בדוח: גאפים כאלה נוטים להמשיך באותו כיוון (post-earnings drift)',
  below_sma150: () => 'מתחת ל-SMA150: המגמה הארוכה עוד שבורה',
  no_slots: () => 'אין מקום פנוי בתקציב',
  same_industry: f => `אותו ענף כמו ${f.v}`,
  high_corr: f => `מתאם ${f.corr} עם ${f.v}`,
  same_ticker: () => 'המניה כבר נבחרה באסטרטגיה אחרת',
  strategy_negative: f => `האסטרטגיה הפסידה בבדיקה לאחור (ממוצע ${f.v}R על ${f.n} עסקאות)`,
};
const LIVE_STATE = { pre: 'פרה-מרקט', open: 'מסחר פתוח', post: 'אחרי סגירה', closed: 'שוק סגור' };
const LIVE_STATUS = {
  waiting: ['cy', 'ממתינה לטריגר'], triggered: ['mag', 'הטריגר נכנס'], cancel: ['rd', 'לבטל: פתיחה מעל הסף'],
  pre_above: ['am', 'פרה-מרקט מעל סף הביטול'], not_today: ['', 'פעילה ביום הכניסה'], expired: ['', 'פגה'],
  open: ['cy', 'פתוחה'], sl: ['rd', 'ב-SL'], tp: ['gr', 'ב-TP'], time_exit: ['am', 'יום יציאה בזמן'],
  watch: ['', 'מעקב'], no_data: ['', 'אין נתון עדכני'],
};
const KIND = { plan: 'תוכנית', position: 'פוזיציה', watch: 'מעקב' };
const flagText = f => f ? (FLAGS[f.code] ? FLAGS[f.code](f) : f.code) : '';
const PATTERN = { flag: 'דגל', triangle: 'משולש / התכנסות', consolidation: 'דחיסת תנודתיות', none: '—' };
const MACD = { above: 'מעל האות', turning: 'מתהפך למעלה', below: 'מתחת לאות' };
const CATALYST = { earnings: 'דוח', other: 'לבדוק חדשות' };
const IMPACT = { high: 'גבוהה', medium: 'בינונית', low: 'נמוכה' };
const STEP = m => {
  if (!m) return '';
  const [k, v] = m.split(':');
  return ({
    start: 'מתחיל', universe: v ? `יקום: ${v} מניות` : 'בונה יקום', download: `מוריד נתונים ${v || ''}`,
    indicators: 'אינדיקטורים וסטטיסטיקת גאפים', stage1: 'שלב 1: סריקה', stage2: `שלב 2: נתוני חברות ${v || ''}`,
    earn: `תאריכי דוחות ${v || ''}`, stage3: 'שלב 3: תוכניות', backtest: `בדיקה לאחור ${v || ''}`, market: 'מצב השוק', saving: 'שומר', done: 'הסתיים',
  })[k] || m;
};
const ERR = e => {
  if (!e) return 'שגיאה לא ידועה';
  const cloudHint = S.app && S.app.cloud ? ' אם זה חוזר באתר, Yahoo כנראה חוסם את השרת בענן. במקרה כזה הפעל את SwingDesk במק: הוא סורק מהבית ושולח את הסריקות לאתר.' : '';
  if (e.includes('universe_empty')) return 'סורק Yahoo לא החזיר מניות. נסה שוב בעוד כמה דקות, או הזן רשימה ידנית בהגדרות.' + cloudHint;
  if (e.includes('no_data')) return 'לא התקבלו נתוני מחיר מ-Yahoo. בדוק את החיבור לאינטרנט ונסה שוב.' + cloudHint;
  return 'הסריקה נכשלה: ' + e;
};

const YES_NO = [[1, 'כן'], [0, 'לא']];
const SETTINGS = [
  ['טלפון: התראות', [['ntfy_topic', 'שם הערוץ ב-ntfy (topic)', 'text'], ['ntfy_alerts', 'התראות מסחר לטלפון', YES_NO],
    ['ntfy_on_scan', 'סיכום כל סריקה לטלפון', YES_NO], ['ntfy_server', 'שרת (להשאיר ntfy.sh)', 'text'], ['ntfy_token', 'טוקן לשרת פרטי (לא חובה)', 'text']]],
  ['אסטרטגיות', [['use_continuation', 'המשך גאפ', YES_NO], ['use_gapfill', 'סגירת גאפ', YES_NO], ['use_quality', 'איכות בירידה', YES_NO],
    ['history_years', 'היסטוריה להורדה ולסטטיסטיקה', [[1, 'שנה'], [2, 'שנתיים'], [5, '5 שנים (איטי)']]]]],
  ['חשבון', [['capital', 'הון ($)'], ['total_risk', 'סיכון כולל מקסימלי ($)'], ['risk_per_trade', 'סיכון לעסקה ($)'],
    ['max_trades', 'מספר עסקאות מקסימלי'], ['rr', 'יחס רווח לסיכון (R)'], ['hold_days', 'ימי החזקה מקסימליים'],
    ['cancel_gap_pct', 'ביטול אם הפתיחה מעל הכניסה (%)']]],
  ['יקום', [['min_mcap', 'שווי שוק מינימלי ($)'], ['min_avgvol', 'מחזור ממוצע מינימלי (מניות)'],
    ['custom_tickers', 'רשימת מניות ידנית', 'textarea', 'ריק = סורק Yahoo על כל השוק. אפשר להפריד בפסיקים או בשורות.']]],
  ['המשך גאפ', [['gap_min_age', 'גיל גאפ מינימלי (ימי מסחר)'], ['gap_max_age', 'גיל גאפ מקסימלי (ימי מסחר)'],
    ['gap_vol_mult', 'מחזור ביום הגאפ (פי ממוצע 50 יום)'], ['sma150_slope_days', 'SMA150 עולה: השוואה ל-X ימים אחורה'],
    ['hh_hl_days', 'חלון שיאים ושפלים עולים (ימים)'], ['rsi_min', 'RSI מינימלי'], ['rsi_max', 'RSI מקסימלי']]],
  ['סגירת גאפ', [['gf_min_age', 'גיל גאפ מינימלי (ימים)'], ['gf_max_age', 'גיל גאפ מקסימלי (ימים)'],
    ['gf_min_gap', 'גודל גאפ מינימלי (%)'], ['gf_max_gap', 'גודל גאפ מקסימלי (%)'],
    ['gf_min_room_atr', 'מרחק מינימלי לסגירה (ATR)'], ['gf_need_trend', 'לחייב מגמה ארוכה עולה', YES_NO],
    ['gf_min_prob', 'סיכוי סגירה מינימלי (0 עד 1)']]],
  ['איכות בירידה', [['q_lookback', 'השיא האחרון: X ימים אחורה'], ['q_min_dd', 'ירידה מינימלית מהשיא (%)'],
    ['q_max_dd', 'ירידה מקסימלית מהשיא (%)'], ['q_min_stab', 'סימני התייצבות מינימליים (מתוך 5)'],
    ['q_min_quality', 'מדדי איכות מינימליים (מתוך 6)']]],
  ['הזדמנויות', [['use_opps', 'לחפש הזדמנויות בכל סריקה', YES_NO], ['opp_lookback', 'השיא: X ימי מסחר אחורה'],
    ['opp_min_drop', 'ירידה מינימלית מהשיא (%)'], ['opp_min_gap', 'גאפ למטה מינימלי (%)'], ['opp_gap_days', 'גאפ מ-X הימים האחרונים'],
    ['opp_min_price', 'מחיר מינימלי ($)'], ['opp_stop_bars', 'סטופ: מתחת לשפל של X ימים'], ['opp_min_stop_atr', 'סטופ: מרחק מינימלי (ATR)'],
    ['opp_min_stab', 'מוכנה: סימני התייצבות מינימליים (מתוך 5)'], ['opp_min_rr', 'מוכנה: יחס מינימלי (R)'], ['opp_max_risk', 'מוכנה: סיכון מקסימלי (%)'],
    ['opp_hold', 'החזקה מקסימלית (ימי מסחר)'], ['opp_max', 'כמה מניות לבדוק לעומק (נתוני חברה)'], ['opp_alert_top', 'התראות על X ההזדמנויות המובילות']]],
  ['לייב וטלגרם', [['live_interval', 'רענון לייב בזמן מסחר (שניות)'], ['auto_sync', 'סנכרון רציף של מחירים חיים', YES_NO], ['tg_alerts', 'התראות לטלגרם', YES_NO],
    ['tg_on_scan', 'סיכום כל סריקה לטלגרם', YES_NO], ['tg_token', 'טוקן הבוט (מ-BotFather)', 'text'], ['tg_chat', 'Chat ID', 'text']]],
  ['סריקה אוטומטית', [['auto_scan', 'סריקה אוטומטית אחרי כל יום מסחר', YES_NO], ['auto_scan_time', 'מאיזו שעה (שעון ישראל, HH:MM)', 'text'],
    ['keep_awake', 'במק: למנוע שינה כל עוד SwingDesk רץ', YES_NO]]],
  ['אתר SwingDesk', [['cloud_url', 'כתובת האתר (למשל swingdesk.up.railway.app)', 'text'], ['cloud_token', 'סיסמת האתר', 'text']]],
  ['בדיקה לאחור', [['use_backtest', 'להריץ בדיקה לאחור בכל סריקה', YES_NO], ['bt_cost_r', 'עלות עסקה בבדיקה (R)']]],
  ['ביצוע', [['trigger_bars', 'טריגר כניסה (המשך): שיא של X ימים'], ['swing_bars', 'שפל מבני ל-SL (המשך): X ימים'],
    ['min_stop_atr', 'מרחק SL מינימלי (ATR)'], ['max_tp_atr', 'דגל כשהיעד רחוק מ-X ATR'],
    ['max_tp_atr_block', 'פסילה כשהיעד רחוק מ-X ATR'],
    ['corr_max', 'מתאם מקסימלי בין שתי עסקאות']]],
];

// ------------------------------------------------------------------ state
const S = { app: null, scan: null, sel: null, strat: 'continuation', view: 'scan', market: null, quotes: {},
  poll: null, charts: [], sort: { key: 'rank', dir: 1 }, prefill: null, lines: {}, extra: [], editPos: null,
  liveTimer: null, live: null, lastAlertId: null, alertPoll: null };

function destroyCharts() {
  S.charts.forEach(c => { try { c.remove(); } catch (e) { /* already gone */ } });
  S.charts = []; S.lines = {}; S.candles = null;
}

// older scans stored one strategy; give them the current shape
function normalizeScan(sc) {
  if (Array.isArray(sc.candidates)) {
    sc.candidates.forEach(r => { r.strategy = 'continuation'; r.key = 'continuation:' + r.ticker; });
    sc.candidates = { continuation: sc.candidates, gapfill: [], quality: [] };
    sc.plans.forEach(p => { p.strategy = 'continuation'; p.key = 'continuation:' + p.ticker; });
    sc.near = { continuation: (sc.near || []).map(r => Object.assign(r, { strategy: 'continuation', key: 'continuation:' + r.ticker })), gapfill: [], quality: [] };
    sc.fail_counts = { continuation: sc.fail_counts || {}, gapfill: {}, quality: {} };
    sc.selected = (sc.selected || []).map(t => 'continuation:' + t);
    sc.enabled = { continuation: 1, gapfill: 0, quality: 0 };
  }
  return sc;
}

const allCands = sc => Object.values(sc.candidates).flat();
const planOf = key => S.scan.plans.find(p => p.key === key);
function findRow(key) {
  if (!key) return null;
  if (key.startsWith('near:')) {
    const k = key.slice(5), s = k.split(':')[0];
    return (S.scan.near[s] || []).find(r => r.key === k) || null;
  }
  return allCands(S.scan).find(r => r.key === key) || null;
}

// ------------------------------------------------------------------ boot
async function init() {
  try {
    S.app = await api('/api/state');
  } catch (e) {
    $('#view').innerHTML = `<div class="empty"><h2>אין חיבור לשרת של SwingDesk</h2><p>ודא שחלון SwingDesk פתוח במחשב, ורענן את הדף.</p></div>`;
    return;
  }
  $$('#nav button').forEach(b => b.addEventListener('click', () => route(b.dataset.view)));
  renderTop();
  if (S.app.scans.length) await loadScan(S.app.scans[0].id);
  openHash();
  window.addEventListener('hashchange', () => { if (location.hash) openHash(); });
  if (S.app.job.state === 'running') startPolling();
  loadMarket();
  await pollAlerts(true);
  pollSync();
  S.alertPoll = setInterval(pollSync, 15000);
}

async function loadScan(id) {
  try {
    S.scan = normalizeScan(await api('/api/scans/' + id));
    const first = S.scan.plans.find(p => p.selected) || S.scan.plans[0];
    if (first) { S.sel = first.key; S.strat = first.strategy; }
    else {
      S.strat = Object.keys(STRAT).find(s => S.scan.enabled && S.scan.enabled[s]) || 'continuation';
      const nr = (S.scan.near[S.strat] || [])[0];
      S.sel = nr ? 'near:' + nr.key : null;
    }
  } catch (e) { toast('לא הצלחתי לטעון את הסריקה: ' + e.message, true); }
}

async function loadMarket(force = false) {
  if (S.scan && S.scan.market && !S.market) { S.market = S.scan.market; renderAnnun(); }
  try { S.market = await api('/api/market' + (force ? '?r=' + Date.now() : '')); } catch (e) { /* keep snapshot */ }
  renderAnnun();
}

// a tap on a phone notification opens /#opp/TICKER, /#live or /#opps
function openHash() {
  const deep = location.hash.startsWith('#opp/');
  route(hashRoute());
  if (deep) setTimeout(() => {
    const d = $('#oppDetail'), bar = $('.topbar');
    if (!d || innerWidth >= 1100) return;
    const pad = bar && getComputedStyle(bar).position === 'sticky' ? bar.offsetHeight + 8 : 8;
    window.scrollTo({ top: d.getBoundingClientRect().top + scrollY - pad });
  }, 400);
}

function hashRoute() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h) history.replaceState(null, '', location.pathname + location.search);
  if (h.startsWith('opp/')) {
    S.oppSel = h.slice(4).toUpperCase().replace(/[^A-Z0-9.^-]/g, '');
    if (S.oppF) Object.assign(S.oppF, { status: 'all', tags: [], q: '' });
    return 'opps';
  }
  return ['opps', 'scan', 'live', 'stats', 'positions', 'journal', 'events', 'settings'].includes(h) ? h : 'opps';
}

function route(view) {
  S.view = view;
  if (S.remoteTimer) { clearTimeout(S.remoteTimer); S.remoteTimer = null; }
  $$('#nav button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
  destroyCharts();
  if (S.liveTimer) { clearInterval(S.liveTimer); S.liveTimer = null; }
  ({ opps: viewOpps, scan: viewScan, live: viewLive, stats: viewStats, positions: viewPositions, journal: viewJournal, events: viewEvents, settings: viewSettings })[view]();
  window.scrollTo({ top: 0 });
}

function renderTop() {
  const a = S.app;
  $('#topmeta').innerHTML = [
    a.demo ? '<span class="badge demo">נתוני דמו, לא אמיתיים</span>' : '',
    `<span>${esc(a.source)}</span>`,
    a.lan_url ? `<span>מהטלפון: <span class="num cyn">${esc(a.lan_url)}</span></span>` : '',
    a.static ? `<span class="badge cy" title="נתוני הענן עודכנו ב-${esc((a.built || '').replace('T', ' ').slice(0, 16))}">ענן · ${esc((a.built || '').slice(11, 16))}</span>`
      : a.cloud ? '<span class="badge cy" title="SwingDesk רץ כאתר בענן">אתר</span>' : '',
    a.remote_session ? '<span class="badge gr" title="מחובר דרך הקישור המאובטח">מחובר מבחוץ</span>'
      : (a.remote && a.remote.state === 'on' ? `<button class="badge gr linkish" id="remoteBadge" title="${esc(a.remote.url || '')}">מבחוץ: פעיל</button>` : ''),
    syncHtml(),
    a.auth ? '<button class="btn ghost" id="logoutBtn" title="יציאה מהאתר במחשב הזה">יציאה</button>' : '',
  ].join('');
  bindSync();
  const rb = $('#remoteBadge');
  if (rb) rb.onclick = () => route('settings');
  const lo = $('#logoutBtn');
  if (lo) lo.onclick = async () => { try { await api('/api/logout', { method: 'POST', body: {} }); } catch (e) { /* ignore */ } location.href = '/login'; };
}

// ------------------------------------------------------------------ annunciator strip
function spark(arr, up) {
  if (!arr || arr.length < 2) return '';
  const mn = Math.min(...arr), mx = Math.max(...arr), w = 46, h = 16;
  const pts = arr.map((v, i) => `${(i / (arr.length - 1)) * w},${h - ((v - mn) / ((mx - mn) || 1)) * h}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${up ? 'var(--green)' : 'var(--red)'}" stroke-width="1.3"/></svg>`;
}

function windowEvents() {
  const sc = S.scan;
  if (!sc) return { win: [], before: [] };
  const ev = S.app.events || [];
  return {
    win: ev.filter(e => e.date >= sc.entry_date && e.date <= sc.exit_date),
    before: ev.filter(e => e.date >= sc.last_bar && e.date < sc.entry_date),
  };
}

function renderAnnun() {
  const m = S.market || (S.scan && S.scan.market);
  const reg = {};
  (m && m.regime || []).forEach(r => reg[r.ticker] = r);
  const names = { '^VIX': 'VIX', '^TNX': 'תשואת 10 שנים' };
  let h = '';
  (m && m.rows || []).forEach(r => {
    if (!isNum(r.close)) { h += `<div class="tile"><div class="k">${esc(names[r.ticker] || r.ticker)}</div><div class="v">—</div></div>`; return; }
    const st = reg[r.ticker] ? reg[r.ticker].state : '';
    const isIdx = !r.ticker.startsWith('^');
    const lights = isIdx ? `<span class="lights" title="מעל SMA50 / מעל SMA150"><i class="${r.vs_sma50 > 0 ? 'on' : 'off'}"></i><i class="${r.vs_sma150 > 0 ? 'on' : 'off'}"></i></span>` : '';
    const val = r.ticker === '^TNX' ? fmt(r.close, 2) + '%' : fmt(r.close, 2);
    h += `<div class="tile ${st}" title="5 ימים: ${pct(r.chg_5d)} | RSI ${fmt(r.rsi, 0)}">
      <div class="k"><span>${esc(names[r.ticker] || r.ticker)}</span>${lights}</div>
      <div class="v num">${val}</div><div class="d num ${sgn(r.chg_1d)}">${pct(r.chg_1d)}</div>${spark(r.spark, r.chg_5d >= 0)}</div>`;
  });
  if (S.scan) {
    const { win, before } = windowEvents();
    const hi = win.some(e => e.impact === 'high');
    h += `<div class="tile ev ${win.length ? (hi ? 'bad' : 'warn') : 'ok'}" title="${esc(win.map(e => dshort(e.date) + ' ' + e.title).join('\n'))}">
      <div class="k"><span>אירועים בחלון ההחזקה</span></div>
      <div class="v">${win.length ? N(win.length) : 'אין'}</div>
      <div class="d muted">${win.length ? esc(win.slice(0, 2).map(e => dshort(e.date) + ' ' + e.title).join(', ')) : N(dshort(S.scan.entry_date) + '–' + dshort(S.scan.exit_date))}</div></div>`;
    if (before.length) {
      h += `<div class="tile ev warn"><div class="k"><span>לפני הכניסה</span></div><div class="v">${N(before.length)}</div>
        <div class="d muted">${esc(before.slice(0, 2).map(e => e.title).join(', '))}</div></div>`;
    }
    const br = S.scan.breadth;
    if (br) {
      const stt = br.above50 >= 0.6 ? 'ok' : br.above50 >= 0.4 ? 'warn' : 'bad';
      h += `<div class="tile ev ${stt}" title="מתוך ${br.n} מניות ביקום. עולות/יורדות ${br.adv}/${br.dec}"><div class="k"><span>רוחב שוק</span></div>
        <div class="v">${N(pctOf(br.above50))} <span class="muted" style="font-size:12px;font-weight:400">מעל SMA50</span></div>
        <div class="d muted">מעל SMA150 ${N(pctOf(br.above150))}, שיאים/שפלים ${N(br.new_highs + '/' + br.new_lows)}</div></div>`;
    }
    const b = S.scan.budget;
    h += `<div class="tile ev ${b.slots ? 'ok' : 'warn'}"><div class="k"><span>תקציב סיכון</span></div>
      <div class="v num">${money(b.remaining)} / ${money(S.scan.settings.total_risk)}</div>
      <div class="d muted">${b.slots ? `${N(b.slots)} עסקאות פנויות, ${money(b.per_trade)} לכל אחת` : 'אין מקום לעסקה חדשה'}</div></div>`;
  }
  $('#annun').innerHTML = h;
}

// ------------------------------------------------------------------ scan job
async function runScan() {
  try {
    S.app.job = await api('/api/scan', { method: 'POST', body: {} });
    startPolling();
    updateProgress();
  } catch (e) { toast(e.message.includes('409') ? 'סריקה כבר רצה' : e.message, true); }
}

function startPolling() {
  if (S.poll) return;
  S.poll = setInterval(async () => {
    let j;
    try { j = await api('/api/job'); } catch (e) { return; }
    S.app.job = j;
    updateProgress();
    if (j.state === 'running') return;
    clearInterval(S.poll); S.poll = null;
    if (j.state === 'done') {
      S.app = await api('/api/state');
      await loadScan(j.id);
      S.market = S.scan.market;
      toast(`הסריקה הסתיימה: ${allCands(S.scan).length} מועמדות`);
      renderAnnun();
      if (['scan', 'stats', 'opps'].includes(S.view)) route(S.view);
      pollSync();
    } else if (j.state === 'error') {
      toast(ERR(j.error), true); updateProgress();
    } else if (j.state === 'cancelled') {
      toast('הסריקה נעצרה'); updateProgress();
    }
  }, 700);
}

function updateProgress() {
  const p = $('#prog');
  if (!p) return;
  const j = S.app.job, run = j.state === 'running';
  p.hidden = !run;
  $('#runBtn').disabled = run;
  if (run) {
    $('.bar', p).style.width = (j.pct || 0) + '%';
    $('.msg', p).textContent = `${STEP(j.msg)} (${j.pct || 0}%)`;
  }
}

// ------------------------------------------------------------------ scan view
function viewScan() {
  const sc = S.scan;
  const opts = (S.app.scans || []).map(x => `<option value="${esc(x.id)}" ${sc && sc.id === x.id ? 'selected' : ''}>${dfull(x.last_bar)} (${x.candidates} מועמדות${x.demo ? ', דמו' : ''})</option>`).join('');
  let h = staleBanner() + `<section class="toolbar">
    <button class="btn primary" id="runBtn">הרץ סריקה</button>
    <div class="progress" id="prog" hidden><div class="track"><div class="bar"></div></div><span class="msg"></span><button class="btn ghost" id="cancelBtn">עצור</button></div>
    <div class="spacer"></div>
    ${opts ? `<label class="inline">סריקה <select id="scanPick">${opts}</select></label>` : ''}
    ${sc ? `<a class="btn" href="/api/scans/${esc(sc.id)}/report.md" title="קובץ להעלאה לשיחה עם Claude">דוח ל-Claude</a>
            <a class="btn" href="/api/scans/${esc(sc.id)}/plans.csv">CSV</a>` : ''}
  </section>`;
  if (!sc) {
    h += `<div class="panel empty"><h2>עוד לא הורצה סריקה</h2>
      <p>הסריקה עוברת על כל המניות בארה"ב שעומדות בתנאי היקום (בערך 1,500), מחשבת אינדיקטורים וסטטיסטיקת גאפים, ובונה תוכנית לכל מניה שעוברת. זה לוקח 5–15 דקות.</p>
      <p class="hint">כדאי להריץ אחרי 22:00 או 23:00 שעון ישראל, כשהנר היומי כבר סגור.</p></div>`;
    $('#view').innerHTML = h;
  } else {
    const b = sc.budget;
    const nAll = allCands(sc).length;
    const facts = [
      ['נר אחרון', `${N(dfull(sc.last_bar))} <span class="badge ${sc.partial ? 'am' : 'gr'}">${sc.partial ? 'חלקי' : 'סגור'}</span>`],
      ['כניסה', N(dfull(sc.entry_date))],
      ['יציאה בזמן', N(dfull(sc.exit_date))],
      ['מועמדות', N(nAll)],
      ['נבחרו', `${N(sc.selected.length)} <span class="muted">מתוך ${N(b.slots)} פנויות</span>`],
      ['סיכון לעסקה', N(money(b.per_trade))],
      ['יקום', `${N(fint(sc.usable_n))} <span class="muted">מניות</span>`],
      ['נשלף', N((sc.pulled_il || sc.pulled_utc || '').slice(11, 16) + ' ' + dshort((sc.pulled_il || sc.pulled_utc || '').slice(0, 10)))],
    ];
    h += `<section class="facts">${facts.map(([l, v]) => `<div class="fact"><div class="l">${l}</div><div class="v">${v}</div></div>`).join('')}</section>`;
    if (sc.demo) h += `<p class="note am" style="margin:-4px 0 12px">נתוני דמו סינתטיים. הסימולים לא קיימים בבורסה. להרצה אמיתית פתח את SwingDesk בלי ‎--demo.</p>`;
    if (sc.partial) h += `<p class="note am" style="margin:-4px 0 12px">הסריקה רצה בזמן מסחר, והנר של היום עוד לא סגור. מומלץ להריץ שוב אחרי הסגירה.</p>`;
    h += selectedHtml(sc);
    h += `<nav class="tabs" id="stratTabs">${Object.keys(STRAT).map(s => {
      const on = !sc.enabled || sc.enabled[s];
      return `<button data-s="${s}" class="${S.strat === s ? 'on' : ''}" ${on ? '' : 'disabled title="כבויה בהגדרות"'}>${STRAT[s]} <span class="cnt">${on ? (sc.candidates[s] || []).length : '—'}</span></button>`;
    }).join('')}<span class="tabhint">${STRAT_HINT[S.strat]}${btLine(sc, S.strat)}</span></nav>`;
    h += `<section class="split">
      <div class="panel"><div class="panel-h">${STRAT[S.strat]} <span class="sub">לחיצה על שורה פותחת גרף ותוכנית</span></div>
        <div class="tablewrap" id="candWrap">${candTable(sc)}</div></div>
      <aside class="panel detail" id="detail"></aside></section>`;
    h += rejectsHtml(sc);
    $('#view').innerHTML = h;
    bindScan();
    renderDetail();
  }
  bindStale();
  $('#runBtn').onclick = runScan;
  const cb = $('#cancelBtn'); if (cb) cb.onclick = () => api('/api/scan/cancel', { method: 'POST', body: {} });
  const sp = $('#scanPick');
  if (sp) sp.onchange = async () => { await loadScan(sp.value); renderAnnun(); route('scan'); };
  updateProgress();
}

function btLine(sc, s) {
  const b = sc.backtest && sc.backtest[s];
  if (!b || !b.trades) return '';
  return ` <span class="${b.avg_r >= 0 ? 'grn' : 'red'}">בבדיקה לאחור: ${N(fint(b.trades))} עסקאות, תוחלת ${N(fmt(b.avg_r, 2) + 'R')}, הצלחה ${N(pctOf(b.win))}.</span>`;
}

function selectedHtml(sc) {
  const sel = sc.plans.filter(p => p.selected);
  if (!sel.length) {
    return `<div class="panel planbar"><div class="panel-h">התוכנית למסחר הבא</div><div class="panel-b muted">${allCands(sc).length
      ? 'אף מועמדת לא עברה את כל הסינונים והתקציב. אפשר לעבור על המועמדות עם הדגלים ולהחליט ידנית.'
      : 'אין היום מניות שעומדות בכל התנאים. זו תוצאה לגיטימית: הכללים לא מתכופפים.'}</div></div>`;
  }
  return `<div class="panel planbar"><div class="panel-h">התוכנית למסחר הבא <span class="sub">${N(dfull(sc.entry_date))}</span></div>
    <div class="tablewrap" style="max-height:none"><table class="grid"><thead><tr><th>סימול</th><th>אסטרטגיה</th><th>כניסה (Buy Stop)</th><th>SL</th><th>TP</th><th>כמות</th><th>סיכון</th><th>רווח</th><th>בטל אם פותח מעל</th><th>יציאה בזמן</th></tr></thead>
    <tbody>${sel.map(p => `<tr data-key="${esc(p.key)}" data-s="${p.strategy}"><td class="t">${esc(p.ticker)}</td><td>${STRAT[p.strategy]}</td>
      <td class="n mag">${fmt(p.entry)}</td><td class="n amb">${fmt(p.sl)}</td><td class="n grn">${fmt(p.tp)}</td><td class="n">${fint(p.qty)}</td>
      <td class="n">${money(p.risk_usd, 2)}</td><td class="n">${money(p.reward_usd, 2)}</td><td class="n">${fmt(p.cancel_above)}</td><td class="n">${dfull(p.time_exit)}</td></tr>`).join('')}</tbody></table></div></div>`;
}

function statusOf(plan) {
  if (!plan) return { rank: 9, html: '<span class="badge rd">—</span>' };
  if (plan.selected) return { rank: 0, html: '<span class="badge mag">נבחרה</span>' };
  if (!plan.ok) return { rank: 3, html: `<span class="badge rd" title="${esc(plan.blocking.map(flagText).join('\n'))}">נפסלה</span>` };
  if (plan.flags.length) return { rank: 2, html: `<span class="badge am" title="${esc(plan.flags.map(flagText).join('\n'))}">${plan.flags.length} דגלים</span>` };
  return { rank: 1, html: '<span class="badge gr">עומדת בתנאים</span>' };
}

const COLS = {
  continuation: [
    ['סטאפ', 'setup', r => r.setup === 'correction' ? '<span class="amb">תיקון</span>' : 'המשך'],
    ['גאפ', 'gap_date', r => `${dshort(r.gap_date)} <span class="${r.dir === 'up' ? 'up' : 'dn'}">${r.dir === 'up' ? '▲' : '▼'}</span>`, 'n'],
    ['גאפ %', 'gap_pct', r => `<span class="${sgn(r.gap_pct)}">${pct(r.gap_pct, 1)}</span>`, 'n'],
    ['מחזור', 'vol_ratio', r => '×' + fmt(r.vol_ratio, 1), 'n'], ['RS', 'rs_rank', r => fint(r.rs_rank), 'n'],
    ['סגירה', 'close', r => fmt(r.close), 'n'], ['SMA50', 'sma50', r => fmt(r.sma50), 'n'], ['SMA150', 'sma150', r => fmt(r.sma150), 'n'],
    ['RSI', 'rsi', r => fmt(r.rsi, 1), 'n'], ['MACD', null, r => MACD[r.macd_state] || ''],
    ['תבנית', 'pattern', r => PATTERN[r.pattern] || '—'], ['זרז', null, r => CATALYST[r.catalyst] || '—'],
  ],
  gapfill: [
    ['גאפ', 'gap_date', r => `${dshort(r.gap_date)} <span class="dn">▼</span>`, 'n'],
    ['גאפ %', 'gap_pct', r => `<span class="dn">${pct(r.gap_pct, 1)}</span>`, 'n'],
    ['גיל', 'age', r => fint(r.age), 'n'],
    ['רמת סגירה', 'fill_level', r => fmt(r.fill_level), 'n'],
    ['עד הסגירה', 'to_fill_pct', r => `${pct(r.to_fill_pct, 1)} <span class="muted">(${fmt(r.to_fill_atr, 1)} ATR)</span>`, 'n'],
    ['סיכוי סגירה', 'prob', r => r.fill_prob && isNum(r.fill_prob.p) ? `${pctOf(r.fill_prob.p)} <span class="muted">N=${fint(r.fill_prob.n)}</span>` : '—', 'n'],
    ['מגמה ארוכה', 'long_trend', r => r.long_trend ? '<span class="grn">עולה</span>' : '<span class="amb">לא</span>'], ['RS', 'rs_rank', r => fint(r.rs_rank), 'n'],
    ['RSI', 'rsi', r => fmt(r.rsi, 1), 'n'], ['זרז', null, r => CATALYST[r.catalyst] || '—'],
  ],
  quality: [
    ['מהשיא', 'dd_pct', r => `<span class="dn">${pct(r.dd_pct, 1)}</span>`, 'n'],
    ['איכות', 'qual_n', r => `${fint(r.qual_n)}/${fint(r.qual_avail)}`, 'n'],
    ['התייצבות', 'stab_n', r => `${fint(r.stab_n)}/5`, 'n'],
    ['ROE', null, r => pctOf((r.fund || {}).roe), 'n'],
    ['מרווח תפעולי', null, r => pctOf((r.fund || {}).op_margin), 'n'],
    ['צמיחת הכנסות', null, r => pctOf((r.fund || {}).rev_growth), 'n'],
    ['P/E קדימה', null, r => fmt((r.fund || {}).fpe, 1), 'n'],
    ['P/FCF', 'p_fcf', r => fmt(r.p_fcf, 1), 'n'],
    ['עד השיא', 'to_high_pct', r => `<span class="up">${pct(r.to_high_pct, 0)}</span>`, 'n'],
    ['יעד אנליסטים', 'to_target_pct', r => pct(r.to_target_pct, 0), 'n'],
    ['ענף', null, r => esc(r.industry || '—')],
  ],
};

function candTable(sc) {
  const rows = (sc.candidates[S.strat] || []).map(r => {
    const plan = planOf(r.key), st = statusOf(plan);
    return { r, st, rank: st.rank * 1000 - (plan ? plan.score : 0) };
  });
  const k = S.sort.key, d = S.sort.dir;
  const val = x => k === 'rank' ? x.rank : k === 'prob' ? ((x.r.fill_prob || {}).p ?? -1) : (x.r[k] ?? -1e9);
  rows.sort((a, b) => (typeof val(a) === 'string' ? String(val(a)).localeCompare(String(val(b))) : val(a) - val(b)) * d);
  if (sc.enabled && !sc.enabled[S.strat]) return `<div class="empty"><p>האסטרטגיה הזו כבויה בהגדרות.</p></div>`;
  if (!rows.length) return `<div class="empty"><h2>אין מועמדות באסטרטגיה הזו</h2><p>זו תוצאה לגיטימית. בטבלת "כמעט עברו" למטה רואים מי הייתה הכי קרובה ולמה נפלה.</p></div>`;
  const th = (label, key) => `<th ${key ? `data-sort="${key}"` : ''}>${label}${S.sort.key === key && key ? (d > 0 ? ' ▴' : ' ▾') : ''}</th>`;
  const cols = COLS[S.strat];
  return `<table class="grid" id="candTable"><thead><tr>${th('סימול', 'ticker')}${th('סטטוס', 'rank')}${cols.map(c => th(c[0], c[1])).join('')}${th('שם')}</tr></thead>
    <tbody>${rows.map(({ r, st }) => `<tr data-key="${esc(r.key)}" class="${S.sel === r.key ? 'sel' : ''}">
      <td class="t">${esc(r.ticker)}</td><td>${st.html}</td>${cols.map(c => `<td class="${c[3] || ''}">${c[2](r)}</td>`).join('')}
      <td class="nm" title="${esc(r.name)}">${esc(r.name)}</td></tr>`).join('')}</tbody></table>`;
}

function bindRows(scope) {
  $$('tr[data-key]', scope).forEach(tr => tr.addEventListener('click', () => {
    const key = tr.dataset.key;
    if (tr.dataset.s && tr.dataset.s !== S.strat) { S.strat = tr.dataset.s; S.sel = key; route('scan'); return; }
    S.sel = key;
    $$('#view tr[data-key]').forEach(x => x.classList.toggle('sel', x.dataset.key === S.sel));
    renderDetail();
    if (window.innerWidth < 1100) $('#detail').scrollIntoView({ behavior: 'smooth' });
  }));
}

function bindScan() {
  bindRows($('#view'));
  $$('#stratTabs button[data-s]').forEach(b => b.onclick = () => {
    S.strat = b.dataset.s; S.sort = { key: 'rank', dir: 1 };
    const first = (S.scan.candidates[S.strat] || [])[0] || null;
    const nr = (S.scan.near[S.strat] || [])[0];
    S.sel = first ? first.key : (nr ? 'near:' + nr.key : null);
    route('scan');
  });
  $$('#candTable th[data-sort]').forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.sort;
    S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (k === 'rank' || k === 'ticker' ? 1 : -1) };
    $('#candWrap').innerHTML = candTable(S.scan);
    bindScan();
  }));
}

function checksHtml(r, st) {
  const keys = Object.keys(r.checks || {});
  return `<div class="checks">${keys.map(k => `<div class="chk ${r.checks[k] ? 'pass' : 'fail'}">${checkLabel(k, st)}</div>`).join('')}</div>`;
}

function subChecks(title, obj, labels) {
  if (!obj) return '';
  return `<div class="subchecks"><div class="l">${title}</div><div class="checks">${Object.keys(labels).map(k => {
    const v = obj[k];
    return `<div class="chk ${v === true ? 'pass' : v === false ? 'fail' : ''}">${labels[k]}${v === null || v === undefined ? ' <span class="muted">(אין נתון)</span>' : ''}</div>`;
  }).join('')}</div></div>`;
}

function fundHtml(r) {
  const f = r.fund;
  if (!f) return '';
  const cells = [
    ['ROE', pctOf(f.roe, 1)], ['מרווח תפעולי', pctOf(f.op_margin, 1)], ['מרווח גולמי', pctOf(f.gross_margin, 1)],
    ['צמיחת הכנסות', pctOf(f.rev_growth, 1)], ['צמיחת רווח', pctOf(f.earn_growth, 1)], ['תזרים חופשי', big(f.fcf)],
    ['חוב להון', isNum(f.de) ? fmt(f.de / 100, 2) : '—'], ['P/E', fmt(f.pe, 1)], ['P/E קדימה', fmt(f.fpe, 1)],
    ['EPS → קדימה', `${fmt(f.eps)} → ${fmt(f.feps)}`], ['P/FCF', fmt(r.p_fcf, 1)], ['שווי שוק', big(f.mcap)],
  ];
  return `<div class="plan fund">${cells.map(([l, v]) => `<div class="cell"><div class="l">${l}</div><div class="v num">${v}</div></div>`).join('')}</div>`;
}

async function renderDetail() {
  destroyCharts();
  const el = $('#detail');
  if (!el) return;
  const sc = S.scan, st = sc.settings;
  const r = findRow(S.sel);
  if (!r) { el.innerHTML = `<div class="empty"><p>בחר מניה מהטבלה כדי לראות גרף ותוכנית.</p></div>`; return; }
  const isNear = S.sel.startsWith('near:');
  const plan = isNear ? null : planOf(r.key);
  const ps = plan ? statusOf(plan) : null;
  const t = r.ticker, s = r.strategy;
  const meta = [];
  if (r.dir && s !== 'quality') meta.push(`גאפ ${r.dir === 'up' ? 'עולה' : 'יורד'} ב-${N(dfull(r.gap_date))}, ${N(pct(r.gap_pct, 1), sgn(r.gap_pct))}, מחזור ${N('×' + fmt(r.vol_ratio, 1))}`);
  if (s === 'quality') meta.push(`${N(pct(r.dd_pct, 1), 'dn')} מהשיא של ${N(fmt(r.recent_high))} (${N(dfull(r.recent_high_date))})`);
  if (r.sector) meta.push(`${esc(r.sector)}, ${esc(r.industry)}`);
  meta.push(`ATR ${N(fmt(r.atr))} (${N(fmt(r.atr_pct, 1) + '%')})`);
  meta.push(`RSI ${N(fmt(r.rsi, 1))}`);
  let h = `<div class="dh"><span class="tk">${esc(t)}</span><span class="nm">${esc(r.name || '')}</span>
      <span class="badge cy">${STRAT[s]}${s === 'continuation' && r.setup === 'correction' ? ': תיקון' : ''}</span>
      ${ps ? ps.html : '<span class="badge rd">לא עברה</span>'}
      <div class="meta">${meta.map(x => `<span>${x}</span>`).join('')}</div></div>
    ${checksHtml(r, st)}`;
  if (s === 'quality') h += subChecks('סימני התייצבות', r.stab, STAB) + subChecks('איכות עסקית', r.qual, QUAL);
  h += `<div class="chart" id="c1"></div><div class="chart rsi" id="c2"></div>
    <div class="legend"><span><i style="border-color:var(--cyan)"></i>SMA50</span><span><i style="border-color:#3E8796"></i>SMA150</span>
      ${r.dir ? '<span><i style="border-color:var(--cyan);border-top-style:dotted"></i>אזור הגאפ</span>' : ''}
      ${s === 'quality' ? '<span><i style="border-color:rgba(141,163,176,.7);border-top-style:dotted"></i>השיא האחרון</span>' : ''}
      ${plan ? '<span><i style="border-color:var(--magenta);border-top-style:dashed"></i>כניסה</span><span><i style="border-color:var(--amber)"></i>SL</span><span><i style="border-color:var(--green)"></i>TP</span>' : ''}
      <span>למטה: RSI(14)</span></div>`;
  if (s === 'quality') h += fundHtml(r);
  const notes = [];
  if (s === 'gapfill' && r.fill_prob && isNum(r.fill_prob.p)) {
    const fp = r.fill_prob;
    notes.push(['cy', `בהיסטוריה של היקום: גאפים דומים (${esc(basisText(fp.basis))}) שעדיין היו פתוחים אחרי ${N(fp.age)} ימים, נסגרו בתוך ${N(fp.horizon)} הימים הבאים ב-${N(pctOf(fp.p), 'cyn')} מהמקרים (${N('N=' + fint(fp.n))}).`]);
  }
  if (s === 'gapfill' || (s === 'continuation' && r.dir === 'down')) notes.push(['cy', `רמת סגירת הגאפ: ${N(fmt(r.fill_level))}${isNum(r.to_fill_pct) ? `, ${N(pct(r.to_fill_pct, 1))} מהסגירה (${N(fmt(r.to_fill_atr, 1))} ATR)` : ''}.`]);
  if (s === 'continuation' && sc.gap_stats && sc.gap_stats.cont && r.dir === 'up') {
    const c = sc.gap_stats.cont;
    notes.push(['cy', `בסיס סטטיסטי: גאפים עולים במגמה עם מחזור ×1.5 שעדיין פתוחים ביום 5. ב-5 הימים הבאים: חציון ${N(pct(c.median * 100, 2))}, חיוביים ${N(pctOf(c.win))} (${N('N=' + fint(c.n))}).`]);
  }
  if (s === 'quality') {
    notes.push(['cy', `תקרות אופטימיות, לא תחזית: עד השיא הקודם ${N(pct(r.to_high_pct, 0))}, עד SMA50 ${N(pct(r.to_sma50_pct, 0))}${isNum(r.to_target_pct) ? `, עד יעד האנליסטים ${N(pct(r.to_target_pct, 0))} (${N(fint((r.fund || {}).n_analysts))} אנליסטים)` : ''}.`]);
    notes.push(['cy', 'חזרה לשיא לוקחת בדרך כלל שבועות עד חודשים. התוכנית כאן היא כניסת סווינג של 2–5 ימים לפי הכללים שלך.']);
  }
  if (plan) {
    const basis = { atr_floor: `ברצפה של ${st.min_stop_atr} ATR, מתחת לשפל המבני`, post_gap_low: 'מתחת לשפל שאחרי הגאפ' }[plan.sl_basis] || `מתחת לשפל של ${st.swing_bars} ימים`;
    notes.push(['cy', `טריגר הכניסה: ${N(fmt(plan.trigger))}. ה-SL ${basis} (${N(fmt(plan.struct_low))}), במרחק ${N(fmt(plan.stop_atr, 2))} ATR. היעד דורש ${N(fmt(plan.tp_atr, 2))} ATR.`]);
    if (plan.alt_limit) notes.push(['cy', `חלופה: Limit בנסיגה לראש הגאפ ב-${N(fmt(plan.alt_limit))}`]);
    if (r.earnings_dates) notes.push(['cy', `דוחות: ${r.earnings_dates.length ? r.earnings_dates.map(d => N(dfull(d))).join(', ') : 'לא ידוע'}`]);
    plan.flags.forEach(f => notes.push(['am', esc(flagText(f))]));
    plan.blocking.forEach(f => notes.push(['rd', 'פסילה: ' + esc(flagText(f))]));
    if (plan.not_selected) notes.push(['am', 'לא נבחרה: ' + esc(flagText(plan.not_selected))]);
    const cells = [
      ['כניסה (Buy Stop)', fmt(plan.entry), 'mag'], ['SL', fmt(plan.sl), 'am'], [`TP (${plan.rr}R)`, fmt(plan.tp), 'gr'],
      ['כמות', fint(plan.qty)], ['חשיפה', money(plan.exposure)], ['סיכון', money(plan.risk_usd, 2)],
      ['רווח פוטנציאלי', money(plan.reward_usd, 2)], ['ביטול אם הפתיחה מעל', fmt(plan.cancel_above)], ['יציאה בזמן', dfull(plan.time_exit)],
    ];
    h += `<div class="plan">${cells.map(([l, v, c]) => `<div class="cell"><div class="l">${l}</div><div class="v num ${c || ''}">${v}</div></div>`).join('')}</div>`;
  } else {
    (r.fails || []).forEach(f => notes.push(['rd', FAILS[f] || f]));
    if (r.fund_skipped) notes.push(['am', 'נתוני החברה לא נבדקו: יותר מדי מועמדות בשלב המחיר.']);
  }
  h += `<div class="notes">${notes.map(([c, x]) => `<div class="note ${c}">${x}</div>`).join('')}</div>`;
  const watching = (S.app.watchlist || []).includes(t);
  const watchBtn = `<button class="btn ghost" id="watchBtn">${watching ? 'הסר מהמעקב בלייב' : 'הוסף למעקב בלייב'}</button>`;
  if (!plan) h += `<div class="actions">${watchBtn}</div>`;
  if (plan) {
    h += `<div class="whatif"><label>כניסה<input type="number" step="0.01" id="wiE" value="${plan.entry}"></label>
      <label>SL<input type="number" step="0.01" id="wiS" value="${plan.sl}"></label>
      <label>R<input type="number" step="0.1" id="wiR" value="${plan.rr}"></label>
      <div class="out" id="wiOut"></div></div>
      <div class="actions"><button class="btn" id="copyOrder">העתק פקודה</button>
      <button class="btn" id="toPos">רשום כפוזיציה פתוחה</button>
      <button class="btn ghost" id="wiReset">אפס מחשבון</button>${watchBtn}</div>`;
  }
  h += `<div class="news"><div class="panel-h">חדשות אחרונות <span class="sub">Yahoo Finance</span></div><div class="newsb" id="newsBox"><span class="muted">טוען חדשות...</span></div></div>`;
  el.innerHTML = h;
  $('#watchBtn').onclick = () => toggleWatch(t);
  loadNews(t);
  let cd = sc.charts[t];
  if (!cd) {
    $('#c1').innerHTML = '<div class="empty">טוען גרף...</div>';
    try { cd = await api('/api/chart?t=' + encodeURIComponent(t)); } catch (e) { $('#c1').innerHTML = `<div class="empty">אין נתוני גרף: ${esc(e.message)}</div>`; return; }
    if (findRow(S.sel) !== r) return;
    $('#c1').innerHTML = '';
  }
  drawCharts(cd, r, plan, $('#c1'), $('#c2'));
  if (plan) bindWhatIf(plan);
}

async function loadNews(t) {
  const box = $('#newsBox');
  try {
    const items = await api('/api/news?t=' + encodeURIComponent(t));
    if (!$('#newsBox') || findRow(S.sel)?.ticker !== t) return;
    box.innerHTML = items.length ? items.map(n => `<div class="newsi">${n.link ? `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)}
      <div class="muted">${esc(n.publisher || '')}${n.time ? ' | ' + esc(ago(n.time)) : ''}</div></div>`).join('') : '<span class="muted">אין חדשות זמינות.</span>';
  } catch (e) { box.innerHTML = `<span class="muted">לא הצלחתי למשוך חדשות: ${esc(e.message)}</span>`; }
}

function ago(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const h = (Date.now() - d.getTime()) / 36e5;
  if (h < 1) return 'לפני פחות משעה';
  if (h < 24) return `לפני ${Math.round(h)} שעות`;
  return `לפני ${Math.round(h / 24)} ימים`;
}

async function toggleWatch(t) {
  const wl = new Set(S.app.watchlist || []);
  wl.has(t) ? wl.delete(t) : wl.add(t);
  try {
    S.app.watchlist = await api('/api/watchlist', { method: 'PUT', body: [...wl] });
    toast(S.app.watchlist.includes(t) ? `${t} נוספה למעקב בלייב` : `${t} הוסרה מהמעקב`);
    const b = $('#watchBtn'); if (b) b.textContent = S.app.watchlist.includes(t) ? 'הסר מהמעקב בלייב' : 'הוסף למעקב בלייב';
    if (S.view === 'live') viewLive();
  } catch (e) { toast(e.message, true); }
}

function basisText(b) {
  return String(b || '').replace('down', 'למטה').replace('up', 'למעלה').replace('all למטה gaps', 'כל הגאפים למטה')
    .replace('all למעלה gaps', 'כל הגאפים למעלה').replace('no uptrend', 'בלי מגמה עולה').replace('uptrend', 'במגמה עולה');
}

// ------------------------------------------------------------------ charts
const LW = window.LightweightCharts;
function chartBase(extra = {}) {
  return Object.assign({
    layout: { background: { type: 'solid', color: '#142431' }, textColor: '#8DA3B0', fontFamily: 'IBM Plex Sans, Segoe UI, sans-serif', fontSize: 11 },
    grid: { vertLines: { color: 'rgba(39,64,79,.35)' }, horzLines: { color: 'rgba(39,64,79,.35)' } },
    rightPriceScale: { borderColor: '#27404F', minimumWidth: 70 },
    timeScale: { borderColor: '#27404F', rightOffset: 6 },
    crosshair: { mode: LW.CrosshairMode.Normal },
    autoSize: true,
  }, extra);
}

function drawCharts(cd, r, plan, el1, el2) {
  if (!LW || !cd || !cd.bars || !cd.bars.length) return;
  const ch = LW.createChart(el1, chartBase());
  S.extra = [];
  const candles = ch.addCandlestickSeries({
    upColor: '#63CF86', downColor: '#EE6A5C', borderVisible: false, wickUpColor: '#63CF86', wickDownColor: '#EE6A5C',
    autoscaleInfoProvider: orig => {
      const res = orig();
      if (res && S.extra.length) {
        res.priceRange.minValue = Math.min(res.priceRange.minValue, ...S.extra);
        res.priceRange.maxValue = Math.max(res.priceRange.maxValue, ...S.extra);
      }
      return res;
    },
  });
  candles.setData(cd.bars);
  const v = ch.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
  ch.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  v.setData(cd.bars.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(99,207,134,.28)' : 'rgba(238,106,92,.28)' })));
  const l50 = ch.addLineSeries({ color: '#57D2E6', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  l50.setData(cd.sma50);
  const l150 = ch.addLineSeries({ color: '#3E8796', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  l150.setData(cd.sma150);
  const pl = (price, color, title, style = LW.LineStyle.Solid, width = 1) =>
    candles.createPriceLine({ price, color, title, lineStyle: style, lineWidth: width, axisLabelVisible: true });
  const markers = [];
  if (r && r.dir && isNum(r.zone_lo) && r.strategy !== 'quality') {
    pl(r.zone_lo, 'rgba(87,210,230,.75)', r.dir === 'down' ? '' : 'גאפ', LW.LineStyle.Dotted);
    pl(r.zone_hi, 'rgba(87,210,230,.75)', r.dir === 'down' ? 'סגירת גאפ' : '', LW.LineStyle.Dotted);
    if (cd.bars.some(b => b.time === r.gap_date)) markers.push({ time: r.gap_date, position: r.dir === 'up' ? 'belowBar' : 'aboveBar', color: '#57D2E6', shape: r.dir === 'up' ? 'arrowUp' : 'arrowDown', text: 'גאפ' });
  }
  if (r && r.strategy === 'quality' && isNum(r.recent_high)) {
    pl(r.recent_high, 'rgba(141,163,176,.7)', 'שיא אחרון', LW.LineStyle.SparseDotted);
    if (cd.bars.some(b => b.time === r.recent_high_date)) markers.push({ time: r.recent_high_date, position: 'aboveBar', color: '#8DA3B0', shape: 'circle', text: 'שיא' });
  }
  if (markers.length) candles.setMarkers(markers.sort((a, b) => a.time.localeCompare(b.time)));
  if (r && isNum(r.high52) && r.strategy !== 'quality') {
    const lastClose = cd.bars[cd.bars.length - 1].close;
    if (r.high52 < lastClose * 1.25) pl(r.high52, 'rgba(141,163,176,.6)', '52W', LW.LineStyle.SparseDotted);
  }
  S.candles = candles;
  if (plan) setPlanLines(plan.entry, plan.sl, plan.tp);
  const span = r && r.strategy === 'quality' ? 160 : 100;
  ch.timeScale().setVisibleLogicalRange({ from: Math.max(0, cd.bars.length - span), to: cd.bars.length + 4 });

  const base2 = chartBase({ timeScale: { visible: false, borderColor: '#27404F', rightOffset: 6 }, handleScroll: false, handleScale: false });
  base2.layout = Object.assign({}, base2.layout, { attributionLogo: false });
  const rc = LW.createChart(el2, base2);
  const rm = new Map(cd.rsi.map(p => [p.time, p.value]));
  const rs = rc.addLineSeries({ color: '#57D2E6', lineWidth: 1.5, priceLineVisible: false, crosshairMarkerVisible: false, priceFormat: { type: 'price', precision: 0, minMove: 1 } });
  rs.setData(cd.bars.map(b => rm.has(b.time) ? { time: b.time, value: rm.get(b.time) } : { time: b.time }));
  const lv = r && r.strategy === 'quality' ? [35, 70] : [40, 70];
  lv.forEach(x => rs.createPriceLine({ price: x, color: 'rgba(141,163,176,.55)', lineStyle: LW.LineStyle.Dotted, lineWidth: 1, axisLabelVisible: true, title: '' }));
  ch.timeScale().subscribeVisibleLogicalRangeChange(rg => { if (rg) rc.timeScale().setVisibleLogicalRange(rg); });
  rc.timeScale().setVisibleLogicalRange(ch.timeScale().getVisibleLogicalRange());
  S.charts.push(ch, rc);
}

function setPlanLines(entry, sl, tp) {
  const c = S.candles;
  if (!c) return;
  Object.values(S.lines).forEach(l => { try { c.removePriceLine(l); } catch (e) { /* noop */ } });
  S.lines = {
    e: c.createPriceLine({ price: entry, color: '#E066D2', title: 'כניסה', lineStyle: LW.LineStyle.Dashed, lineWidth: 2, axisLabelVisible: true }),
    s: c.createPriceLine({ price: sl, color: '#F0B442', title: 'SL', lineStyle: LW.LineStyle.Solid, lineWidth: 2, axisLabelVisible: true }),
    t: c.createPriceLine({ price: tp, color: '#63CF86', title: 'TP', lineStyle: LW.LineStyle.Solid, lineWidth: 2, axisLabelVisible: true }),
  };
  S.extra = [entry, sl, tp];
}

// ------------------------------------------------------------------ what-if + actions
function calcPlan(plan, e, s, rr) {
  const rps = e - s;
  if (!(rps > 0)) return null;
  const tp = e + rr * rps;
  let q = Math.floor(plan.per_trade_risk / rps), capped = false;
  if (q * e > plan.max_exposure) { q = Math.floor(plan.max_exposure / e); capped = true; }
  return { tp: Math.round(tp * 100) / 100, q, capped, rps, risk: q * rps, reward: q * (tp - e), exp: q * e, sAtr: rps / plan.atr, tAtr: (tp - e) / plan.atr };
}

function bindWhatIf(plan) {
  const upd = () => {
    const e = +$('#wiE').value, s = +$('#wiS').value, rr = +$('#wiR').value;
    const c = calcPlan(plan, e, s, rr);
    const out = $('#wiOut');
    if (!c) { out.innerHTML = '<span class="red">ה-SL חייב להיות מתחת לכניסה.</span>'; return; }
    const minAtr = S.scan.settings.min_stop_atr;
    out.innerHTML = `TP ${N(fmt(c.tp), 'grn')} | כמות ${N(fint(c.q))} | חשיפה ${N(money(c.exp))} | סיכון ${N(money(c.risk, 2))} | רווח ${N(money(c.reward, 2))} | SL ${N(fmt(c.sAtr, 2))} ATR`
      + (c.capped ? ' <span class="amb">(הכמות הוגבלה לפי חשיפה)</span>' : '')
      + (c.sAtr < minAtr ? ` <span class="red">ה-SL קרוב מ-${minAtr} ATR, בניגוד לכלל</span>` : '')
      + (isNum(plan.fill_level) && plan.strategy === 'gapfill' && c.tp > plan.fill_level ? ' <span class="amb">היעד מעל רמת סגירת הגאפ</span>' : '');
    setPlanLines(e, s, c.tp);
    S.whatIf = { e, s, tp: c.tp, q: c.q };
  };
  ['#wiE', '#wiS', '#wiR'].forEach(id => $(id).addEventListener('input', upd));
  upd();
  $('#wiReset').onclick = () => { $('#wiE').value = plan.entry; $('#wiS').value = plan.sl; $('#wiR').value = plan.rr; upd(); };
  $('#copyOrder').onclick = async () => {
    const w = S.whatIf;
    const txt = `BUY STOP ${plan.ticker} x${w.q} @ ${w.e} | SL ${w.s} | TP ${w.tp} | בטל אם הפתיחה מעל ${(w.e * (1 + S.scan.settings.cancel_gap_pct / 100)).toFixed(2)} | יציאה בזמן ${dfull(plan.time_exit)}`;
    toast(await copyText(txt) ? 'הפקודה הועתקה' : txt);
  };
  $('#toPos').onclick = () => {
    const w = S.whatIf;
    S.prefill = { ticker: plan.ticker, entry: w.e, sl: w.s, tp: w.tp, qty: w.q, time_exit: plan.time_exit, setup: plan.strategy, scan_id: S.scan.id, opened: today() };
    route('positions');
  };
}

// ------------------------------------------------------------------ rejects
function rejectsHtml(sc) {
  const s = S.strat, st = sc.settings;
  const fc = Object.entries(sc.fail_counts[s] || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...fc.map(x => x[1]));
  const bars = fc.map(([k, n]) => `<div class="brow"><span>${FAILS[k] || k}</span><div class="track"><div class="fill" style="width:${(n / max) * 100}%"></div></div><span class="num muted">${fint(n)}</span></div>`).join('');
  const nearRows = (sc.near[s] || []).slice(0, 30);
  const near = nearRows.map(r => {
    const info = s === 'quality' ? `${pct(r.dd_pct, 0)}` : s === 'gapfill' ? `${dshort(r.gap_date)} ${pct(r.gap_pct, 1)}` : `${dshort(r.gap_date)} ${pct(r.gap_pct, 1)}`;
    return `<tr data-key="near:${esc(r.key)}" class="${S.sel === 'near:' + r.key ? 'sel' : ''}">
      <td class="t">${esc(r.ticker)}</td><td class="n">${info}</td><td class="n">${fmt(r.rsi, 1)}</td>
      <td class="red">${r.fails.map(f => FAILS[f] || f).join(', ')}</td></tr>`;
  }).join('');
  let corr = '<div class="empty" style="padding:20px">צריך לפחות שתי מניות כשירות כדי לחשב מתאם.</div>';
  if (sc.corr) {
    const t = sc.corr.tickers, n = t.length;
    const cell = v => { const a = Math.max(0, Math.min(1, Math.abs(v))); return `<div class="num" style="background:rgba(${v >= 0 ? '224,102,210' : '87,210,230'},${(a * 0.7).toFixed(2)})">${v.toFixed(2)}</div>`; };
    corr = `<div class="corr" style="grid-template-columns:52px repeat(${n},minmax(40px,1fr))"><div></div>${t.map(x => `<div class="h">${esc(x)}</div>`).join('')}
      ${t.map((x, i) => `<div class="h">${esc(x)}</div>${sc.corr.matrix[i].map(cell).join('')}`).join('')}</div>
      <p class="hint" style="padding:0 14px 12px">מתאם תשואות יומיות, 60 ימים, בין המועמדות הכשירות מכל האסטרטגיות. ורוד חזק = נעות יחד. שתי עסקאות עם מתאם גבוה הן בפועל אותו סיכון.</p>`;
  }
  const head = s === 'quality' ? 'מהשיא' : 'גאפ';
  return `<section class="rejects">
    <div class="panel"><div class="panel-h">למה מניות נפסלו <span class="sub">${STRAT[s]}, הכלל הראשון שנכשל</span></div><div class="bars">${bars || '<span class="muted">—</span>'}</div>
      ${sc.frozen.length ? `<div class="notes"><div class="note rd">חשד למחיר קפוא (עסקת מזומן): ${sc.frozen.map(esc).join(', ')}</div></div>` : ''}
      ${s === 'quality' && sc.quality_skipped ? `<div class="notes"><div class="note am">${N(sc.quality_skipped)} מניות עברו את שלב המחיר אבל לא נבדקו פונדמנטלית (מגבלת כמות). אפשר להחמיר את ההתייצבות בהגדרות.</div></div>` : ''}</div>
    <div class="panel"><div class="panel-h">כמעט עברו <span class="sub">עברו את התנאי הראשי, נפלו בכלל אחר</span></div>
      <div class="tablewrap" style="max-height:420px"><table class="grid"><thead><tr><th>סימול</th><th>${head}</th><th>RSI</th><th>נפלה ב</th></tr></thead>
      <tbody>${near || '<tr><td colspan="4" class="muted">אין</td></tr>'}</tbody></table></div></div>
    <div class="panel"><div class="panel-h">מתאם בין המועמדות</div>${corr}</div>
  </section>`;
}

// ------------------------------------------------------------------ statistics view
function heat(p, n) {
  if (!isNum(p) || !n) return '<td class="n muted">—</td>';
  const a = (0.12 + 0.55 * p).toFixed(2);
  return `<td class="n" style="background:rgba(87,210,230,${n < 30 ? 0.06 : a})${n < 30 ? ';color:var(--dim)' : ''}">${pctOf(p)}</td>`;
}

function viewStats() {
  const sc = S.scan;
  if (!sc || !sc.gap_stats) {
    $('#view').innerHTML = `<div class="panel empty"><h2>אין עדיין סטטיסטיקה</h2><p>הסטטיסטיקה נבנית בכל סריקה מתוך הנתונים שהורדו. הרץ סריקה במסך הסריקה.</p></div>`;
    return;
  }
  const g = sc.gap_stats;
  const fillTable = dir => `<table class="grid plain"><thead><tr><th>גודל הגאפ</th><th>מגמה לפני הגאפ</th><th>מקרים</th><th>נסגר תוך 5 ימים</th><th>תוך 10</th><th>תוך 20</th></tr></thead><tbody>
    ${g.fill.filter(r => r.dir === dir).map(r => `<tr><td class="n">${r.size}</td><td>${r.trend ? 'מעל SMA150' : 'מתחת ל-SMA150'}</td><td class="n">${fint(r.n)}</td>${heat(r.p5, r.n)}${heat(r.p10, r.n)}${heat(r.p20, r.n)}</tr>`).join('')}</tbody></table>`;
  const condTable = dir => {
    const rows = {};
    g.cond.filter(r => r.dir === dir).forEach(r => { const k = r.size + '|' + r.trend; (rows[k] = rows[k] || { size: r.size, trend: r.trend, ages: {} }).ages[r.age] = r; });
    const ages = ['1-2', '3-5', '6-10'];
    return `<table class="grid plain"><thead><tr><th>גודל</th><th>מגמה</th>${ages.map(a => `<th>פתוח אחרי ${a} ימים</th>`).join('')}</tr></thead><tbody>
      ${Object.values(rows).map(r => `<tr><td class="n">${r.size}</td><td>${r.trend ? 'מעל SMA150' : 'מתחת'}</td>${ages.map(a => { const x = r.ages[a] || {}; return heat(x.p, x.n).replace('</td>', ` <span class="muted">(${fint(x.n)})</span></td>`); }).join('')}</tr>`).join('')}</tbody></table>`;
  };
  const c = g.cont;
  $('#view').innerHTML = `<div class="stack">${backtestHtml(sc)}
    <div class="panel"><div class="panel-h">סטטיסטיקת גאפים: מה רואים כאן</div><div class="panel-b">
      <p style="margin:0 0 8px">כל גאפ מלא של 1% ומעלה ב-${N(fint(sc.usable_n))} המניות של הסריקה, בהיסטוריה של ${({ '1y': 'שנה', '2y': 'שנתיים', '5y': '5 שנים' })[sc.history] || esc(sc.history || '')}: ${N(fint(g.n_events))} מקרים. הטבלאות עונות על שאלה אחת: באיזה אחוז מהמקרים המחיר חזר לסגור את הגאפ, ותוך כמה זמן.</p>
      <p class="hint" style="margin:0">מגבלות: היקום כולל רק מניות שקיימות היום ומעל סף השווי (הטיית שורדים). אין כאן הבחנה היסטורית בין גאפ של דוח לגאפ אחר, ונתוני עבר לא מבטיחים עתיד. תאים עם פחות מ-30 מקרים מוצגים בעמעום, ואין לבסס עליהם החלטה.</p></div></div>
    <div class="row2">
      <div class="panel"><div class="panel-h">גאפים למטה: כמה נסגרו</div><div class="tablewrap">${fillTable('down')}</div></div>
      <div class="panel"><div class="panel-h">גאפים למעלה: כמה נסגרו <span class="sub">אחוז נמוך = המשך חזק יותר</span></div><div class="tablewrap">${fillTable('up')}</div></div>
    </div>
    <div class="panel"><div class="panel-h">גאפ למטה שעדיין פתוח: הסיכוי שייסגר ב-5 הימים הבאים <span class="sub">זה המספר שמופיע ליד כל מועמדת בסגירת גאפ</span></div><div class="tablewrap">${condTable('down')}</div></div>
    <div class="panel"><div class="panel-h">גאפ למעלה שעדיין פתוח: הסיכוי שייסגר ב-5 הימים הבאים <span class="sub">סגירה כאן פוגעת בעסקת המשך</span></div><div class="tablewrap">${condTable('up')}</div></div>
    <div class="panel"><div class="panel-h">בדיקת הכלל של המשך גאפ</div><div class="panel-b">
      ${c ? `<p style="margin:0 0 10px">גאפים עולים, במגמה עולה, במחזור של פי 1.5 לפחות, שעדיין פתוחים ביום החמישי. מה קרה ב-5 ימי המסחר הבאים: חציון ${N(pct(c.median * 100, 2))}, ממוצע ${N(pct(c.mean * 100, 2))}, חיוביים ב-${N(pctOf(c.win))} מהמקרים (${N('N=' + fint(c.n))}).</p>
      <table class="grid plain"><thead><tr><th>גודל הגאפ</th><th>מקרים</th><th>חציון 5 ימים</th><th>חיוביים</th></tr></thead><tbody>
      ${g.cont_by_size.map(r => `<tr><td class="n">${r.size}</td><td class="n">${fint(r.n)}</td><td class="n ${sgn(r.median)}">${pct(r.median * 100, 2)}</td>${heat(r.win, r.n)}</tr>`).join('')}</tbody></table>
      <p class="hint" style="margin:10px 0 0">זה מדד גס של התשואה, לא סימולציה של הכניסה, הסטופ והיעד. חציון קרוב לאפס אומר שהכלל לבדו לא נותן יתרון, והיתרון צריך להגיע מהסינון הנוסף ומניהול הסיכון.</p>`
      : '<p class="muted">אין מספיק מקרים.</p>'}</div></div>
  </div>`;
  drawBacktest(sc);
}

const BT_COLORS = { continuation: '#57D2E6', gapfill: '#E066D2', quality: '#F0B442' };
function backtestHtml(sc) {
  const bt = sc.backtest;
  if (!bt) return `<div class="panel"><div class="panel-h">בדיקה לאחור</div><div class="panel-b muted">הבדיקה לאחור כבויה בהגדרות, או שהסריקה הזו נעשתה בגרסה קודמת.</div></div>`;
  const p = bt.params || {};
  const card = s => {
    const b = bt[s];
    if (!b || !b.trades) return `<div class="panel"><div class="panel-h"><i class="sw" style="background:${BT_COLORS[s]}"></i>${STRAT[s]}</div><div class="panel-b muted">אין עסקאות בבדיקה (${N(fint((b || {}).signals || 0))} אותות).</div></div>`;
    const ex = b.exits || {};
    const cells = [
      ['עסקאות', fint(b.trades)], ['אחוז הצלחה', pctOf(b.win)], ['תוחלת', `<span class="${b.avg_r >= 0 ? 'grn' : 'red'}">${fmt(b.avg_r, 3)}R</span>`],
      ['חציון', fmt(b.median_r, 3) + 'R'], ['Profit factor', fmt(b.pf, 2)], ['לעסקה בדולרים', money(b.usd_per_trade, 2)],
      ['חצי שנה אחרונה', isNum(b.recent_avg_r) ? `<span class="${b.recent_avg_r >= 0 ? 'grn' : 'red'}">${fmt(b.recent_avg_r, 3)}R</span> <span class="muted">(${fint(b.recent_n)})</span>` : '—'],
      ['יציאות TP / SL / זמן', `${pctOf(ex.tp)} / ${pctOf(ex.sl)} / ${pctOf(ex.time)}`],
      ['בוטלו / לא הופעלו / נפסלו', `${fint(b.cancelled)} / ${fint(b.nofill)} / ${fint(b.blocked)}`],
    ];
    return `<div class="panel"><div class="panel-h"><i class="sw" style="background:${BT_COLORS[s]}"></i>${STRAT[s]} <span class="sub">${N(fint(b.signals))} אותות</span></div>
      <div class="plan">${cells.map(([l, v]) => `<div class="cell"><div class="l">${l}</div><div class="v num" style="font-size:15px">${v}</div></div>`).join('')}</div>
      <div class="panel-b" style="padding-top:8px"><table class="grid plain"><thead><tr><th>שנה</th><th>עסקאות</th><th>תוחלת</th><th>הצלחה</th></tr></thead><tbody>
      ${(b.by_year || []).map(y => `<tr><td class="n">${y.year}</td><td class="n">${fint(y.n)}</td><td class="n ${sgn(y.avg_r)}">${fmt(y.avg_r, 3)}R</td><td class="n">${pctOf(y.win)}</td></tr>`).join('')}</tbody></table></div></div>`;
  };
  return `<div class="panel"><div class="panel-h">בדיקה לאחור של הכללים שלך <span class="sub">${dfull(p.first)} עד ${dfull(p.last)}, ${N(fint(sc.usable_n))} מניות</span></div>
    <div class="panel-b"><p style="margin:0 0 8px">כל יום בהיסטוריה שבו מניה עמדה בתנאי המחיר של אסטרטגיה, הורץ כעסקה: Buy Stop ליום הבא, ביטול אם הפתיחה מעל ${N(sc.settings.cancel_gap_pct + '%')}, SL ו-TP לפי הכללים, יציאה בזמן אחרי ${N(p.hold_days)} ימים, ועלות של ${N(p.cost_r + 'R')} לעסקה. בר שנוגע גם ב-SL וגם ב-TP נספר כהפסד.</p>
    <p class="hint" style="margin:0">לא נבדק: סינון דוחות (אין לוח היסטורי), נתוני חברה באיכות בירידה, ומגבלת 2 עסקאות במקביל. היקום הוא המניות של היום (הטיית שורדים). פחות מ-100 עסקאות זה מדגם חלש.</p></div>
    <div class="chart" id="btChart" style="height:240px"></div>
    <div class="legend">${Object.keys(STRAT).map(s => `<span><i style="border-color:${BT_COLORS[s]}"></i>${STRAT[s]}: R מצטבר</span>`).join('')}</div></div>
    <div class="bt3">${Object.keys(STRAT).map(card).join('')}</div>`;
}

function drawBacktest(sc) {
  const el = $('#btChart');
  if (!el || !LW || !sc.backtest) return;
  const ch = LW.createChart(el, chartBase());
  Object.keys(STRAT).forEach(s => {
    const b = sc.backtest[s];
    if (!b || !b.curve || !b.curve.length) return;
    const ls = ch.addLineSeries({ color: BT_COLORS[s], lineWidth: 2, priceLineVisible: false });
    ls.setData(b.curve);
  });
  ch.timeScale().fitContent();
  S.charts.push(ch);
}

// ------------------------------------------------------------------ live view
async function viewLive() {
  $('#view').innerHTML = `<section class="toolbar"><span id="liveState"></span><span class="muted" id="liveUpd"></span><div class="spacer"></div>
      <button class="btn" id="notifBtn">התראות בדפדפן</button><button class="btn primary" id="liveNow">רענן עכשיו</button></section>
    <div class="stack">
      <div class="panel"><div class="panel-h">מעקב חי <span class="sub">העסקאות שנבחרו בסריקה האחרונה, הפוזיציות הפתוחות ורשימת המעקב</span></div><div class="tablewrap" id="liveTable"><div class="empty">טוען...</div></div></div>
      <div class="row2">
        <div class="panel"><div class="panel-h">התראות <span class="spacer"></span><button class="btn ghost" id="alertsClear">נקה</button></div><div id="alertsBox" class="alerts"></div></div>
        <div class="panel"><div class="panel-h">רשימת מעקב</div><div class="panel-b">
          <form id="wlForm" style="display:flex;gap:8px"><input type="text" id="wlInput" placeholder="סימול, למשל NVDA" style="direction:ltr;flex:1;text-transform:uppercase"><button class="btn" type="submit">הוסף</button></form>
          <div class="chips" id="wlChips"></div></div></div>
      </div></div>`;
  $('#liveNow').onclick = () => refreshLiveView(true);
  $('#notifBtn').onclick = async () => {
    if (!('Notification' in window)) { toast('הדפדפן הזה לא תומך בהתראות', true); return; }
    const r = await Notification.requestPermission();
    toast(r === 'granted' ? 'התראות בדפדפן פעילות' : 'ההתראות לא אושרו');
  };
  $('#alertsClear').onclick = async () => { await api('/api/alerts', { method: 'DELETE' }); refreshLiveView(false); };
  $('#wlForm').onsubmit = ev => { ev.preventDefault(); const t = $('#wlInput').value.trim().toUpperCase(); if (t) toggleWatch(t); };
  renderChips();
  await refreshLiveView(false);
  S.liveTimer = setInterval(() => refreshLiveView(false), 15000);
}

function renderChips() {
  const box = $('#wlChips');
  if (!box) return;
  const wl = S.app.watchlist || [];
  box.innerHTML = wl.length ? wl.map(t => `<span class="chip">${esc(t)}<button title="הסר" data-un="${esc(t)}">×</button></span>`).join('') : '<span class="hint">אפשר להוסיף מניות למעקב גם מכרטיס המניה בסריקה.</span>';
  $$('[data-un]', box).forEach(b => b.onclick = () => toggleWatch(b.dataset.un));
}

async function refreshLiveView(force) {
  let L;
  try { L = force ? await api('/api/live/refresh', { method: 'POST', body: {} }) : await api('/api/live'); }
  catch (e) { if (force) toast('הרענון נכשל: ' + e.message, true); return; }
  S.live = L;
  if (S.view !== 'live') return;
  if (force) toast('עודכן');
  const st = L.state || 'closed';
  $('#liveState').innerHTML = `<span class="badge ${st === 'open' ? 'gr' : st === 'closed' ? '' : 'am'}">${LIVE_STATE[st] || st}</span>`;
  $('#liveUpd').textContent = L.updated ? `עודכן ${L.updated.slice(11, 16)}${L.ny_time ? `, ניו יורק ${L.ny_time.slice(11, 16)}` : ''}${L.error ? ' | שגיאה: ' + L.error : ''}` : 'עוד לא עודכן';
  const items = L.items || [];
  $('#liveTable').innerHTML = !items.length ? `<div class="empty"><p>אין מה לעקוב כרגע. כאן יופיעו העסקאות שנבחרו בסריקה האחרונה, הפוזיציות הפתוחות ומניות שהוספת למעקב.</p></div>` :
    `<table class="grid plain"><thead><tr><th>סימול</th><th>סוג</th><th>מחיר</th><th>שינוי</th><th>פרה-מרקט</th><th>פתיחה</th><th>טווח יומי</th><th>רמות</th><th>מרחק</th><th>מצב</th></tr></thead><tbody>
    ${items.map(it => {
      const q = it.q || {}, px = q.price;
      const [cls, label] = LIVE_STATUS[it.status] || ['', it.status || '—'];
      const lv = it.kind === 'watch' ? '—' : `<span class="mag">${fmt(it.entry)}</span> / <span class="amb">${fmt(it.sl)}</span> / <span class="grn">${fmt(it.tp)}</span>`;
      let dist = '—';
      if (isNum(px) && it.kind === 'plan') dist = `לטריגר ${pct((it.entry / px - 1) * 100, 1)}`;
      if (isNum(px) && it.kind === 'position' && it.entry > it.sl) dist = `${fmt((px - it.entry) / (it.entry - it.sl), 2)}R`;
      return `<tr><td class="t">${esc(it.ticker)}</td><td>${KIND[it.kind]}${it.strategy ? ` <span class="muted">${SETUPS[it.strategy] || ''}</span>` : ''}</td>
        <td class="n">${fmt(px)}</td><td class="n ${sgn(q.chg_pct)}">${pct(q.chg_pct)}</td><td class="n">${fmt(q.pre)}</td><td class="n">${fmt(q.session_open)}</td>
        <td class="n">${isNum(q.day_low) ? fmt(q.day_low) + '–' + fmt(q.day_high) : '—'}</td><td class="n">${lv}</td><td class="n">${dist}</td>
        <td><span class="badge ${cls}">${label}${it.status === 'not_today' ? ' ' + dfull(it.entry_date) : ''}</span></td></tr>`;
    }).join('')}</tbody></table>`;
  const al = L.alerts || [];
  $('#alertsBox').innerHTML = al.length ? al.slice(0, 30).map(a => `<div class="alert ${a.type}"><span class="num muted">${dshort(a.time.slice(0, 10))} ${a.time.slice(11, 16)}</span> ${esc(a.text)}</div>`).join('')
    : '<div class="panel-b muted">אין התראות. התראה נשלחת כשהטריגר נכנס, כשהפתיחה מעל סף הביטול, כש-SL או TP נפגעים, וביום היציאה בזמן.</div>';
  renderChips();
  handleAlerts(al);
}

// ------------------------------------------------------------------ alerts everywhere
async function pollAlerts(first) {
  try {
    const L = await api('/api/live');
    S.live = L;
    if (first) { S.lastAlertId = (L.alerts && L.alerts[0] && L.alerts[0].id) || null; updateAlertBadge(L.alerts || []); return; }
    handleAlerts(L.alerts || []);
  } catch (e) { /* server busy */ }
}

function handleAlerts(alerts) {
  updateAlertBadge(alerts);
  if (!alerts.length || alerts[0].id === S.lastAlertId) return;
  const fresh = [];
  for (const a of alerts) { if (a.id === S.lastAlertId) break; fresh.push(a); }
  S.lastAlertId = alerts[0].id;
  if (!fresh.length) return;
  toast(fresh[0].text);
  beep();
  if ('Notification' in window && Notification.permission === 'granted') fresh.slice(0, 3).forEach(a => new Notification('SwingDesk', { body: a.text }));
}

function updateAlertBadge(alerts) {
  const b = $('#nav button[data-view=live]');
  if (!b) return;
  const n = alerts.filter(a => a.time.slice(0, 10) === today()).length;
  b.innerHTML = 'לייב' + (n ? ` <span class="nbadge">${n}</span>` : '');
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0.15, ctx.currentTime); g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
    o.start(); o.stop(ctx.currentTime + 0.35);
  } catch (e) { /* audio blocked */ }
}

// ------------------------------------------------------------------ positions
async function viewPositions() {
  const pos = S.app.positions || [];
  const st = S.app.settings;
  const openRisk = pos.reduce((a, p) => a + Math.max(0, (p.entry - p.sl) * p.qty), 0);
  const slots = Math.max(0, st.max_trades - pos.length);
  const remaining = Math.max(0, st.total_risk - openRisk);
  const per = slots ? Math.min(st.risk_per_trade, remaining / slots) : 0;
  const pf = S.prefill || {};
  const ed = S.editPos ? pos.find(p => p.id === S.editPos) : null;
  const f = ed || pf;
  const h = `<div class="row2">
    <div class="panel"><div class="panel-h">תקציב סיכון</div><div class="panel-b">
      <div class="meter" title="סיכון פתוח מתוך התקציב"><span style="width:${Math.min(100, openRisk / st.total_risk * 100)}%;background:var(--amber)"></span></div>
      <div class="stats" style="margin-top:12px">
        <div class="fact"><div class="l">סיכון פתוח</div><div class="v num">${money(openRisk, 2)}</div></div>
        <div class="fact"><div class="l">נשאר</div><div class="v num">${money(remaining, 2)}</div></div>
        <div class="fact"><div class="l">עסקאות פנויות</div><div class="v num">${slots}</div></div>
        <div class="fact"><div class="l">סיכון לעסקה חדשה</div><div class="v num">${money(per, 2)}</div></div>
      </div><p class="hint">סיכון פתוח = (כניסה − SL) × כמות. הסריקה הבאה מקזזת אותו אוטומטית.</p></div></div>
    <div class="panel"><div class="panel-h">${ed ? 'עריכת פוזיציה' : 'פוזיציה חדשה'} ${pf.ticker && !ed ? '<span class="sub">מולא מהתוכנית, עדכן למחיר הביצוע בפועל</span>' : ''}</div>
      <form id="posForm" class="formgrid">
        <label>סימול<input type="text" name="ticker" required value="${esc(f.ticker || '')}" style="direction:ltr;text-transform:uppercase"></label>
        <label>מחיר כניסה בפועל<input type="number" step="0.01" name="entry" required value="${f.entry ?? ''}"></label>
        <label>SL<input type="number" step="0.01" name="sl" required value="${f.sl ?? ''}"></label>
        <label>TP<input type="number" step="0.01" name="tp" value="${f.tp ?? ''}"></label>
        <label>כמות<input type="number" step="1" name="qty" required value="${f.qty ?? ''}"></label>
        <label>תאריך כניסה<input type="date" name="opened" value="${esc(f.opened || today())}"></label>
        <label>יציאה בזמן<input type="date" name="time_exit" value="${esc(f.time_exit || '')}"></label>
        <label>אסטרטגיה<select name="setup">${['', ...Object.keys(SETUPS)].map(k => `<option value="${k}" ${f.setup === k ? 'selected' : ''}>${k ? SETUPS[k] : '—'}</option>`).join('')}</select></label>
        <label class="wide">הערות<input type="text" name="notes" value="${esc(f.notes || '')}"></label>
        <div class="wide" style="display:flex;gap:8px"><button class="btn primary" type="submit">${ed ? 'שמור שינויים' : 'הוסף פוזיציה'}</button>
        ${ed || pf.ticker ? '<button class="btn ghost" type="button" id="posClear">נקה</button>' : ''}</div>
      </form></div></div>
    <div class="panel" style="margin-top:14px"><div class="panel-h">פוזיציות פתוחות <span class="sub" id="qStamp"></span><span class="spacer"></span><button class="btn" id="qRefresh">רענון מחירים</button></div>
      <div class="tablewrap" id="posTable">${posTable(pos)}</div></div>`;
  $('#view').innerHTML = h;
  $('#posForm').onsubmit = async ev => {
    ev.preventDefault();
    const body = Object.fromEntries(new FormData(ev.target).entries());
    Object.assign(body, { scan_id: f.scan_id || '' });
    try {
      if (ed) await api('/api/positions/' + ed.id, { method: 'PUT', body });
      else await api('/api/positions', { method: 'POST', body });
      S.prefill = null; S.editPos = null;
      S.app = await api('/api/state');
      toast(ed ? 'הפוזיציה עודכנה' : 'הפוזיציה נוספה');
      route('positions');
    } catch (e) { toast('לא נשמר: ' + e.message, true); }
  };
  const pc = $('#posClear'); if (pc) pc.onclick = () => { S.prefill = null; S.editPos = null; route('positions'); };
  $('#qRefresh').onclick = () => refreshQuotes(true);
  bindPosTable();
  refreshQuotes();
}

function posTable(pos) {
  if (!pos.length) return '<div class="empty"><p>אין פוזיציות פתוחות. כשנכנסת לעסקה, רשום אותה כאן כדי שהסריקה תקזז את הסיכון שלה.</p></div>';
  return `<table class="grid plain"><thead><tr><th>סימול</th><th>אסטרטגיה</th><th>כניסה</th><th>SL</th><th>TP</th><th>כמות</th><th>מחיר</th><th>רווח/הפסד</th><th>R</th><th>ל-SL</th><th>ל-TP</th><th>יציאה בזמן</th><th>מצב</th><th></th></tr></thead><tbody>
    ${pos.map(p => {
      const q = S.quotes[p.ticker], px = q ? q.price : null, rps = p.entry - p.sl;
      const pnl = isNum(px) ? (px - p.entry) * p.qty : null, rNow = isNum(px) && rps > 0 ? (px - p.entry) / rps : null;
      const left = weekdaysUntil(p.time_exit);
      let state = '<span class="muted">—</span>';
      if (isNum(px) && px <= p.sl) state = '<span class="badge rd">מתחת ל-SL</span>';
      else if (isNum(px) && p.tp && px >= p.tp) state = '<span class="badge gr">הגיעה ליעד</span>';
      else if (left !== null && p.time_exit && left <= 0) state = '<span class="badge am">יציאה בזמן היום</span>';
      else if (isNum(px)) state = '<span class="badge cy">פתוחה</span>';
      return `<tr><td class="t">${esc(p.ticker)}</td><td>${SETUPS[p.setup] || '—'}</td><td class="n">${fmt(p.entry)}</td><td class="n amb">${fmt(p.sl)}</td><td class="n grn">${fmt(p.tp)}</td>
        <td class="n">${fint(p.qty)}</td><td class="n">${fmt(px)}</td><td class="n ${sgn(pnl)}">${money(pnl, 2)}</td><td class="n ${sgn(rNow)}">${isNum(rNow) ? fmt(rNow, 2) + 'R' : '—'}</td>
        <td class="n">${isNum(px) ? pct((p.sl / px - 1) * 100, 1) : '—'}</td><td class="n">${isNum(px) && p.tp ? pct((p.tp / px - 1) * 100, 1) : '—'}</td>
        <td class="n">${dfull(p.time_exit)}${left !== null && p.time_exit ? ` <span class="muted">(${left} ימים)</span>` : ''}</td><td>${state}</td>
        <td><span class="inl"><input type="number" step="0.01" placeholder="מחיר יציאה" data-exit="${p.id}" value="${isNum(px) ? px.toFixed(2) : ''}"><button class="btn" data-close="${p.id}">סגור</button>
        <button class="btn ghost" data-edit="${p.id}">עריכה</button><button class="btn ghost danger" data-del="${p.id}" title="מחיקה בלי רישום ביומן">מחק</button></span></td></tr>`;
    }).join('')}</tbody></table>`;
}

function bindPosTable() {
  $$('[data-close]').forEach(b => b.onclick = async () => {
    const id = b.dataset.close, px = $(`[data-exit="${id}"]`).value;
    if (!isNum(px)) { toast('הזן מחיר יציאה', true); return; }
    try {
      const j = await api(`/api/positions/${id}/close`, { method: 'POST', body: { exit: +px, date: today() } });
      S.app = await api('/api/state');
      toast(`נסגרה ונרשמה ביומן: ${isNum(j.r) ? fmt(j.r, 2) + 'R' : ''} ${money(j.pnl, 2)}`);
      route('positions');
    } catch (e) { toast(e.message, true); }
  });
  $$('[data-edit]').forEach(b => b.onclick = () => { S.editPos = b.dataset.edit; route('positions'); });
  $$('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('למחוק את הפוזיציה בלי לרשום אותה ביומן?')) return;
    await api('/api/positions/' + b.dataset.del, { method: 'DELETE' });
    S.app = await api('/api/state');
    route('positions');
  });
}

async function refreshQuotes(manual = false) {
  const t = [...new Set((S.app.positions || []).map(p => p.ticker))];
  if (!t.length) return;
  try {
    S.quotes = Object.assign(S.quotes, await api('/api/quotes?t=' + encodeURIComponent(t.join(','))));
    if (S.view !== 'positions') return;
    $('#posTable').innerHTML = posTable(S.app.positions);
    bindPosTable();
    const any = Object.values(S.quotes)[0];
    $('#qStamp').textContent = any ? `מחירים נכון ל-${dfull(any.date)}, בעיכוב של כ-15 דקות` : '';
    if (manual) toast('המחירים עודכנו');
  } catch (e) { if (manual) toast('לא הצלחתי למשוך מחירים: ' + e.message, true); }
}

// ------------------------------------------------------------------ journal
function viewJournal() {
  const J = S.app.journal || [];
  const rs = J.filter(j => isNum(j.r)).map(j => j.r);
  const wins = J.filter(j => j.pnl > 0), losses = J.filter(j => j.pnl <= 0);
  const sumW = wins.reduce((a, j) => a + j.pnl, 0), sumL = losses.reduce((a, j) => a + j.pnl, 0);
  const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
  const byS = Object.keys(SETUPS).map(s => { const x = J.filter(j => j.setup === s && isNum(j.r)); return [s, x.length, avg(x.map(j => j.r))]; }).filter(x => x[1]);
  const stats = [
    ['עסקאות', fint(J.length)], ['אחוז הצלחה', J.length ? fmt(wins.length / J.length * 100, 0) + '%' : '—'],
    ['תוחלת (R ממוצע)', isNum(avg(rs)) ? fmt(avg(rs), 2) + 'R' : '—'], ['רווח/הפסד כולל', money(J.reduce((a, j) => a + j.pnl, 0), 2)],
    ['R ממוצע ברווח', isNum(avg(wins.map(j => j.r).filter(isNum))) ? fmt(avg(wins.map(j => j.r).filter(isNum)), 2) + 'R' : '—'],
    ['R ממוצע בהפסד', isNum(avg(losses.map(j => j.r).filter(isNum))) ? fmt(avg(losses.map(j => j.r).filter(isNum)), 2) + 'R' : '—'],
    ['Profit factor', sumL < 0 ? fmt(sumW / Math.abs(sumL), 2) : '—'],
    ...byS.map(([s, n, a]) => [`${SETUPS[s]} (${n})`, fmt(a, 2) + 'R']),
  ];
  $('#view').innerHTML = `<div class="stack">
    <div class="panel"><div class="stats">${stats.map(([l, v]) => `<div class="fact"><div class="l">${l}</div><div class="v num">${v}</div></div>`).join('')}</div>
      ${J.length < 20 ? `<p class="hint" style="padding:8px 14px;margin:0">${J.length} עסקאות. מתחת ל-20–30 עסקאות הנתונים האלה הם בעיקר רעש. אל תשנה כללים על סמך מדגם קטן.</p>` : ''}</div>
    <div class="panel"><div class="panel-h">עקומת R מצטברת</div><div class="chart" id="jc" style="height:220px;border-top:0"></div></div>
    <div class="panel"><div class="panel-h">עסקאות סגורות</div><div class="tablewrap">${J.length ? `<table class="grid plain"><thead><tr><th>סימול</th><th>אסטרטגיה</th><th>נפתחה</th><th>נסגרה</th><th>כניסה</th><th>SL</th><th>יציאה</th><th>כמות</th><th>רווח/הפסד</th><th>R</th><th>הערות</th><th></th></tr></thead><tbody>
      ${J.map(j => `<tr><td class="t">${esc(j.ticker)}</td><td>${SETUPS[j.setup] || '—'}</td><td class="n">${dfull(j.opened)}</td><td class="n">${dfull(j.closed)}</td><td class="n">${fmt(j.entry)}</td><td class="n">${fmt(j.sl)}</td>
        <td class="n">${fmt(j.exit)}</td><td class="n">${fint(j.qty)}</td><td class="n ${sgn(j.pnl)}">${money(j.pnl, 2)}</td><td class="n ${sgn(j.r)}">${isNum(j.r) ? fmt(j.r, 2) + 'R' : '—'}</td>
        <td class="nm">${esc(j.notes || '')}</td><td><button class="btn ghost danger" data-jdel="${j.id}">מחק</button></td></tr>`).join('')}</tbody></table>`
      : '<div class="empty"><p>היומן ריק. עסקה נכנסת לכאן כשסוגרים פוזיציה במסך הפוזיציות.</p></div>'}</div></div></div>`;
  $$('[data-jdel]').forEach(b => b.onclick = async () => {
    if (!confirm('למחוק את העסקה מהיומן?')) return;
    await api('/api/journal/' + b.dataset.jdel, { method: 'DELETE' });
    S.app = await api('/api/state'); route('journal');
  });
  if (LW && rs.length) {
    const ch = LW.createChart($('#jc'), chartBase({ timeScale: { visible: false } }));
    const s = ch.addBaselineSeries({ baseValue: { type: 'price', price: 0 }, topLineColor: '#63CF86', bottomLineColor: '#EE6A5C',
      topFillColor1: 'rgba(99,207,134,.25)', topFillColor2: 'rgba(99,207,134,.02)', bottomFillColor1: 'rgba(238,106,92,.02)', bottomFillColor2: 'rgba(238,106,92,.25)' });
    let cum = 0;
    const chron = J.filter(j => isNum(j.r)).slice().reverse();
    s.setData(chron.map((j, i) => ({ time: i + 1, value: (cum += j.r) })));
    ch.timeScale().fitContent();
    S.charts.push(ch);
  } else $('#jc').innerHTML = '<div class="empty">אין עדיין נתונים</div>';
}

// ------------------------------------------------------------------ events
function viewEvents() {
  const ev = (S.app.events || []).slice();
  const sc = S.scan;
  const row = (e, i) => {
    const inWin = sc && e.date >= sc.entry_date && e.date <= sc.exit_date;
    const before = sc && e.date >= sc.last_bar && e.date < sc.entry_date;
    return `<tr class="${inWin ? 'inwin' : ''}"><td><input type="date" data-i="${i}" data-f="date" value="${esc(e.date)}"></td>
      <td style="width:55%"><input type="text" data-i="${i}" data-f="title" value="${esc(e.title)}" style="width:100%"></td>
      <td><select data-i="${i}" data-f="impact">${Object.entries(IMPACT).map(([k, v]) => `<option value="${k}" ${e.impact === k ? 'selected' : ''}>${v}</option>`).join('')}</select></td>
      <td>${inWin ? '<span class="badge am">בחלון ההחזקה</span>' : before ? '<span class="badge cy">לפני הכניסה</span>' : e.date < today() ? '<span class="muted">עבר</span>' : ''}</td>
      <td><button class="btn ghost danger" data-evdel="${i}">מחק</button></td></tr>`;
  };
  $('#view').innerHTML = `<div class="panel"><div class="panel-h">אירועי מאקרו ודוחות של ענקיות <span class="sub">${sc ? `חלון ההחזקה הנוכחי: ${dfull(sc.entry_date)} עד ${dfull(sc.exit_date)}` : ''}</span></div>
    <div class="tablewrap"><table class="grid plain"><thead><tr><th>תאריך</th><th>אירוע</th><th>השפעה</th><th>מצב</th><th></th></tr></thead><tbody id="evBody">${ev.map(row).join('')}</tbody></table></div>
    <div class="actions"><button class="btn" id="evAdd">הוסף אירוע</button><button class="btn primary" id="evSave">שמור</button>
    <span class="hint" style="align-self:center">הרשימה ההתחלתית אומתה ב-28.10.2026. דוחות של מניות בודדות נבדקים אוטומטית בסריקה.</span></div></div>`;
  const collect = () => {
    $$('#evBody [data-i]').forEach(inp => { ev[+inp.dataset.i][inp.dataset.f] = inp.value; });
    return ev;
  };
  $('#evAdd').onclick = () => { collect(); ev.push({ date: today(), title: '', impact: 'medium' }); S.app.events = ev; viewEvents(); };
  $$('[data-evdel]').forEach(b => b.onclick = () => { collect(); ev.splice(+b.dataset.evdel, 1); S.app.events = ev; viewEvents(); });
  $('#evSave').onclick = async () => {
    try { S.app.events = await api('/api/events', { method: 'PUT', body: collect().filter(e => e.date && e.title) }); toast('האירועים נשמרו'); renderAnnun(); viewEvents(); }
    catch (e) { toast(e.message, true); }
  };
}

// ------------------------------------------------------------------ settings
function viewSettings() {
  const s = S.app.settings, d = S.app.defaults;
  const field = ([k, label, type, hint]) => {
    if (type === 'textarea') return `<label class="wide">${label}<textarea name="${k}" rows="3" style="direction:ltr">${esc(s[k])}</textarea><span class="hint">${hint}</span></label>`;
    if (type === 'text') return `<label>${label}<input type="text" name="${k}" value="${esc(s[k])}" style="direction:ltr" autocomplete="off"></label>`;
    if (Array.isArray(type)) return `<label>${label}<select name="${k}">${type.map(([v, t]) => `<option value="${v}" ${+s[k] === v ? 'selected' : ''}>${t}</option>`).join('')}</select><span class="hint">ברירת מחדל: ${(type.find(x => x[0] === +d[k]) || [0, d[k]])[1]}</span></label>`;
    return `<label>${label}<input type="number" step="any" name="${k}" value="${s[k]}"><span class="hint">ברירת מחדל: ${N(d[k])}</span></label>`;
  };
  const tgHelp = `<div class="panel-b" style="padding-top:0"><p class="hint" style="margin:0 0 8px">חיבור טלגרם: בטלגרם פותחים את <b>@BotFather</b>, שולחים /newbot ומעתיקים את הטוקן לשדה כאן. אחר כך שולחים הודעה כלשהי לבוט החדש, שומרים את ההגדרות, ולוחצים "זהה Chat ID".</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" type="button" id="tgDetect">זהה Chat ID</button><button class="btn" type="button" id="tgTest">שלח הודעת בדיקה</button>
    <span class="hint" style="align-self:center">${S.app.tg_ready ? 'טלגרם מחובר.' : 'טלגרם עוד לא מחובר.'}</span></div></div>`;
  const topic = s.ntfy_topic || '';
  const nst = (S.app.remote || {}).ntfy;
  const ntfyHelp = `<div class="panel-b phonehelp">
    <ol class="steps">
      <li>מתקינים בטלפון את האפליקציה <b>ntfy</b> (App Store או Google Play. חינם, בלי הרשמה ובלי מספר טלפון).</li>
      <li>${topic ? 'זה הערוץ הפרטי שלך:' : 'יוצרים ערוץ פרטי בלחיצה:'}
        <div class="topicbox">${topic ? `<span class="topic num">${esc(topic)}</span><button class="btn" type="button" id="ntfyCopy">העתק</button>` : ''}
        <button class="btn ${topic ? 'ghost' : 'primary'}" type="button" id="ntfyNew">${topic ? 'צור ערוץ חדש' : 'צור ערוץ פרטי'}</button></div></li>
      <li>באפליקציה לוחצים <b>+</b>, מקלידים את שם הערוץ בדיוק כמו כאן, משאירים את השרת ntfy.sh ולוחצים Subscribe. באייפון מאשרים התראות.</li>
      <li><button class="btn ${topic ? 'primary' : ''}" type="button" id="ntfyTest" ${topic ? '' : 'disabled'}>שלח הודעת בדיקה</button>
        <span class="hint">אמורה להגיע לטלפון תוך כמה שניות, גם בסלולר.</span></li>
    </ol>
    <p class="hint" style="margin:6px 0 0">שם הערוץ הוא המפתח: מי שיודע אותו יכול לקרוא את ההתראות. לא לשתף ולא להחליף לשם פשוט.
      ${nst ? (nst.ok ? `שליחה אחרונה: ${esc(nst.time.slice(11, 16))}, הצליחה.` : `<span class="red">שליחה אחרונה נכשלה (${esc(nst.error || '')}).</span>`) : ''}</p></div>`;
  const cloudHelp = S.app.cloud
    ? `<div class="panel-b" style="padding-top:0"><p class="hint" style="margin:0">זה האתר עצמו, ולכן השדות האלה לא בשימוש כאן. הם מיועדים ל-SwingDesk שרץ במחשב בבית: אם Yahoo חוסם את השרת בענן, המחשב בבית סורק ושולח לכאן כל סריקה.</p></div>`
    : `<div class="panel-b" style="padding-top:0"><p class="hint" style="margin:0 0 8px">כשממלאים כאן את הכתובת והסיסמה, כל סריקה שרצה במחשב הזה נשלחת גם לאתר. זה הגיבוי למקרה ש-Yahoo חוסם את השרת בענן.</p>
      <button class="btn" type="button" id="cloudPush">שלח את הסריקה האחרונה לאתר</button></div>`;
  const autoHelp = `<div class="panel-b" style="padding-top:0"><p class="hint" style="margin:0">הסריקה האוטומטית רצה רק כשחלון SwingDesk פתוח, פעם אחת לכל יום מסחר, אחרי הסגירה בארה"ב. ${S.app.auto_scan_last && S.app.auto_scan_last.session ? `הריצה האחרונה: יום המסחר ${dfull(S.app.auto_scan_last.session)}.` : ''}</p></div>`;
  $('#view').innerHTML = `<form id="setForm" class="stack">
    ${SETTINGS.map(([group, fields]) => `<div class="panel"><div class="panel-h">${group}</div><div class="formgrid">${fields.map(field).join('')}</div>${group === 'טלפון: התראות' ? ntfyHelp : group === 'לייב וטלגרם' ? tgHelp : group === 'סריקה אוטומטית' ? autoHelp : group === 'אתר SwingDesk' ? cloudHelp : ''}</div>${group === 'טלפון: התראות' ? '<div class="panel" id="remotePanel"></div>' : ''}`).join('')}
    <div style="display:flex;gap:8px"><button class="btn primary" type="submit">שמור הגדרות</button><button class="btn ghost" type="button" id="setReset">חזרה לברירת מחדל</button>
    <span class="hint" style="align-self:center">השינויים חלים מהסריקה הבאה. סריקות קודמות נשארות כמו שהן.</span></div></form>`;
  const saveForm = async () => {
    S.app.settings = await api('/api/settings', { method: 'PUT', body: Object.fromEntries(new FormData($('#setForm')).entries()) });
    S.app = await api('/api/state');
  };
  $('#setForm').onsubmit = async ev => {
    ev.preventDefault();
    try { await saveForm(); toast('ההגדרות נשמרו'); viewSettings(); } catch (e) { toast(e.message, true); }
  };
  renderRemote(S.app.remote);
  const nc = $('#ntfyCopy');
  if (nc) nc.onclick = async () => toast(await copyText(topic) ? 'שם הערוץ הועתק' : 'ההעתקה נכשלה', false);
  const nn = $('#ntfyNew');
  nn.onclick = async () => {
    if (topic && !nn.dataset.sure) { nn.dataset.sure = '1'; nn.textContent = 'בטוח? הטלפון יצטרך להירשם לערוץ החדש'; return; }
    try { await saveForm(); await api('/api/ntfy/topic', { method: 'POST', body: {} }); S.app = await api('/api/state'); toast('נוצר ערוץ פרטי. עכשיו נרשמים אליו באפליקציה.'); viewSettings(); }
    catch (e) { toast(e.message, true); }
  };
  $('#ntfyTest').onclick = async () => {
    try { await saveForm(); await api('/api/ntfy/test', { method: 'POST', body: {} }); toast('נשלחה הודעת בדיקה לטלפון'); }
    catch (e) { toast('השליחה נכשלה: ' + e.message, true); }
  };
  const cp = $('#cloudPush');
  if (cp) cp.onclick = async () => {
    try { await api('/api/cloud/push', { method: 'POST', body: {} }); toast('הסריקה נשלחה לאתר'); }
    catch (e) { toast('השליחה נכשלה: ' + e.message + '. ודא שהכתובת והסיסמה נשמרו.', true); }
  };
  $('#tgTest').onclick = async () => {
    try { await api('/api/telegram/test', { method: 'POST', body: {} }); toast('הודעת בדיקה נשלחה לטלגרם'); }
    catch (e) { toast('השליחה נכשלה: ' + e.message + '. ודא שהטוקן וה-Chat ID נשמרו.', true); }
  };
  $('#tgDetect').onclick = async () => {
    try {
      const r = await api('/api/telegram/detect', { method: 'POST', body: {} });
      S.app = await api('/api/state'); toast(`Chat ID זוהה: ${r.chat} ${r.name}`); viewSettings();
    } catch (e) { toast(e.message.includes('no messages') ? 'לא נמצאה הודעה. שלח הודעה לבוט בטלגרם ונסה שוב.' : 'הזיהוי נכשל: ' + e.message + '. שמור קודם את הטוקן.', true); }
  };
  $('#setReset').onclick = async () => {
    const keep = ['tg_token', 'tg_chat', 'ntfy_topic', 'ntfy_server', 'ntfy_token', 'cloud_url', 'cloud_token'];
    S.app.settings = await api('/api/settings', { method: 'PUT', body: Object.assign({}, d, Object.fromEntries(keep.map(k => [k, s[k]]))) });
    toast('חזרה לברירת המחדל'); viewSettings();
  };
}

// ------------------------------------------------------------------ remote access panel (settings)
const REMOTE_STATE = { off: ['כבוי', 'muted'], installing: ['מתקין רכיב חיבור', 'am'], starting: ['מתחבר', 'am'], on: ['פעיל', 'gr'], error: ['תקלה, מנסה שוב', 'rd'] };

function renderRemote(st) {
  const box = $('#remotePanel');
  if (!box || !st) return;
  const a = S.app;
  const [label, cls] = REMOTE_STATE[st.state] || [st.state, 'muted'];
  const head = `<div class="panel-h">גישה לדשבורד מכל מקום <span class="badge ${cls === 'muted' ? '' : cls}" style="${cls === 'muted' ? 'color:var(--muted)' : ''}">${label}</span></div>`;
  if (a.remote_session) {
    box.innerHTML = head + `<div class="panel-b"><p class="hint" style="margin:0">אתה מחובר עכשיו מבחוץ, דרך הקישור המאובטח. הפעלה, כיבוי וסיסמה משנים רק מהמחשב שמריץ את SwingDesk.</p></div>`;
    return;
  }
  if (st.cloud) {
    box.innerHTML = head + `<div class="panel-b"><p class="hint" style="margin:0">זה האתר בענן, והוא כבר נגיש מכל מקום עם הסיסמה שלו.</p></div>`;
    return;
  }
  let status = '';
  if (st.state === 'installing') status = `מוריד את רכיב החיבור cloudflared מ-Cloudflare${isNum(st.install_pct) ? ` (${st.install_pct}%)` : ''}. קורה רק בפעם הראשונה.`;
  else if (st.state === 'starting') status = 'פותח חיבור מאובטח ל-Cloudflare. בדרך כלל עד חצי דקה.';
  else if (st.state === 'error') status = `<span class="red">${esc(st.error || 'החיבור נפל')}</span>. SwingDesk מנסה שוב לבד.`;
  else if (st.state === 'on') status = `<div class="remoteurl"><a class="num cyn" href="${esc(st.url)}" target="_blank" rel="noopener">${esc(st.url)}</a>
      <button class="btn" type="button" id="rmCopy">העתק</button>
      ${a.ntfy_ready ? '<button class="btn primary" type="button" id="rmSend">שלח את הקישור לטלפון</button>' : ''}</div>
      <span class="hint">פעיל מאז ${esc((st.since || '').slice(11, 16))}. ${a.ntfy_ready ? 'הקישור נשלח לטלפון אוטומטית, וכל התראה פותחת אותו.' : 'כדאי להגדיר למטה התראות לטלפון: כך הקישור יגיע אליך לבד בכל פעם שהוא מתחלף.'}</span>`;
  const pw = st.has_password
    ? `<span>סיסמה: <b class="grn">מוגדרת</b></span> <button class="btn ghost" type="button" id="rmPwShow">שנה סיסמה</button>
       <span id="rmPwBox" hidden><input type="password" id="rmPw" autocomplete="new-password" placeholder="סיסמה חדשה, 8 תווים לפחות" dir="ltr"><button class="btn" type="button" id="rmPwSave">שמור</button></span>`
    : `<span id="rmPwBox"><input type="password" id="rmPw" autocomplete="new-password" placeholder="בחר סיסמה, 8 תווים לפחות" dir="ltr"><button class="btn primary" type="button" id="rmPwSave">שמור סיסמה</button></span>`;
  const on = st.enabled && st.state !== 'off';
  const cmd = a.start_cmd || '';
  box.innerHTML = head + `<div class="panel-b remote">
    <p class="hint" style="margin:0 0 10px">פותח לדשבורד הזה כתובת https מאובטחת דרך Cloudflare, כך שאפשר להיכנס מהטלפון גם בסלולר ומחוץ לבית. בלי חשבון ובלי לפתוח פורטים בראוטר. כל כניסה מבחוץ דורשת סיסמה.
      בהפעלה הראשונה יורד פעם אחת רכיב החיבור הרשמי של Cloudflare, בערך 20 מגה.</p>
    <div class="rmrow">${pw}</div>
    <div class="rmrow"><button class="btn ${on ? 'ghost danger' : 'primary'}" type="button" id="rmToggle" ${st.has_password || on ? '' : 'disabled'}>${on ? 'כבה גישה מבחוץ' : 'הפעל גישה מבחוץ'}</button>
      ${st.has_password ? '' : '<span class="hint">קודם בוחרים סיסמה.</span>'}</div>
    ${status ? `<div class="rmrow rmstatus">${status}</div>` : ''}
    <details class="rmneeds"><summary>מה צריך כדי שזה יעבוד כשאני בחוץ</summary>
      <ul><li>המחשב בבית דלוק, מחובר לחשמל ולאינטרנט. במחשב נייד המכסה נשאר פתוח: מכסה סגור מרדים את המק.</li>
      <li>SwingDesk רץ בחלון הטרמינל. כל עוד הוא רץ, המק לא נכנס לשינה (הגדרה "למנוע שינה" למטה).</li>
      <li>אחרי שמפעילים כאן פעם אחת, הגישה עולה לבד בכל הפעלה של SwingDesk, גם בפקודה הרגילה:</li></ul>
      ${cmd ? `<div class="cmd"><code dir="ltr">${esc(cmd)}</code><button class="btn" type="button" id="rmCmd">העתק</button></div>` : ''}
      <ul><li>הכתובת מתחלפת בכל הפעלה מחדש של SwingDesk. הכתובת החדשה נשלחת לטלפון ב-ntfy, ולחיצה על כל התראה פותחת את הכתובת העדכנית. בכתובת חדשה מקלידים שוב את הסיסמה.</li></ul>
    </details></div>`;
  const q = id => $('#' + id);
  if (q('rmCopy')) q('rmCopy').onclick = async () => toast(await copyText(st.url) ? 'הקישור הועתק' : 'ההעתקה נכשלה');
  if (q('rmCmd')) q('rmCmd').onclick = async () => toast(await copyText(cmd) ? 'הפקודה הועתקה' : 'ההעתקה נכשלה');
  if (q('rmSend')) q('rmSend').onclick = async () => {
    try { await api('/api/remote/notify', { method: 'POST', body: {} }); toast('הקישור נשלח לטלפון'); } catch (e) { toast('השליחה נכשלה: ' + e.message, true); }
  };
  if (q('rmPw')) q('rmPw').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); q('rmPwSave').click(); } };
  if (q('rmPwShow')) q('rmPwShow').onclick = () => { q('rmPwBox').hidden = false; q('rmPwShow').hidden = true; q('rmPw').focus(); };
  if (q('rmPwSave')) q('rmPwSave').onclick = async () => {
    const v = q('rmPw').value;
    if (v.length < 8) { toast('הסיסמה קצרה מדי: לפחות 8 תווים', true); return; }
    try { S.app.remote = await api('/api/remote/password', { method: 'POST', body: { password: v } }); toast(st.has_password ? 'הסיסמה הוחלפה. מי שהיה מחובר מבחוץ צריך להיכנס שוב.' : 'הסיסמה נשמרה'); renderRemote(S.app.remote); }
    catch (e) { toast(e.message, true); }
  };
  q('rmToggle').onclick = async () => {
    try {
      S.app.remote = await api(on ? '/api/remote/stop' : '/api/remote/start', { method: 'POST', body: {} });
      toast(on ? 'הגישה מבחוץ כובתה' : 'מפעיל גישה מבחוץ...'); renderRemote(S.app.remote); renderTop(); pollRemote();
    } catch (e) { toast(e.message, true); }
  };
  pollRemote();
}

function pollRemote() {
  if (S.remoteTimer) clearTimeout(S.remoteTimer);
  const st = S.app.remote || {};
  const fast = ['installing', 'starting'].includes(st.state);
  S.remoteTimer = setTimeout(async () => {
    S.remoteTimer = null;
    if (S.view !== 'settings' || S.app.remote_session) return;
    try {
      const prev = S.app.remote || {};
      const nx = await api('/api/remote');
      S.app.remote = nx;
      const ae = document.activeElement;
      const typing = ae && ae.id === 'rmPw';
      if (!typing && (nx.state !== prev.state || nx.url !== prev.url || nx.install_pct !== prev.install_pct || nx.error !== prev.error)) { renderRemote(nx); renderTop(); return; }
    } catch (e) { /* server busy */ }
    pollRemote();
  }, fast ? 1500 : 8000);
}

document.addEventListener('DOMContentLoaded', init);

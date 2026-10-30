'use strict';
/* SwingDesk: Opportunities view and the sync control. Uses helpers from app.js. */

const OPP_STATUS = { ready: ['gr', 'מוכנה'], watch: ['cy', 'להמתין לאישור'], risky: ['rd', 'מסוכנת'] };
const OPP_TAG = { drop: 'ירידה חדה', gap: 'גאפ למטה', oversold: 'מכירת יתר', capitulation: 'קפיטולציה', support200: 'על SMA200', reversal: 'נר היפוך' };
const LEVEL = { r382: 'תיקון 38.2%', r50: 'תיקון 50%', r618: 'תיקון 61.8%', sma50: 'SMA50', sma200: 'SMA200', gap: 'סגירת הגאפ', high: 'השיא' };
const BASIS = { full: 'אותו עומק ירידה, סוג, התייצבות ומגמה', no_trend: 'אותו עומק ירידה, סוג והתייצבות', type: 'אותו סוג והתייצבות', stab: 'אותה רמת התייצבות', none: '' };
const OPP_SORT = {
  default: ['סטטוס וציון', null], score: ['ציון', r => -r.score], rr: ['יחס סיכוי לסיכון', r => -(r.plan.rr2 || 0)],
  drop: ['עומק הירידה', r => r.dd_pct], risk: ['סיכון נמוך', r => r.risk_pct],
  quality: ['איכות', r => -((r.qual_avail ? r.qual_n / r.qual_avail : 0) + (r.qual_avail >= 4 ? 1 : 0))],
  edge: ['תוחלת היסטורית', r => -(((r.base || {}).n >= 30 ? (r.base || {}).avg_r : -9))],
};
S.oppF = S.oppF || { status: 'all', tags: [], maxRisk: '', minRR: '', noEarn: false, deepOnly: false, q: '', sort: 'default' };
S.oppQ = S.oppQ || { quotes: {} };
S.oppSel = S.oppSel || null;
S.sync = S.sync || null;

const sg = (x, d = 1) => pct(x, d);
const lvlName = k => LEVEL[k] || k || '';

// ------------------------------------------------------------------ why / risks in Hebrew
function whyText(w, op) {
  switch (w.code) {
    case 'drop': return `ירדה מ-${N(fmt(w.from))} (${N(dfull(w.from_date))}) ל-${N(fmt(w.to))}: ${N(sg(w.pct), 'dn')} ב-${N(w.days)} ימי מסחר. השפל ${N(fmt(w.low))} (${N(dfull(w.low_date))}), ומאז ${N(sg(w.rebound), 'up')}.`;
    case 'market':
      if (w.spy <= -5) return `באותה תקופה SPY ירד ${N(sg(w.spy))}. חלק גדול מהירידה הוא של השוק כולו, וירידות כאלה נוטות לחזור מהר יותר מירידה ספציפית.`;
      if (w.spy >= 0) return `באותה תקופה SPY עלה ${N(sg(w.spy))}. הירידה ספציפית למניה, ולכן צריך להבין את הסיבה בחדשות למטה.`;
      return `באותה תקופה SPY זז ${N(sg(w.spy))}: רוב הירידה ספציפית למניה.`;
    case 'gap': {
      let t = `גאפ למטה של ${N(sg(w.pct), 'dn')} ב-${N(dfull(w.date))}, עדיין פתוח (${N(w.age)} ימים). רמת הסגירה ${N(fmt(w.level))}, ${N(sg(w.to_fill_pct), 'up')} מעל המחיר.`;
      if (w.prob && isNum(w.prob.p)) t += ` בהיסטוריה של היקום גאפים דומים שהיו פתוחים כך נסגרו בתוך 10 ימים ב-${N(pctOf(w.prob.p))} מהמקרים (${N('N=' + fint(w.prob.n))}).`;
      return t;
    }
    case 'asym': return `סיכון ${N(fmt(w.risk_pct, 1) + '%', 'amb')} עד הסטופ, מול ${N(sg(w.to_t1), 'grn')} ל-T1 (${lvlName(w.t1_kind)})${w.t2 ? ` ו-${N(sg(w.to_t2), 'grn')} ל-T2 (${lvlName(w.t2_kind)})` : ''}. כלומר ${N(fmt(w.rr1, 1) + 'R')}${w.t2 ? ` עד ${N(fmt(w.rr2, 1) + 'R')}` : ''}. עד השיא הקודם ${N(sg(w.to_high, 0))}.`;
    case 'stab': return `${N(w.n + '/5')} סימני התייצבות: המכירה נחלשת והקונים חוזרים.`;
    case 'reversal': return 'נר היפוך ביום האחרון: פטיש אחרי שפל חדש, או בליעה שורית.';
    case 'capitulation': return 'יום קפיטולציה ב-10 הימים האחרונים: מחזור של פי 2.5 ומעלה, עם סגירה בחצי העליון של הטווח. לרוב זה סימן שהמוכרים האחרונים יצאו.';
    case 'oversold': return 'RSI ירד מתחת ל-30 בשבוע האחרון: מכירת יתר.';
    case 'support200': return 'המחיר על SMA200 העולה (בטווח של 3%), תמיכה שמוסדיים מגינים עליה.';
    case 'long_trend': return w.above ? 'המחיר מעל SMA200: הירידה היא תיקון בתוך מגמה ארוכה עולה.' : 'SMA200 עדיין עולה: המגמה הארוכה לא נשברה.';
    case 'quality': return `איכות עסקית ${N(w.n + '/' + w.avail)}: העסק עצמו בריא, הירידה היא במחיר.`;
    case 'valuation': return `מכפיל רווח קדימה ${N(fmt(w.fpe, 1))}${isNum(w.pe) ? ` (היום ${N(fmt(w.pe, 1))})` : ''}${isNum(w.p_fcf) ? `, מחיר לתזרים חופשי ${N(fmt(w.p_fcf, 1))}` : ''}.`;
    case 'analyst': return `יעד האנליסטים הממוצע ${N(sg(w.upside, 0), 'up')} מעל הכניסה (${N(fint(w.n))} אנליסטים, דירוג ${N(fmt(w.rec, 1))} מתוך 5, כש-1 היא קנייה חזקה).`;
    case 'base': return `במצבים דומים ביקום (${BASIS[w.basis] || ''}): ${N(fint(w.n))} מקרים, הצלחה ${N(pctOf(w.win))}, תוחלת ${N(fmt(w.avg_r, 2) + 'R', 'grn')}. העלייה המקסימלית החציונית ב-${op.opp_hold} ימים: ${N(sg(w.mfe), 'up')}.`;
    default: return esc(w.code);
  }
}

function riskText(w, op, deepN) {
  switch (w.code) {
    case 'knife': return 'סכין נופלת: שפל חדש של 10 ימים היום, ופחות מ-2 סימני התייצבות. לא לתפוס.';
    case 'no_stab': return `רק ${N(w.n + '/5')} סימני התייצבות (נדרשים ${w.need}). להמתין לאישור: פריצה של השיא היומי היא האישור.`;
    case 'earnings_soon': return `דוח ב-${N(dfull(w.date))}, בעוד ${N(w.sessions)} ימי מסחר. זה אירוע בינארי, ועדיף לחכות שיעבור.`;
    case 'earnings_in_hold': return `דוח ב-${N(dfull(w.date))} (בעוד ${N(w.sessions)} ימים), בתוך חלון ההחזקה של ${op.opp_hold} ימים.`;
    case 'earnings_drop': return `הירידה התחילה ביום דוח (${N(dfull(w.date))}). ירידה על מידע חדש נוטה להמשיך (post-earnings drift).`;
    case 'below200_falling': return 'מתחת ל-SMA200 יורד: המגמה הארוכה יורדת, והסיכוי להתאוששות נמוך יותר.';
    case 'quality_weak': return `איכות עסקית חלשה: ${N(w.n + '/' + w.avail)}. ייתכן שהירידה משקפת בעיה אמיתית.`;
    case 'fund_unknown': return w.deep ? 'נתוני החברה לא זמינים מ-Yahoo כרגע. לבדוק את העסק לפני כניסה.' : `נתוני החברה לא נבדקו, כי היא לא בין ${N(deepN)} המובילות.`;
    case 'debt': return `חוב גבוה: חוב להון ${N(fmt(w.de / 100, 1))}.`;
    case 'fcf_neg': return 'תזרים חופשי שלילי: החברה שורפת מזומנים.';
    case 'wide_risk': return `סיכון של ${N(fmt(w.pct, 1) + '%')} עד הסטופ, מעל הסף של ${N(w.max + '%')}.`;
    case 'no_room': return 'אין התנגדות שנותנת לפחות 1R: הסטופ רחוק מדי ביחס לפוטנציאל הקרוב.';
    case 'trigger_far': return `הטריגר ${N(fmt(w.pct, 1) + '%')} מעל הסגירה.`;
    case 'short_high': return `short interest של ${N(pctOf(w.pct))} מהמניות הצפות. זה יכול להאיץ עלייה (short squeeze), אבל גם מעיד שיש מי שמהמר נגד.`;
    case 'market_weak': return 'SPY מתחת ל-SMA150: בשוק חלש יש פחות סיכוי לריבאונד.';
    case 'earnings_unknown': return 'תאריך הדוח הבא לא ידוע. לבדוק לפני כניסה.';
    case 'base': return `במצבים דומים ביקום (${BASIS[w.basis] || ''}) התוחלת שלילית: ${N(fmt(w.avg_r, 2) + 'R', 'red')}, הצלחה ${N(pctOf(w.win))} (${N('N=' + fint(w.n))}).`;
    default: return 'שגיאה בניתוח המניה';
  }
}

// ------------------------------------------------------------------ data helpers
function oppRows() {
  const op = S.scan && S.scan.opps;
  if (!op) return [];
  const f = S.oppF, q = f.q.trim().toUpperCase();
  let rows = op.rows.filter(r => (f.status === 'all' || r.status === f.status)
    && (!f.tags.length || f.tags.some(t => r.tags.includes(t)))
    && (!f.maxRisk || r.risk_pct <= +f.maxRisk)
    && (!f.minRR || (r.plan.rr2 || 0) >= +f.minRR)
    && (!f.noEarn || !(isNum(r.earnings_sessions) && r.earnings_sessions <= 5))
    && (!f.deepOnly || r.deep)
    && (!q || r.ticker.includes(q) || (r.name || '').toUpperCase().includes(q)));
  const key = OPP_SORT[f.sort] && OPP_SORT[f.sort][1];
  if (key) rows = rows.slice().sort((a, b) => key(a) - key(b));
  return rows;
}

function liveOf(t) { return (S.oppQ.quotes || {})[t] || null; }

function liveState(r) {
  const q = liveOf(r.ticker);
  if (!q || !isNum(q.price)) return null;
  if (q.price <= r.plan.stop) return ['rd', 'שברה תמיכה'];
  if (isNum(q.day_high) && q.day_high >= r.plan.entry && !q.stale) return ['mag', 'הופעלה'];
  return null;
}

// ------------------------------------------------------------------ view
function viewOpps() {
  const sc = S.scan;
  let h = staleBanner();
  if (!sc || !sc.opps) {
    h += `<div class="panel empty"><h2>אין עדיין הזדמנויות</h2><p>${sc ? 'הסריקה הזו נעשתה בלי חיפוש הזדמנויות. ' : ''}לחץ סנכרון למעלה כדי להריץ סריקה מלאה. הסריקה עוברת על כל היקום ומחפשת מניות שירדו חזק או פתחו בגאפ למטה, ובודקת לכל אחת סיכון, פוטנציאל, איכות ומה קרה במצבים דומים בעבר.</p>
      <button class="btn primary" id="oppScan">הרץ סריקה מלאה</button></div>`;
    $('#view').innerHTML = h;
    $('#oppScan').onclick = () => doSync(true);
    bindStale();
    return;
  }
  const op = sc.opps, c = op.counts, f = S.oppF, P = op.params || {};
  const b = op.base || {};
  const conf = b.confirmed || {}, early = b.early || {};
  h += `<section class="facts">
    <div class="fact"><div class="l">מוכנות</div><div class="v num grn">${c.ready}</div></div>
    <div class="fact"><div class="l">להמתין לאישור</div><div class="v num cyn">${c.watch}</div></div>
    <div class="fact"><div class="l">מסוכנות</div><div class="v num red">${c.risky}</div></div>
    <div class="fact"><div class="l">נבדקו מתוך היקום</div><div class="v">${N(fint(op.total))} <span class="muted" style="font-size:12px">מתוך ${N(fint(sc.usable_n))}</span></div></div>
    <div class="fact"><div class="l">כניסה אחרי התייצבות, בהיסטוריה</div><div class="v">${conf.n ? `${N(fmt(conf.avg_r, 2) + 'R', conf.avg_r >= 0 ? 'grn' : 'red')} <span class="muted" style="font-size:12px">הצלחה ${pctOf(conf.win)}, N=${fint(conf.n)}</span>` : '—'}</div></div>
    <div class="fact"><div class="l">כניסה מוקדמת, בהיסטוריה</div><div class="v">${early.n ? `${N(fmt(early.avg_r, 2) + 'R', early.avg_r >= 0 ? 'grn' : 'red')} <span class="muted" style="font-size:12px">הצלחה ${pctOf(early.win)}, N=${fint(early.n)}</span>` : '—'}</div></div>
  </section>`;
  if (op.market_weak) h += `<p class="note am" style="margin:-4px 0 12px">SPY מתחת ל-SMA150: השוק חלש. ריבאונדים בשוק כזה נוטים להיות קצרים, ולכן כדאי להקטין גודל או להחמיר בדרישת ההתייצבות.</p>`;
  if (sc.demo) h += `<p class="note am" style="margin:-4px 0 12px">נתוני דמו סינתטיים. הסימולים לא קיימים בבורסה.</p>`;
  const chip = (val, label, n) => `<button class="${f.status === val ? 'on' : ''}" data-st="${val}">${label}${isNum(n) ? ` <span class="cnt">${n}</span>` : ''}</button>`;
  h += `<section class="tabs opptools">
    ${chip('all', 'הכל', op.rows.length)}${chip('ready', 'מוכנות', c.ready)}${chip('watch', 'להמתין', c.watch)}${chip('risky', 'מסוכנות', c.risky)}
    <span class="sep"></span>
    ${Object.entries(OPP_TAG).map(([k, v]) => `<button class="tagf ${f.tags.includes(k) ? 'on' : ''}" data-tag="${k}">${v}</button>`).join('')}
  </section>
  <section class="toolbar oppfilters">
    <label class="inline">סיכון מקסימלי <select id="fRisk">${['', 5, 8, 10, 15].map(v => `<option value="${v}" ${String(f.maxRisk) === String(v) ? 'selected' : ''}>${v ? v + '%' : 'הכל'}</option>`).join('')}</select></label>
    <label class="inline">יחס מינימלי <select id="fRR">${['', 1.5, 2, 3, 4].map(v => `<option value="${v}" ${String(f.minRR) === String(v) ? 'selected' : ''}>${v ? v + 'R' : 'הכל'}</option>`).join('')}</select></label>
    <label class="inline"><input type="checkbox" id="fEarn" ${f.noEarn ? 'checked' : ''}> בלי דוח בשבוע הקרוב</label>
    <label class="inline"><input type="checkbox" id="fDeep" ${f.deepOnly ? 'checked' : ''}> רק חברות שנבדקו</label>
    <label class="inline">מיון <select id="fSort">${Object.entries(OPP_SORT).map(([k, v]) => `<option value="${k}" ${f.sort === k ? 'selected' : ''}>${v[0]}</option>`).join('')}</select></label>
    <input type="text" id="fQ" placeholder="חיפוש סימול" value="${esc(f.q)}" style="direction:ltr;width:130px">
    <div class="spacer"></div>
    <span class="muted" id="oppLiveStamp"></span>
    <a class="btn" href="/api/scans/${esc(sc.id)}/report.md">דוח ל-Claude</a>
  </section>
  <section class="split opps">
    <div class="panel"><div class="panel-h">הזדמנויות <span class="sub">מוכנה = ${P.opp_min_stab}/5 התייצבות, יחס ${P.opp_min_rr}R לפחות, סיכון עד ${P.opp_max_risk}%, בלי דוח ב-3 ימים</span></div>
      <div class="tablewrap" id="oppWrap"></div></div>
    <aside class="panel detail" id="oppDetail"></aside>
  </section>
  ${oppBaseHtml(op)}`;
  $('#view').innerHTML = h;
  bindStale();
  $$('[data-st]').forEach(b2 => b2.onclick = () => { f.status = b2.dataset.st; renderOppTable(); markTools(); });
  $$('[data-tag]').forEach(b2 => b2.onclick = () => { const t = b2.dataset.tag; f.tags = f.tags.includes(t) ? f.tags.filter(x => x !== t) : [...f.tags, t]; renderOppTable(); markTools(); });
  $('#fRisk').onchange = e => { f.maxRisk = e.target.value; renderOppTable(); };
  $('#fRR').onchange = e => { f.minRR = e.target.value; renderOppTable(); };
  $('#fEarn').onchange = e => { f.noEarn = e.target.checked; renderOppTable(); };
  $('#fDeep').onchange = e => { f.deepOnly = e.target.checked; renderOppTable(); };
  $('#fSort').onchange = e => { f.sort = e.target.value; renderOppTable(); };
  $('#fQ').oninput = e => { f.q = e.target.value; renderOppTable(); };
  renderOppTable();
  refreshOppQuotes();
  S.liveTimer = setInterval(() => refreshOppQuotes(), 15000);
}

function markTools() {
  $$('[data-st]').forEach(x => x.classList.toggle('on', x.dataset.st === S.oppF.status));
  $$('[data-tag]').forEach(x => x.classList.toggle('on', S.oppF.tags.includes(x.dataset.tag)));
}

function renderOppTable(skipDetail = false) {
  const rows = oppRows();
  const wrap = $('#oppWrap');
  if (!wrap) return;
  if (!rows.length) {
    wrap.innerHTML = `<div class="empty"><h2>אין הזדמנויות בסינון הזה</h2><p>אפשר להרחיב את הסינון. אם אין בכלל מניות מוכנות, זו תשובה לגיטימית: לא כל יום יש הזדמנות טובה.</p></div>`;
    if (!rows.some(r => r.ticker === S.oppSel)) { S.oppSel = null; if (!skipDetail) renderOppDetail(); }
    return;
  }
  if (!S.oppSel || !rows.some(r => r.ticker === S.oppSel)) S.oppSel = rows[0].ticker;
  wrap.innerHTML = `<table class="grid" id="oppTable"><thead><tr><th>סימול</th><th>סטטוס</th><th>ציון</th><th>סוג</th><th>מ־ → ל־</th><th>ירידה</th><th>מהשפל</th><th>עכשיו</th>
    <th>סיכון</th><th>T1 / T2</th><th>יחס</th><th>איכות</th><th>התייצבות</th><th>היסטוריה</th><th>דוח</th><th>שם</th></tr></thead><tbody>
    ${rows.map(r => {
      const [cls, lab] = OPP_STATUS[r.status] || ['', r.status];
      const q = liveOf(r.ticker), ls = liveState(r), b = r.base || {};
      return `<tr data-opp="${esc(r.ticker)}" class="${S.oppSel === r.ticker ? 'sel' : ''}">
        <td class="t">${esc(r.ticker)}${r.held ? ' <span class="muted" title="יש פוזיציה פתוחה">●</span>' : ''}</td>
        <td><span class="badge ${cls}">${lab}</span>${ls ? ` <span class="badge ${ls[0]}">${ls[1]}</span>` : ''}</td>
        <td class="n"><span class="scorebar"><i style="width:${r.score}%"></i></span>${fmt(r.score, 0)}</td>
        <td class="tagcell">${r.tags.map(t => `<span class="tg">${OPP_TAG[t]}</span>`).join('')}</td>
        <td class="n" title="שיא ${dfull(r.high_date)}">${fmt(r.high)} → ${fmt(r.close)}</td>
        <td class="n"><span class="dn">${sg(r.dd_pct)}</span> <span class="muted">${r.days_since_high}י׳</span></td>
        <td class="n up">${sg(r.rebound_pct)}</td>
        <td class="n">${q && isNum(q.price) ? `${fmt(q.price)} <span class="${sgn(q.chg_pct)}">${sg(q.chg_pct)}</span>` : '<span class="muted">—</span>'}</td>
        <td class="n ${r.risk_pct > (S.scan.opps.params.opp_max_risk || 10) ? 'red' : 'amb'}">${fmt(r.risk_pct, 1)}%</td>
        <td class="n grn">${r.plan.t1 ? sg(r.to_t1_pct, 0) : '—'}${r.plan.t2 ? ' / ' + sg(r.to_t2_pct, 0) : ''}</td>
        <td class="n">${r.plan.rr2 ? fmt(r.plan.rr2, 1) + 'R' : '—'}</td>
        <td class="n">${r.deep ? `${r.qual_n}/${r.qual_avail}` : '<span class="muted">—</span>'}</td>
        <td class="n">${r.stab_n}/5</td>
        <td class="n">${b.n >= 30 ? `<span class="${b.avg_r >= 0 ? 'grn' : 'red'}">${fmt(b.avg_r, 2)}R</span> <span class="muted">${fint(b.n)}</span>` : '—'}</td>
        <td class="n">${r.next_earnings ? `${dshort(r.next_earnings)} <span class="${r.earnings_sessions <= 5 ? 'red' : 'muted'}">(${r.earnings_sessions})</span>` : (r.deep ? '<span class="muted">?</span>' : '—')}</td>
        <td class="nm" title="${esc(r.name)}">${esc(r.name)}</td></tr>`;
    }).join('')}</tbody></table>`;
  $$('#oppTable tr[data-opp]').forEach(tr => tr.onclick = () => {
    S.oppSel = tr.dataset.opp;
    $$('#oppTable tr[data-opp]').forEach(x => x.classList.toggle('sel', x.dataset.opp === S.oppSel));
    renderOppDetail();
    if (window.innerWidth < 1100) $('#oppDetail').scrollIntoView({ behavior: 'smooth' });
  });
  if (!skipDetail) renderOppDetail();
}

async function refreshOppQuotes() {
  try { S.oppQ = await api('/api/oppquotes'); } catch (e) { return; }
  if (S.view !== 'opps') return;
  const st = $('#oppLiveStamp');
  if (st) st.textContent = S.oppQ.updated ? `מחירים חיים: ${S.oppQ.updated.slice(11, 16)}${S.oppQ.error ? ' (שגיאה)' : ''}` : 'מחירים חיים: עוד לא';
  const wrap = $('#oppWrap');
  if (wrap) {
    const y = wrap.scrollTop;
    renderOppTable(true);
    wrap.scrollTop = y;
  }
  renderOppLive();
}

function oppBaseHtml(op) {
  const b = op.base;
  if (!b || !b.n) return '';
  const cell = r => `<td class="n">${fint(r.n)}</td>${heat(r.win, r.n)}<td class="n ${sgn(r.avg_r)}">${fmt(r.avg_r, 2)}R</td><td class="n up">${sg(r.mfe)}</td><td class="n dn">${sg(r.mae)}</td>`;
  const a200y = b.above200_yes || {}, a200n = b.above200_no || {};
  return `<div class="panel" style="margin-top:14px"><div class="panel-h">מה ההיסטוריה של היקום מלמדת <span class="sub">${fint(b.n)} עסקאות מדומות, יציאה ב-T1, בסטופ, או אחרי ${op.params.opp_hold} ימים</span></div>
    <div class="panel-b"><p style="margin:0 0 8px">כל יום שבו מניה ביקום עמדה בתנאי הזדמנות (ירידה של ${op.params.opp_min_drop}% ומעלה או גאפ למטה פתוח), הורץ כעסקה עם הכללים של המסך הזה: Buy Stop מעל השיא היומי, סטופ מתחת לשפל של ${op.params.opp_stop_bars} ימים, ו-T1 כיעד. אחרי התייצבות: ${conf2(b.confirmed)}. כניסה מוקדמת: ${conf2(b.early)}. במגמה ארוכה עולה (מעל SMA200): ${conf2(a200y)}. מתחת ל-SMA200: ${conf2(a200n)}.</p>
    <p class="hint" style="margin:0">MFE = העלייה המקסימלית בתקופה, MAE = הירידה המקסימלית, שתיהן חציון. לא נבדק לאחור: נתוני חברה ודוחות. היקום כולל רק מניות שקיימות היום, כך שמניות שקרסו ונמחקו לא נספרות (הטיית שורדים), והתוצאות כנראה אופטימיות מעט.</p></div>
    <div class="tablewrap" style="max-height:none"><table class="grid plain"><thead><tr><th>עומק הירידה</th><th>גאפ למטה</th><th>התייצבות</th><th>מקרים</th><th>הצלחה</th><th>תוחלת</th><th>MFE</th><th>MAE</th></tr></thead><tbody>
    ${b.table.map(r => `<tr><td class="n">${r.ddb}</td><td>${r.gap ? 'כן' : 'לא'}</td><td>${r.stab ? 'יש' : 'אין'}</td>${cell(r)}</tr>`).join('')}</tbody></table></div></div>`;
}

function conf2(x) {
  return x && x.n ? `${N(fmt(x.avg_r, 2) + 'R', x.avg_r >= 0 ? 'grn' : 'red')}, הצלחה ${N(pctOf(x.win))} (${N('N=' + fint(x.n))})` : '—';
}

// ------------------------------------------------------------------ detail
async function renderOppDetail() {
  destroyCharts();
  const el = $('#oppDetail');
  if (!el) return;
  const op = S.scan.opps, P = op.params;
  const r = op.rows.find(x => x.ticker === S.oppSel);
  if (!r) { el.innerHTML = '<div class="empty"><p>בחר מניה מהרשימה.</p></div>'; return; }
  const fresh = S.oppDetailFor !== r.ticker;
  S.oppDetailFor = r.ticker;
  const p = r.plan, [cls, lab] = OPP_STATUS[r.status] || ['', r.status];
  const sz = r.size || {};
  const cells = [
    ['כניסה (Buy Stop)', fmt(p.entry), 'mag'], ['סטופ', fmt(p.stop), 'am'], ['סיכון', `${fmt(r.risk_pct, 1)}% <span class="muted" style="font-size:12px">(${money(p.risk, 2)} למניה)</span>`, 'am'],
    ['T1: ' + lvlName(p.t1_kind), p.t1 ? `${fmt(p.t1)} <span class="muted" style="font-size:12px">${sg(r.to_t1_pct)}, ${fmt(p.rr1, 1)}R</span>` : '—', 'gr'],
    ['T2: ' + lvlName(p.t2_kind), p.t2 ? `${fmt(p.t2)} <span class="muted" style="font-size:12px">${sg(r.to_t2_pct)}, ${fmt(p.rr2, 1)}R</span>` : '—', 'gr'],
    ['פוטנציאל עד השיא', `${fmt(r.high)} <span class="muted" style="font-size:12px">${sg(r.to_high_pct, 0)}</span>`],
    [`כמות לסיכון ${money(sz.per_trade)}`, fint(sz.qty) + (sz.capped ? ' <span class="muted" style="font-size:12px">(הוגבל)</span>' : '')], ['חשיפה', money(sz.exposure)],
    ['רווח ב-T1 / T2', `${money(sz.reward1)} / ${money(sz.reward2)}`],
    ['ביטול אם פותח מעל', fmt(p.cancel_above)], ['כניסה ביום', dfull(p.entry_date)], ['יציאה בזמן', dfull(p.time_exit)],
  ];
  const b = r.base || {};
  let h = `<div class="dh"><span class="tk">${esc(r.ticker)}</span><span class="nm">${esc(r.name || '')}</span>
      <span class="badge ${cls}">${lab}</span><span class="badge cy">ציון ${fmt(r.score, 0)}</span>
      <div class="meta">${r.sector ? `<span>${esc(r.sector)}, ${esc(r.industry)}</span>` : ''}<span>RSI ${N(fmt(r.rsi, 0))}</span><span>ATR ${N(fmt(r.atr_pct, 1) + '%')}</span>${isNum(r.rs_rank) ? `<span>RS ${N(r.rs_rank)}</span>` : ''}${isNum(r.ret_12m) ? `<span>12 חודשים ${N(sg(r.ret_12m, 0), sgn(r.ret_12m))}</span>` : ''}</div></div>
    <div class="story">${whyText(r.why[0] || { code: 'drop', from: r.high, from_date: r.high_date, to: r.close, pct: r.dd_pct, days: r.days_since_high, low: r.low, low_date: r.low_date, rebound: r.rebound_pct }, P)}</div>
    <div class="tagrow">${r.tags.map(t => `<span class="tg">${OPP_TAG[t]}</span>`).join('')}</div>
    <div class="livebox" id="oppLive"></div>
    <div class="chart" id="oc1" style="height:330px"></div><div class="chart rsi" id="oc2"></div>
    <div class="legend"><span><i style="border-color:var(--cyan)"></i>SMA50</span><span><i style="border-color:#B08CF0"></i>SMA200</span>
      <span><i style="border-color:rgba(141,163,176,.8);border-top-style:dotted"></i>שיא, שפל ורמות תיקון</span>
      <span><i style="border-color:var(--magenta);border-top-style:dashed"></i>כניסה</span><span><i style="border-color:var(--amber)"></i>סטופ</span><span><i style="border-color:var(--green)"></i>T1, T2</span></div>
    <div class="plan">${cells.map(([l, v, c]) => `<div class="cell"><div class="l">${l}</div><div class="v num ${c || ''}">${v}</div></div>`).join('')}</div>
    <div class="twocol"><div><div class="colh grn">למה זו הזדמנות</div><div class="notes">${r.why.slice(1).map(w => `<div class="note cy">${whyText(w, P)}</div>`).join('') || '<span class="muted">—</span>'}</div></div>
    <div><div class="colh red">מה יכול להשתבש</div><div class="notes">${r.risks.map(w => `<div class="note ${['knife', 'earnings_soon', 'wide_risk', 'no_room', 'quality_weak', 'base'].includes(w.code) ? 'rd' : 'am'}">${riskText(w, P, op.deep_n)}</div>`).join('') || '<span class="muted">לא נמצאו סיכונים בולטים בנתונים. זה לא אומר שאין.</span>'}</div></div></div>
    ${subChecks('סימני התייצבות', r.stab, STAB)}${r.qual ? subChecks('איכות עסקית', r.qual, QUAL) : ''}
    ${r.fund ? fundHtml(r) : ''}
    ${b.n ? `<div class="basebox"><div class="l">במצבים דומים ביקום <span class="muted">(${BASIS[b.basis] || ''})</span></div>
      <div class="plan">${[['מקרים', fint(b.n)], ['הצלחה', pctOf(b.win)], ['תוחלת', `<span class="${b.avg_r >= 0 ? 'grn' : 'red'}">${fmt(b.avg_r, 2)}R</span>`],
        ['עלייה מקסימלית חציונית', `<span class="up">${sg(b.mfe)}</span>`], ['רבעון עליון', `<span class="up">${sg(b.mfe75)}</span>`], ['ירידה מקסימלית חציונית', `<span class="dn">${sg(b.mae)}</span>`]]
        .map(([l, v]) => `<div class="cell"><div class="l">${l}</div><div class="v num" style="font-size:15px">${v}</div></div>`).join('')}</div></div>` : ''}
    <div class="whatif"><label>כניסה<input type="number" step="0.01" id="owE" value="${p.entry}"></label>
      <label>סטופ<input type="number" step="0.01" id="owS" value="${p.stop}"></label>
      <label>יעד<select id="owT">${p.t1 ? `<option value="${p.t1}">T1 ${fmt(p.t1)}</option>` : ''}${p.t2 ? `<option value="${p.t2}" selected>T2 ${fmt(p.t2)}</option>` : ''}<option value="${r.high}">שיא ${fmt(r.high)}</option></select></label>
      <div class="out" id="owOut"></div></div>
    <div class="actions"><button class="btn" id="oCopy">העתק פקודה</button><button class="btn" id="oPos">רשום כפוזיציה פתוחה</button>
      <button class="btn ghost" id="watchBtn">${(S.app.watchlist || []).includes(r.ticker) ? 'הסר מהמעקב בלייב' : 'הוסף למעקב בלייב'}</button></div>
    <div class="news"><div class="panel-h">חדשות אחרונות <span class="sub">כאן בודקים מה הפיל את המניה</span></div><div class="newsb" id="newsBox"><span class="muted">טוען חדשות...</span></div></div>`;
  el.innerHTML = h;
  if (fresh) el.scrollTop = 0;
  renderOppLive();
  bindOppWhatIf(r);
  $('#watchBtn').onclick = () => toggleWatch(r.ticker);
  loadOppNews(r.ticker);
  let cd = S.scan.charts[r.ticker];
  if (!cd || !cd.sma200) {
    $('#oc1').innerHTML = '<div class="empty">טוען גרף...</div>';
    try { cd = await api('/api/chart?t=' + encodeURIComponent(r.ticker)); } catch (e) { $('#oc1').innerHTML = `<div class="empty">אין נתוני גרף: ${esc(e.message)}</div>`; return; }
    if (S.oppSel !== r.ticker) return;
    $('#oc1').innerHTML = '';
  }
  drawOppChart(cd, r, $('#oc1'), $('#oc2'));
  bindOppWhatIf(r);
}

async function loadOppNews(t) {
  const box = $('#newsBox');
  try {
    const items = await api('/api/news?t=' + encodeURIComponent(t));
    if (S.oppSel !== t || !$('#newsBox')) return;
    box.innerHTML = items.length ? items.map(n => `<div class="newsi">${n.link ? `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)}
      <div class="muted">${esc(n.publisher || '')}${n.time ? ' | ' + esc(ago(n.time)) : ''}</div></div>`).join('') : '<span class="muted">אין חדשות זמינות.</span>';
  } catch (e) { box.innerHTML = `<span class="muted">לא הצלחתי למשוך חדשות: ${esc(e.message)}</span>`; }
}

function renderOppLive() {
  const el = $('#oppLive');
  if (!el || !S.scan || !S.scan.opps) return;
  const r = S.scan.opps.rows.find(x => x.ticker === S.oppSel);
  if (!r) return;
  const q = liveOf(r.ticker);
  if (!q || !isNum(q.price)) { el.innerHTML = '<span class="muted">מחיר חי: עוד לא נמשך. לחץ סנכרון למעלה.</span>'; return; }
  const ls = liveState(r);
  const toTrig = (r.plan.entry / q.price - 1) * 100;
  el.innerHTML = `<span>עכשיו ${N(fmt(q.price), 'cyn')} ${N(sg(q.chg_pct), sgn(q.chg_pct))}</span>
    ${isNum(q.pre) ? `<span>פרה-מרקט ${N(fmt(q.pre))}</span>` : ''}${isNum(q.session_open) ? `<span>פתיחה ${N(fmt(q.session_open))}</span>` : ''}
    ${isNum(q.day_low) ? `<span>טווח ${N(fmt(q.day_low) + '–' + fmt(q.day_high))}</span>` : ''}
    <span>${toTrig > 0 ? `לטריגר ${N(sg(toTrig))}` : 'מעל הטריגר'}</span>${ls ? `<span class="badge ${ls[0]}">${ls[1]}</span>` : ''}
    <span class="muted">${q.time ? 'נכון ל-' + esc(String(q.time).slice(11, 16)) + ' שעון ניו יורק' : ''}${q.stale ? ', לא מהיום' : ''}</span>`;
}

function bindOppWhatIf(r) {
  const upd = () => {
    const e = +$('#owE').value, s = +$('#owS').value, t = +$('#owT').value, risk = e - s;
    const out = $('#owOut');
    if (!(risk > 0)) { out.innerHTML = '<span class="red">הסטופ חייב להיות מתחת לכניסה.</span>'; return; }
    const per = (r.size || {}).per_trade || S.app.settings.risk_per_trade;
    const maxExp = S.app.settings.capital / Math.max(1, S.app.settings.max_trades);
    let q = Math.floor(per / risk), capped = false;
    if (q * e > maxExp) { q = Math.floor(maxExp / e); capped = true; }
    out.innerHTML = `כמות ${N(fint(q))} | חשיפה ${N(money(q * e))} | סיכון ${N(money(q * risk, 2))} (${N(fmt(risk / e * 100, 1) + '%')}) | רווח ביעד ${N(money(q * (t - e), 2), 'grn')} | יחס ${N(fmt((t - e) / risk, 2) + 'R')}`
      + (capped ? ' <span class="amb">(הכמות הוגבלה לפי חשיפה)</span>' : '');
    if (S.candles) setOppPlanLines(e, s, r.plan.t1, t);
    S.oppWhatIf = { e, s, t, q };
  };
  ['#owE', '#owS', '#owT'].forEach(id => { const x = $(id); if (x) x.oninput = upd; if (x && x.tagName === 'SELECT') x.onchange = upd; });
  upd();
  $('#oCopy').onclick = async () => {
    const w = S.oppWhatIf;
    const txt = `BUY STOP ${r.ticker} x${w.q} @ ${w.e} | SL ${w.s} | TP ${w.t}${r.plan.t1 && r.plan.t1 !== w.t ? ` (T1 ${r.plan.t1})` : ''} | בטל אם הפתיחה מעל ${r.plan.cancel_above} | יציאה בזמן ${dfull(r.plan.time_exit)}`;
    toast(await copyText(txt) ? 'הפקודה הועתקה' : txt);
  };
  $('#oPos').onclick = () => {
    const w = S.oppWhatIf;
    S.prefill = { ticker: r.ticker, entry: w.e, sl: w.s, tp: w.t, qty: w.q, time_exit: r.plan.time_exit, setup: 'opportunity', scan_id: S.scan.id, opened: today(), notes: `הזדמנות: ${r.tags.map(t => OPP_TAG[t]).join(', ')}` };
    route('positions');
  };
}

function drawOppChart(cd, r, el1, el2) {
  if (!LW || !cd || !cd.bars || !cd.bars.length) return;
  const ch = LW.createChart(el1, chartBase());
  const p = r.plan;
  S.extra = [p.entry, p.stop, p.t1, p.t2, r.low].filter(isNum);
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
  if (cd.sma200) {
    const l200 = ch.addLineSeries({ color: '#B08CF0', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    l200.setData(cd.sma200);
  }
  const pl = (price, color, title, style, width = 1, label = true) => isNum(price) && candles.createPriceLine({ price, color, title, lineStyle: style, lineWidth: width, axisLabelVisible: label });
  const lv = r.levels || {};
  pl(r.high, 'rgba(141,163,176,.85)', 'שיא', LW.LineStyle.SparseDotted);
  pl(r.low, 'rgba(141,163,176,.85)', 'שפל', LW.LineStyle.SparseDotted);
  ['r382', 'r50', 'r618'].forEach(k => pl(lv[k], 'rgba(141,163,176,.45)', LEVEL[k].replace('תיקון ', ''), LW.LineStyle.Dotted, 1, false));
  if (isNum(lv.gap)) pl(lv.gap, 'rgba(87,210,230,.8)', 'סגירת גאפ', LW.LineStyle.Dotted);
  const markers = [];
  const has = d => cd.bars.some(b => b.time === d);
  if (has(r.high_date)) markers.push({ time: r.high_date, position: 'aboveBar', color: '#8DA3B0', shape: 'circle', text: 'שיא' });
  if (has(r.low_date)) markers.push({ time: r.low_date, position: 'belowBar', color: '#8DA3B0', shape: 'circle', text: 'שפל' });
  if (r.gap && has(r.gap.date)) markers.push({ time: r.gap.date, position: 'aboveBar', color: '#57D2E6', shape: 'arrowDown', text: 'גאפ' });
  if (markers.length) candles.setMarkers(markers.sort((a, b) => a.time.localeCompare(b.time)));
  S.candles = candles;
  S.lines = {};
  setOppPlanLines(p.entry, p.stop, p.t1, p.t2);
  ch.timeScale().setVisibleLogicalRange({ from: Math.max(0, cd.bars.length - Math.max(90, r.days_since_high + 25)), to: cd.bars.length + 4 });
  const base2 = chartBase({ timeScale: { visible: false, borderColor: '#27404F', rightOffset: 6 }, handleScroll: false, handleScale: false });
  base2.layout = Object.assign({}, base2.layout, { attributionLogo: false });
  const rc = LW.createChart(el2, base2);
  const rm = new Map(cd.rsi.map(x => [x.time, x.value]));
  const rs = rc.addLineSeries({ color: '#57D2E6', lineWidth: 1.5, priceLineVisible: false, crosshairMarkerVisible: false, priceFormat: { type: 'price', precision: 0, minMove: 1 } });
  rs.setData(cd.bars.map(b => rm.has(b.time) ? { time: b.time, value: rm.get(b.time) } : { time: b.time }));
  [30, 70].forEach(x => rs.createPriceLine({ price: x, color: 'rgba(141,163,176,.55)', lineStyle: LW.LineStyle.Dotted, lineWidth: 1, axisLabelVisible: true, title: '' }));
  ch.timeScale().subscribeVisibleLogicalRangeChange(rg => { if (rg) rc.timeScale().setVisibleLogicalRange(rg); });
  rc.timeScale().setVisibleLogicalRange(ch.timeScale().getVisibleLogicalRange());
  S.charts.push(ch, rc);
}

function setOppPlanLines(entry, stop, t1, t2) {
  const c = S.candles;
  if (!c) return;
  Object.values(S.lines || {}).forEach(l => { try { c.removePriceLine(l); } catch (e) { /* noop */ } });
  const mk = (price, color, title, style, w = 2) => isNum(price) ? c.createPriceLine({ price, color, title, lineStyle: style, lineWidth: w, axisLabelVisible: true }) : null;
  S.lines = {
    e: mk(entry, '#E066D2', 'כניסה', LW.LineStyle.Dashed), s: mk(stop, '#F0B442', 'סטופ', LW.LineStyle.Solid),
    t1: mk(t1, '#63CF86', 'T1', LW.LineStyle.Solid), t2: t2 !== t1 ? mk(t2, '#63CF86', t2 === S.scan.opps.rows.find(x => x.ticker === S.oppSel)?.high ? 'שיא' : 'T2', LW.LineStyle.Dashed) : null,
  };
  Object.keys(S.lines).forEach(k => { if (!S.lines[k]) delete S.lines[k]; });
  S.extra = [entry, stop, t1, t2].filter(isNum);
}

// ------------------------------------------------------------------ sync control
function syncHtml() {
  return `<div class="sync"><button class="btn syncbtn" id="syncBtn" title="מושך מחירים חיים לכל המסכים. אם נסגר יום מסחר חדש מאז הסריקה האחרונה, מריץ סריקה מלאה."><i class="dot" id="syncDot"></i><span id="syncLbl">סנכרון</span></button>
    <label class="inline autosync" title="סנכרון אוטומטי כל דקה בזמן מסחר, כל עוד החלון של SwingDesk פתוח"><input type="checkbox" id="autoSync" ${S.app.settings.auto_sync ? 'checked' : ''}> רציף</label>
    <span class="muted syncinfo" id="syncInfo"></span></div>`;
}

function bindSync() {
  $('#syncBtn').onclick = () => doSync(false);
  $('#autoSync').onchange = async e => {
    try {
      S.app.settings = await api('/api/settings', { method: 'PUT', body: { auto_sync: e.target.checked ? 1 : 0 } });
      toast(e.target.checked ? 'סנכרון רציף פעיל: מחירים חיים כל דקה בזמן מסחר' : 'סנכרון רציף כבוי');
      pollSync();
    } catch (err) { toast(err.message, true); e.target.checked = !e.target.checked; }
  };
  renderSync();
}

const hhmm = t => t ? String(t).slice(11, 16) : '';
function renderSync() {
  const st = S.sync, dot = $('#syncDot'), info = $('#syncInfo');
  if (!st || !dot) return;
  const t = st.last_sync || st.opp_updated || st.live_updated;
  const age = t ? (Date.now() - new Date(t).getTime()) / 1000 : Infinity;
  const interval = Math.max(20, +S.app.settings.live_interval || 60);
  let cls = 'ok', txt = '';
  if (st.job && st.job.state === 'running') { cls = 'run'; txt = `סורק ${st.job.pct || 0}%`; }
  else if (!st.has_scan) { cls = 'bad'; txt = 'אין סריקה. לחץ סנכרון'; }
  else if (st.stale) { cls = 'bad'; txt = `נתונים יומיים מ-${dshort(st.last_bar)}. לחץ סנכרון`; }
  else if (st.market_state === 'closed') { cls = 'ok'; txt = `שוק סגור. נר ${dshort(st.last_bar)}${t ? ', מחירים ' + hhmm(t) : ''}`; }
  else if (age > 3 * interval) { cls = 'warn'; txt = `${LIVE_STATE[st.market_state] || ''}. עודכן ${hhmm(t) || 'מזמן'}`; }
  else { cls = 'ok'; txt = `${LIVE_STATE[st.market_state] || ''}. חי ${hhmm(t)}`; }
  dot.className = 'dot ' + cls;
  info.textContent = txt;
  const cb = $('#autoSync'); if (cb) cb.checked = !!st.auto_sync;
}

async function doSync(full) {
  const btn = $('#syncBtn');
  if (btn) { btn.disabled = true; $('#syncLbl').textContent = 'מסנכרן...'; }
  try {
    const r = await api('/api/sync', { method: 'POST', body: { full: !!full } });
    S.sync = r;
    S.app = Object.assign(S.app, await api('/api/state'));
    if (r.scan_started) { toast(r.has_scan ? 'נסגר יום מסחר חדש. מתחילה סריקה מלאה על נתונים עדכניים.' : 'מתחילה סריקה מלאה.'); startPolling(); }
    else toast('סונכרן: מחירים חיים, מדדים ופוזיציות עודכנו.');
    await refreshOppQuotes();
    S.market = null; loadMarket();
    if (!r.scan_started && ['live', 'positions'].includes(S.view)) route(S.view);
  } catch (e) { toast('הסנכרון נכשל: ' + e.message, true); }
  finally { if (btn) { btn.disabled = false; $('#syncLbl').textContent = 'סנכרון'; } renderSync(); }
}

async function pollSync() {
  try {
    const st = await api('/api/sync');
    S.sync = st;
    if (st.remote && S.app) {
      const pr = S.app.remote || {};
      if (pr.state !== st.remote.state || pr.url !== st.remote.url) { S.app.remote = Object.assign({}, pr, st.remote); renderTop(); }
    }
    renderSync();
    handleAlerts(st.alerts || []);
    if (S.view === 'opps' || S.view === 'scan') { const b = $('#staleBox'); if (b) b.outerHTML = staleBanner(); bindStale(); }
  } catch (e) { /* server busy */ }
}

function staleBanner() {
  const st = S.sync;
  if (!st || !st.stale || !st.has_scan || (st.job && st.job.state === 'running')) return '<div id="staleBox"></div>';
  return `<div id="staleBox" class="stale"><span>הנתונים היומיים מ-${N(dfull(st.last_bar))}, ומאז נסגר יום מסחר נוסף (${N(dfull(st.expected_session))}).</span><button class="btn primary" id="staleSync">סנכרן עכשיו</button></div>`;
}

function bindStale() { const b = $('#staleSync'); if (b) b.onclick = () => doSync(true); }

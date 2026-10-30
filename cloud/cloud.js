// SwingDesk cloud dashboard (GitHub Pages). Read-only.
// The page asks for the password once, derives the key (PBKDF2-SHA256), decrypts data.bin (AES-256-GCM)
// and answers the dashboard's /api calls from it. Scans and price checks run on GitHub on a schedule.
(() => {
  'use strict';
  const LS = 'sd_cloud_key_v1';
  const READONLY = 'בגרסת הענן אפשר רק לצפות. הסריקות ובדיקות המחירים רצות לבד ב-GitHub.';
  let meta = null, bundle = null, loading = null, lastMetaCheck = 0;

  const hex = h => Uint8Array.from(h.match(/../g).map(x => parseInt(x, 16)));
  const b64e = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
  const b64d = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const store = {
    get() { try { return JSON.parse(localStorage.getItem(LS) || 'null'); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(LS, JSON.stringify(v)); } catch (e) { /* private mode: ask again next time */ } },
    clear() { try { localStorage.removeItem(LS); } catch (e) { /* ignore */ } },
  };

  async function fetchMeta() {
    const r = await fetch('meta.json?t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) throw new Error('האתר עוד לא פורסם. הריצה הראשונה ב-GitHub עוד לא הסתיימה.');
    return r.json();
  }

  async function deriveRaw(pw, m) {
    const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveBits']);
    return crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: hex(m.salt), iterations: m.iter }, base, 256);
  }

  async function decryptBundle(raw, m) {
    const key = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
    const r = await fetch('data.bin?t=' + m.ts, { cache: 'no-store' });
    if (!r.ok) throw new Error('data.bin: HTTP ' + r.status);
    const buf = new Uint8Array(await r.arrayBuffer());
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.slice(4, 16) }, key, buf.slice(16));
    const text = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
    return JSON.parse(text);
  }

  function overlay(html) {
    let el = document.getElementById('cloudGate');
    if (!el) { el = document.createElement('div'); el.id = 'cloudGate'; document.body.appendChild(el); }
    el.innerHTML = html;
    return el;
  }
  const hideOverlay = () => { const el = document.getElementById('cloudGate'); if (el) el.remove(); };

  function askPassword(err) {
    return new Promise(resolve => {
      const el = overlay(`<form class="gate" autocomplete="on">
        <div class="brandline"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 22 L12 15 L17 19 L27 8" stroke="#E066D2" stroke-width="3" fill="none"/><circle cx="27" cy="8" r="2.5" fill="#63CF86"/></svg><b>SwingDesk</b></div>
        <p>הדשבורד מוצפן. מקלידים את הסיסמה פעם אחת, והטלפון זוכר אותה.</p>
        <input type="text" name="username" value="swingdesk" autocomplete="username" hidden>
        <label for="cgPw">סיסמה</label>
        <input id="cgPw" type="password" autocomplete="current-password" required dir="ltr">
        <button type="submit">פתיחה</button>
        <div class="err" role="alert">${err || ''}</div></form>`);
      const f = el.querySelector('form');
      setTimeout(() => el.querySelector('#cgPw').focus(), 50);
      f.onsubmit = e => { e.preventDefault(); f.querySelector('button').textContent = 'פותח...'; resolve(el.querySelector('#cgPw').value); };
    });
  }

  async function load() {
    try { meta = await fetchMeta(); }
    catch (e) { overlay(`<div class="gate"><b>SwingDesk</b><p>${e.message}</p></div>`); throw e; }
    lastMetaCheck = Date.now();
    const saved = store.get();
    let raw = saved && saved.salt === meta.salt && saved.iter === meta.iter ? b64d(saved.k).buffer : null;
    let err = '';
    for (;;) {
      if (!raw) raw = await deriveRaw(await askPassword(err), meta);
      try {
        bundle = await decryptBundle(raw, meta);
        store.set({ k: b64e(raw), salt: meta.salt, iter: meta.iter });
        hideOverlay();
        document.body.classList.add('static');
        return bundle;
      } catch (e) {
        if (e && e.name === 'OperationError') { raw = null; store.clear(); err = 'סיסמה שגויה. נסה שוב.'; continue; }
        overlay(`<div class="gate"><b>SwingDesk</b><p>לא הצלחתי לטעון את הנתונים: ${String(e && e.message || e)}</p><button onclick="location.reload()">נסה שוב</button></div>`);
        throw e;
      }
    }
  }

  // newer data published since the page opened? swap it in quietly
  async function refresh(force) {
    if (!force && Date.now() - lastMetaCheck < 60000) return false;
    lastMetaCheck = Date.now();
    let m;
    try { m = await fetchMeta(); } catch (e) { return false; }
    if (!force && meta && m.ts === meta.ts) return false;
    const saved = store.get();
    if (!saved || saved.salt !== m.salt) { location.reload(); return false; }
    try {
      const nb = await decryptBundle(b64d(saved.k).buffer, m);
      const newScan = !bundle || !bundle.scan || !nb.scan || nb.scan.id !== bundle.scan.id;
      meta = m; bundle = nb;
      const hasS = typeof S !== 'undefined';
      if (hasS && S.app) Object.assign(S.app, bundle.state);
      if (newScan && hasS && bundle.scan) setTimeout(async () => { await loadScan(bundle.scan.id); route(S.view); }, 0);
      else if (window.renderTop) setTimeout(renderTop, 0);
      return true;
    } catch (e) { return false; }
  }

  // New York session state, computed on the phone (the data may be a few minutes old)
  function marketState() {
    const ny = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const d = ny.getDay(), hm = ny.getHours() * 60 + ny.getMinutes();
    if (d === 0 || d === 6) return 'closed';
    if (hm >= 240 && hm < 570) return 'pre';
    if (hm >= 570 && hm < 960) return 'open';
    if (hm >= 960 && hm < 1200) return 'post';
    return 'closed';
  }

  const ready = () => bundle ? Promise.resolve(bundle) : (loading = loading || load());
  const copy = o => JSON.parse(JSON.stringify(o));

  window.api = async function api(path, opts) {
    const method = ((opts && opts.method) || 'GET').toUpperCase();
    await ready();
    const [p, qs] = String(path).split('?');
    const q = new URLSearchParams(qs || '');
    if (method === 'GET') {
      if (p === '/api/state') return copy(bundle.state);
      if (p === '/api/job') return { state: 'idle', pct: 0 };
      if (p === '/api/sync') {
        refresh(false);
        const ms = marketState();
        return Object.assign(copy(bundle.sync), { alerts: (bundle.alerts || []).slice(0, 10), market_state: bundle.state.demo ? 'open' : ms });
      }
      if (p === '/api/oppquotes') return copy(bundle.opp_quotes || { quotes: {} });
      if (p === '/api/market') { if (!bundle.market) throw new Error('אין נתוני שוק'); return copy(bundle.market); }
      if (p === '/api/live') return Object.assign(copy(bundle.live || { items: [] }), { alerts: bundle.alerts || [] });
      if (p === '/api/news') return [];
      if (p === '/api/remote') return { state: 'off', cloud: true };
      if (p === '/api/chart') {
        const t = (q.get('t') || '').toUpperCase();
        const c = bundle.scan && bundle.scan.charts && bundle.scan.charts[t];
        if (!c) throw new Error('הגרף של המניה הזו לא נכלל בגרסת הטלפון');
        return copy(c);
      }
      if (p === '/api/quotes') {
        const all = Object.assign({}, (bundle.opp_quotes || {}).quotes || {});
        ((bundle.live || {}).items || []).forEach(i => { if (i.q) all[i.ticker] = i.q; });
        const out = {};
        (q.get('t') || '').split(',').filter(Boolean).forEach(t => { if (all[t]) out[t] = all[t]; });
        return out;
      }
      if (p.startsWith('/api/scans/')) {
        if (!bundle.scan) throw new Error('אין סריקה');
        return copy(bundle.scan);
      }
      throw new Error(READONLY);
    }
    if (p === '/api/logout') { store.clear(); location.replace(location.pathname); return new Promise(() => {}); }
    if (p === '/api/sync') {
      const got = await refresh(true);
      return Object.assign(copy(bundle.sync), { did: got ? ['reload'] : [], scan_started: false, market_state: marketState() });
    }
    if (p === '/api/live/refresh') { await refresh(true); return Object.assign(copy(bundle.live || { items: [] }), { alerts: bundle.alerts || [] }); }
    throw new Error(READONLY);
  };

  // settings: what runs where, read-only
  window.viewSettings = function viewSettings() {
    const a = S.app, s = a.settings, repo = a.repo || '';
    const gh = repo ? `https://github.com/${repo}` : '';
    const row = (k, v) => `<div class="kvrow"><span>${k}</span><b>${v}</b></div>`;
    $('#view').innerHTML = `<div class="stack">
      <div class="panel"><div class="panel-h">SwingDesk בענן <span class="badge cy">קריאה בלבד</span></div><div class="panel-b cloudinfo">
        <p>הכול רץ ב-GitHub, בלי המחשב בבית:</p>
        <ul><li><b>סריקה מלאה</b> בכל ערב מסחר, אחרי הסגירה בארה"ב (בערך 23:40 שעון ישראל).</li>
          <li><b>בדיקת מחירים</b> כל רבע שעה בזמן מסחר ובפרה-מרקט: טריגרים, ביטולים בפתיחה, ושבירת תמיכה בהזדמנויות.</li>
          <li><b>התראות</b> לאפליקציה ntfy בטלפון${a.ntfy_ready ? '' : ' (עוד לא הוגדר ערוץ: הסוד NTFY_TOPIC)'}.</li></ul>
        <p class="hint">GitHub מריץ משימות מתוזמנות לפי עומס, ולפעמים באיחור של כמה דקות. הנתונים בדף מתעדכנים לבד.</p>
        <div class="kv">${row('עודכן לאחרונה', esc((a.built || '').replace('T', ' ').slice(0, 16)))}${row('נר יומי אחרון', esc(S.sync && S.sync.last_bar || '—'))}
          ${row('הון', `<span class="num">$${fint(s.capital)}</span>`)}${row('סיכון לעסקה', `<span class="num">$${fint(s.risk_per_trade)}</span>`)}
          ${row('הזדמנות מוכנה', `ירידה של ${s.opp_min_drop}% לפחות, התייצבות ${s.opp_min_stab} מתוך 5, יחס ${s.opp_min_rr}R לפחות, סיכון עד ${s.opp_max_risk}%`)}</div>
        <p class="hint">שינוי ספים: ב-GitHub, בסוד <b>SETTINGS_JSON</b>${gh ? ` (<a href="${gh}/settings/secrets/actions" target="_blank" rel="noopener">כאן</a>)` : ''}. השינוי חל מהסריקה הבאה. למשל:</p>
        <code class="codeblock" dir="ltr">{"opp_min_drop": 20, "opp_min_stab": 4}</code>
        <div class="actions" style="padding:0">${gh ? `<a class="btn" href="${gh}/actions" target="_blank" rel="noopener">הריצות ב-GitHub</a>` : ''}
          <button class="btn ghost" type="button" id="cgLogout">שכח את הסיסמה בטלפון הזה</button></div>
      </div></div></div>`;
    $('#cgLogout').onclick = () => api('/api/logout', { method: 'POST', body: {} });
  };

  // start decrypting right away, before the dashboard asks
  document.addEventListener('DOMContentLoaded', () => { ready().catch(() => {}); }, { once: true });
})();

"use strict";
/* Claude Token Manager - dashboard script. No external resources; works under CSP script-src 'self'. */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const get = u => fetch(u).then(async r => {
  const d = await r.json().catch(() => ({error: "risposta non valida " + r.status}));
  if (!r.ok || (d && d.error)) throw new Error(d.error || r.status);
  return d;
});
const post = (u, b) => fetch(u, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(b || {})}).then(r => r.json());
const LOC = "it-IT";
const fmt = n => n == null ? "-" : n.toLocaleString();
const dash = v => v == null ? "-" : v;
const compact = n => n == null ? "-" : n >= 1e9 ? (n / 1e9).toFixed(2) + "B" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);
const clock = e => new Date(e * 1000).toLocaleTimeString(LOC, {hour: "2-digit", minute: "2-digit"});
const when = e => new Date(e * 1000).toLocaleString(LOC, {weekday: "short", hour: "2-digit", minute: "2-digit"});
function until(e) {
  const s = e - Date.now() / 1000;
  if (s <= 0) return "ora";
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? d + " g " + h + " h" : h ? h + " h " + m + " m" : m + " m";
}
const ICON_WARN = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5 18.5 17h-17z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M10 8v4.2M10 14.4v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
let WARN = 80;

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => { t.hidden = true; }, 3500);
}
function setVars(root) {  // CSSOM writes are allowed under style-src 'self' (inline style attributes are not)
  root.querySelectorAll("[data-v],[data-l]").forEach(el => {
    if (el.dataset.l != null) el.style.setProperty("--l", el.dataset.l + "%");
    if (el.dataset.v != null) {
      if (el.classList.contains("m-fill") || el.classList.contains("m-proj")) {
        el.style.setProperty("--v", "0%");
        requestAnimationFrame(() => requestAnimationFrame(() => el.style.setProperty("--v", el.dataset.v + "%")));
      } else el.style.setProperty("--v", el.dataset.v + "%");
    }
  });
}

/* ---------- limits: gauges + alerts ---------- */
function gauge(win, title, span, w) {
  if (!w) return {alerts: [], over: false, html: `<section class="gauge" data-win="${win}" data-state="none"><div class="g-head"><h2>${title}<span class="win">${span}</span></h2><span class="chip">Nessuna lettura</span></div>
    <p class="g-empty">Ancora nessuna lettura per questa finestra. Compare dopo la prima risposta di Claude Code, oppure manca il CSV dei limiti (vedi il README).</p></section>`};
  const p = w.projection || {}, alerts = [];
  let state = "ok", chip = "Sotto la soglia di allerta", cls = "ok";
  if (w.stale) { state = "stale"; chip = "Finestra già ripristinata"; cls = ""; }
  else if (w.pct >= 100) { state = "over"; chip = "Limite raggiunto"; cls = "bad"; alerts.push(`${title}: limite raggiunto.`); }
  else if (p.status === "exceeds") { state = "over"; chip = "Lo supererà"; cls = "bad"; alerts.push(`${title}: di questo passo supera il limite verso ${esc(p.exceeds_at_text || "il ripristino")}.`); }
  else if (w.pct >= WARN) { state = "warn"; chip = "Sopra la soglia di allerta"; cls = "warn"; alerts.push(`${title} è al ${w.pct}%, sopra la tua soglia di allerta del ${WARN}%.`); }
  else if (p.status === "ok" && p.at_reset_pct >= WARN) { state = "warn"; chip = "In salita"; cls = "warn"; alerts.push(`${title}: previsto ${p.at_reset_pct}% al ripristino, sopra la tua soglia di allerta del ${WARN}%.`); }
  let projDd, projPct = null, over = false;
  if (w.stale) projDd = "In attesa di una nuova lettura";
  else if (p.status === "exceeds") { projDd = `Oltre il limite<small>verso ${esc(p.exceeds_at_text || "")}</small>`; projPct = 100; over = true; }
  else if (p.status === "ok") { projDd = `${p.at_reset_pct}% al ripristino<small>${p.hours_left} h rimaste nella finestra</small>`; projPct = p.at_reset_pct; }
  else projDd = `Dati ancora insufficienti<small>${esc(p.reason || "")}</small>`;
  const pace = p.rate_per_hour != null ? `${p.rate_per_hour} pt/h<small>recente: ${w.rate_per_hour == null ? "-" : w.rate_per_hour + " pt/h"}</small>` : `-<small>recente: ${w.rate_per_hour == null ? "-" : w.rate_per_hour + " pt/h"}</small>`;
  const reset = w.resets_at ? `fra ${until(w.resets_at)}<small>${esc(when(w.resets_at))}</small>` : `${esc(w.resets_at_text || "sconosciuto")}`;
  const proj = projPct != null && projPct > w.pct ? `<span class="m-proj${over ? " over" : ""}" data-l="${w.pct}" data-v="${(projPct - w.pct).toFixed(1)}"></span>` : "";
  const html = `<section class="gauge" data-win="${win}" data-state="${state}" aria-labelledby="g${win}">
    <div class="g-head"><h2 id="g${win}">${title}<span class="win">${span}</span></h2><span class="chip ${cls}">${chip}</span></div>
    <div class="g-num"><span class="n">${w.pct}</span><span class="u">%</span><span class="lbl">usato</span></div>
    <div class="meter" role="meter" aria-label="Consumo ${title}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${w.pct}" aria-valuetext="${w.pct} per cento usato">
      <span class="m-fill" data-v="${w.pct}"></span>${proj}
      <span class="m-t" data-l="25"></span><span class="m-t" data-l="50"></span><span class="m-t" data-l="75"></span>
      <span class="m-warn${WARN > 82 ? " right" : ""}" data-l="${WARN}"><span>allerta ${WARN}%</span></span></div>
    <div class="m-scale" aria-hidden="true"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
    <dl class="facts"><div><dt>Ripristino</dt><dd>${reset}</dd></div><div><dt>Ritmo medio</dt><dd>${pace}</dd></div><div><dt>Previsione</dt><dd>${projDd}</dd></div></dl>
  </section>`;
  return {alerts, over: state === "over", html};
}
let CHART_ROWS = [];
async function limits() {
  try {
    const d = await get("/api/limits");
    WARN = d.warn_pct || WARN;
    const gs = [gauge("5", "Sessione di 5 ore", "mobile", d.five_hour), gauge("7", "Settimana", "7 giorni", d.seven_day)];
    $("gauges").innerHTML = gs.map(g => g.html).join("");
    setVars($("gauges"));
    const msgs = gs.flatMap(g => g.alerts), worst = gs.some(g => g.over);
    $("alerts").innerHTML = msgs.length ? `<div class="banner ${worst ? "bad" : "warn"}" role="${worst ? "alert" : "status"}">${ICON_WARN}<ul>${msgs.map(m => `<li>${esc(m)}</li>`).join("")}</ul></div>` : "";
    CHART_ROWS = d.series || [];
    $("histnote").textContent = d.readings ? `${d.readings} letture, ultima alle ${d.last.time}` : "CSV dei limiti mancante o vuoto. Aggiungi lo snippet della statusline (vedi il README).";
    drawChart();
    live(true);
  } catch (e) { live(false, e.message); }
}
function live(ok, msg) {
  $("live").dataset.state = ok ? "ok" : "err";
  $("livetxt").textContent = ok ? "Aggiornato " + new Date().toLocaleTimeString(LOC, {hour: "2-digit", minute: "2-digit"}) : "Non in linea";
  const er = $("err"); er.hidden = ok; er.textContent = ok ? "" : "Impossibile leggere il server: " + msg;
}

/* ---------- history chart ---------- */
function drawChart() {
  const host = $("chart"), rows = CHART_ROWS;
  if (!rows.length) { host.innerHTML = '<p class="empty"><b>Ancora nessuno storico</b>Le letture compaiono qui dopo che lo snippet della statusline ha registrato qualche riga.</p>'; return; }
  const W = Math.max(host.clientWidth || 640, 280), narrow = W < 560, H = narrow ? 240 : 360;
  const m = {l: 34, r: narrow ? 58 : 74, t: 14, b: 26}, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const t0 = rows[0].t, t1 = Math.max(rows[rows.length - 1].t, Date.now() / 1000), X = t => m.l + (t - t0) / Math.max(t1 - t0, 1) * iw, Y = v => m.t + (1 - v / 100) * ih;
  let g = "";
  [0, 25, 50, 75, 100].forEach(v => { g += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)" stroke-width="1"${v && v < 100 ? ' stroke-dasharray="2 4"' : ""}/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${v}</text>`; });
  const step = 6 * 3600, d0 = new Date(t0 * 1000); d0.setMinutes(0, 0, 0);
  for (let t = d0.getTime() / 1000; t <= t1; t += 3600) {
    if (t < t0) continue;
    const d = new Date(t * 1000); if (d.getHours() % 6) continue;
    const midnight = d.getHours() === 0, lab = midnight ? d.toLocaleDateString(LOC, {weekday: "short", day: "numeric"}) : String(d.getHours()).padStart(2, "0") + ":00";
    g += `<line x1="${X(t)}" x2="${X(t)}" y1="${m.t}" y2="${m.t + ih}" stroke="var(--line-soft)" stroke-width="${midnight ? 1.5 : 1}"/><text x="${X(t)}" y="${H - 6}" text-anchor="middle"${midnight ? ' font-weight="650"' : ""}>${esc(lab)}</text>`;
  }
  g += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(WARN)}" y2="${Y(WARN)}" stroke="var(--warn)" stroke-width="1.2" stroke-dasharray="5 4"/><text x="${W - m.r + 6}" y="${Y(WARN) + 4}" fill="var(--warn)">allerta ${WARN}</text>`;
  const seg = (k) => { const out = []; let cur = []; rows.forEach(r => { if (r[k] == null) { if (cur.length) out.push(cur); cur = []; } else cur.push(r); }); if (cur.length) out.push(cur); return out; };
  const series = [["p5", "--w5", "5h"], ["p7", "--w7", "7g"]];
  const ends = [];
  series.forEach(([k, c, name]) => {
    seg(k).forEach(s => {
      const pts = s.map(r => `${X(r.t).toFixed(1)},${Y(r[k]).toFixed(1)}`);
      if (k === "p5") g += `<path d="M${pts[0]} L${pts.join(" L")} L${X(s[s.length - 1].t).toFixed(1)},${Y(0)} L${X(s[0].t).toFixed(1)},${Y(0)}Z" fill="var(${c})" opacity=".1"/>`;
      g += `<path d="M${pts.join(" L")}" fill="none" stroke="var(${c})" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;
    });
    const last = [...rows].reverse().find(r => r[k] != null);
    if (last) ends.push({y: Y(last[k]), c, txt: `${name} ${last[k]}%`, x: X(last.t)});
  });
  if (ends.length === 2 && Math.abs(ends[0].y - ends[1].y) < 15) { const mid = (ends[0].y + ends[1].y) / 2, up = ends[0].y <= ends[1].y; ends[0].y = mid + (up ? -8 : 8); ends[1].y = mid + (up ? 8 : -8); }
  ends.forEach(e => { g += `<circle cx="${e.x.toFixed(1)}" cy="${e.y.toFixed(1)}" r="3.5" fill="var(${e.c})"/><text class="lbl" x="${W - m.r + 6}" y="${e.y + 4}" fill="var(${e.c})">${e.txt}</text>`; });
  g += `<line id="xh" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" stroke="var(--mut)" stroke-width="1" visibility="hidden"/><circle id="d5" r="4.5" fill="var(--w5)" stroke="var(--panel)" stroke-width="2" visibility="hidden"/><circle id="d7" r="4.5" fill="var(--w7)" stroke="var(--panel)" stroke-width="2" visibility="hidden"/>`;
  g += `<rect id="hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" tabindex="0" role="img" aria-label="Storico del consumo, ultime 48 ore. Usa le frecce sinistra e destra per leggere i valori."/>`;
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="Grafico dello storico del consumo">${g}</svg>`;
  const hit = $("hit"), svg = host.querySelector("svg"), pts = rows.filter(r => r.p5 != null || r.p7 != null);
  const show = i => {
    const r = pts[Math.max(0, Math.min(pts.length - 1, i))], x = X(r.t);
    $("xh").setAttribute("x1", x); $("xh").setAttribute("x2", x); $("xh").setAttribute("visibility", "visible");
    [["d5", "p5"], ["d7", "p7"]].forEach(([id, k]) => { const c = $(id); if (r[k] == null) c.setAttribute("visibility", "hidden"); else { c.setAttribute("cx", x); c.setAttribute("cy", Y(r[k])); c.setAttribute("visibility", "visible"); } });
    const tip = $("tip"); tip.hidden = false;
    tip.innerHTML = `${esc(new Date(r.t * 1000).toLocaleString(LOC, {weekday: "short", hour: "2-digit", minute: "2-digit"}))}<br><span class="k k5"></span>5h <b>${dash(r.p5)}%</b> &nbsp; <span class="k k7"></span>7g <b>${dash(r.p7)}%</b>`;
    const wrap = $("chartwrap").getBoundingClientRect(), sv = svg.getBoundingClientRect(), left = (sv.left - wrap.left) + x * (sv.width / W);
    tip.style.left = Math.min(Math.max(left + 12, 0), wrap.width - tip.offsetWidth) + "px"; tip.style.top = "30px";
    return i;
  };
  let cur = pts.length - 1;
  const idxAt = ev => { const b = svg.getBoundingClientRect(), t = t0 + ((ev.clientX - b.left) * (W / b.width) - m.l) / iw * (t1 - t0); let bi = 0, bd = 1e18; pts.forEach((r, i) => { const dd = Math.abs(r.t - t); if (dd < bd) { bd = dd; bi = i; } }); return bi; };
  hit.addEventListener("pointermove", ev => { cur = show(idxAt(ev)); });
  hit.addEventListener("pointerleave", () => { $("tip").hidden = true; ["xh", "d5", "d7"].forEach(id => $(id).setAttribute("visibility", "hidden")); });
  hit.addEventListener("keydown", ev => { if (ev.key === "ArrowLeft") cur = show(cur - 1); else if (ev.key === "ArrowRight") cur = show(cur + 1); else return; ev.preventDefault(); });
}
let rz; new ResizeObserver(() => { clearTimeout(rz); rz = setTimeout(drawChart, 80); }).observe($("chartwrap"));

/* ---------- jobs ---------- */
const STATUS = {queued: ["In coda", "", ""], approved: ["Approvato", "info", ""], running: ["In corso", "ok", "run"], blocked: ["Bloccato", "warn", "sq"], done: ["Fatto", "ok", "ring"], failed: ["Fallito", "bad", "sq"], cancelled: ["Annullato", "", "cancel"]};
const PROP = {exclude: "escludere", merge: "unire", integrate: "integrare", sequence: "in sequenza"};
const TAG = {clean: "pulita", mixed: "mista", sessions: "sessioni", interval: "intervallo", override: "forzato", keyword: "parola chiave", default: "predefinito"};
const tag = v => TAG[v] || v;
const chipFor = s => { const [t, c, x] = STATUS[s] || [s, "", ""]; return `<span class="chip ${c} ${x}">${esc(t)}</span>`; };
const clean = s => String(s || "").replace(/[*`]/g, "").replace(/\s+/g, " ").trim();
function jrow(j, own) {
  const where = (j.targets || []).join(", ") || j.project || "";
  const km = [j.kind, j.model].filter(Boolean).join(" / ");
  const orig = !own && j.status_text != null ? `<div class="orig"><i>Letto come ${tag(esc(j.status_how))}:</i> ${esc(clean(j.status_text).slice(0, 150))}</div>` : "";
  const est = `<span class="v">5h <b>${esc(dash(j.estimate_pct_5h))}</b> &middot; 7g <b>${esc(dash(j.estimate_pct_7d))}</b></span>` +
    (j.est_tokens ? `<span class="v tk">${esc(compact(j.est_tokens))} tok</span>` : "");
  let act = "";
  if (j.finished_at) act = `<span class="v">5h <b>${esc(dash(j.actual_pct_5h))}</b> &middot; 7g <b>${esc(dash(j.actual_pct_7d))}</b> <span class="tag">${tag(esc(j.measure))}</span></span>`;
  if (j.actual_tokens) act += `<span class="v tk" title="${esc((j.actual_tokens_raw || []).join(" / "))} (input / output / cache scritta / cache letta)">${esc(compact(j.actual_tokens))} tok <span class="tag">${tag(esc(j.token_measure))}</span></span>`;
  else if (j.started_at) act = `<span class="mut">in corso</span>`;
  else act = `<span class="mut">-</span>`;
  let ctl = "";
  if (own) {
    const opts = Object.keys(STATUS).map(s => `<option value="${s}"${s === j.status ? " selected" : ""}>${STATUS[s][0]}</option>`).join("");
    const btn = j.finished_at ? "" : j.started_at
      ? `<button class="btn sm" type="button" data-act="finish" data-mixed="0" data-id="${esc(j.id)}">Chiudi, pulito</button><button class="btn sm" type="button" data-act="finish" data-mixed="1" data-id="${esc(j.id)}">Chiudi, misto</button>`
      : `<button class="btn sm" type="button" data-act="start" data-id="${esc(j.id)}">Avvia</button>`;
    ctl = `<div class="acts">${btn}</div><select class="sm" data-act="status" data-id="${esc(j.id)}" aria-label="Cambia stato di ${esc(j.id)}">${opts}</select>`;
  }
  return `<tr><td class="id" data-label="ID">${esc(j.id)}</td>
    <td class="title" data-label="Lavoro"><div class="t">${esc(j.title)}</div><div class="sub">${esc(where)}${km ? " &middot; " + esc(km) : ""}${j.after ? " &middot; dopo " + esc(j.after) : ""}${j.attempts ? " &middot; tentativi " + esc(j.attempts) : ""}</div>${j.result ? `<div class="orig">${esc(j.result)}</div>` : ""}${orig}</td>
    <td data-label="Stato">${chipFor(j.status)}</td>
    <td class="num" data-label="Stima">${est}</td><td class="num" data-label="Reale">${act}</td>
    ${own ? `<td class="act" data-label="">${ctl}</td>` : ""}</tr>`;
}
const JHEAD_RO = "<thead><tr><th>ID</th><th>Lavoro</th><th>Stato</th><th class='num'>Stima, % del limite</th><th class='num'>Reale, % del limite</th></tr></thead>";
const JHEAD = "<thead><tr><th>ID</th><th>Lavoro</th><th>Stato</th><th class='num'>Stima, % del limite</th><th class='num'>Reale, % del limite</th><th><span class='sr'>Azioni</span></th></tr></thead>";
async function jobs() {
  const d = await get("/api/jobs"), all = d.jobs.concat(d.markdown || []), cnt = {};
  all.forEach(j => { cnt[j.status] = (cnt[j.status] || 0) + 1; });
  $("qcount").innerHTML = Object.keys(STATUS).filter(s => cnt[s]).map(s => `${chipFor(s).replace("</span>", " " + cnt[s] + "</span>")}`).join("");
  let h = `<h3 class="group-title">La tua coda<small>${d.jobs.length} lavori</small></h3>`;
  h += d.jobs.length ? `<table class="tbl queue">${JHEAD}<tbody>${d.jobs.map(j => jrow(j, true)).join("")}</tbody></table>` : `<p class="empty"><b>Ancora nessun lavoro</b>Aggiungine uno qui sotto. Le stime diventano più affidabili man mano che i lavori si chiudono e si misurano.</p>`;
  if ((d.markdown_skipped || []).length) h += `<div class="banner warn">${ICON_WARN}<div>Righe saltate nella coda markdown (il numero di celle non corrisponde all'intestazione):<ul>${d.markdown_skipped.map(r => `<li class="ink">${esc(r)}</li>`).join("")}</ul></div></div>`;
  h += `<h3 class="group-title">Importati dal markdown<small>sola lettura</small></h3>`;
  h += d.markdown_path_set ? `<table class="tbl queue ro">${JHEAD_RO}<tbody>${d.markdown.map(j => jrow(j, false)).join("")}</tbody></table>` : `<p class="empty"><b>Nessuna coda markdown configurata</b>Imposta <code>markdown_queue</code> in config.json per importare una tabella.</p>`;
  $("jobs").innerHTML = h;
}

/* ---------- conflicts, report, orders, calibration ---------- */
async function conf() {
  const d = await get("/api/conflicts");
  $("conf").innerHTML = d.conflicts.length
    ? d.conflicts.map(c => `<div class="conf-item"><b>${esc(c.a)} + ${esc(c.b)}</b> su ${esc(c.shared.join(", "))}<div>${esc(PROP[c.proposal] || c.proposal)}: ${esc(c.reason)}</div></div>`).join("")
    : `<p class="empty"><b>Nessuna sovrapposizione</b>I lavori in coda e approvati lavorano su obiettivi diversi.</p>`;
}
async function rep() { const d = await get("/api/report"); $("repout").textContent = d.markdown; }
async function orders() {
  const d = await get("/api/orders"), tone = {new: "info", taken: "ok run", done: "ok ring", rejected: " cancel"};
  $("orderlist").innerHTML = d.orders.length
    ? `<table class="tbl orders"><thead><tr><th>ID</th><th>Ordine</th><th>Stato</th><th class="num">Priorità</th><th>Obiettivi</th><th>Lavoro</th></tr></thead><tbody>` + d.orders.map(o => `<tr><td class="id" data-label="ID">${esc(o.id)}</td><td class="title" data-label="Ordine">${esc((o.text || "").slice(0, 160))}</td>
      <td data-label="Stato"><span class="chip ${tone[o.status] || ""}">${esc(o.status)}${o.unknown_status ? " (?)" : ""}</span></td><td class="num" data-label="Priorità">${esc(o.priority)}</td>
      <td data-label="Obiettivi">${esc((o.targets || []).join(", ") || "-")}</td><td data-label="Lavoro">${esc(o.job || "-")}</td></tr>`).join("") + "</tbody></table>"
    : `<p class="empty"><b>L'uscita è vuota</b>Gli ordini che mandi compaiono qui con il loro stato.</p>`;
}
async function cal() {
  const d = await get("/api/calibration"), k = Object.keys(d).sort();
  $("cal").innerHTML = k.length
    ? `<table class="tbl"><thead><tr><th>Finestra, livello, tipo, modello</th><th class="num">Rapporto</th><th class="num">Campioni</th></tr></thead><tbody>${k.map(x => `<tr><td data-label="Gruppo">${esc(x)}</td><td class="num" data-label="Rapporto">${esc(d[x].ratio)}</td><td class="num" data-label="Campioni">${esc(d[x].n)}</td></tr>`).join("")}</tbody></table>`
    : `<p class="empty"><b>Storico insufficiente</b>Avvia e chiudi qualche lavoro per costruirlo.</p>`;
}

/* ---------- tokens ---------- */
function tokTable(rows, label, today) {
  const max = Math.max(1, ...rows.map(r => r.total));
  return `<table class="tbl tokens"><thead><tr><th>${label}</th><th>Totale</th><th class="num">Output</th><th class="num" title="Cache scritta">Scritti</th><th class="num" title="Cache letta">Letti</th><th class="num">Input</th></tr></thead><tbody>` +
    rows.map(r => `<tr><td data-label="${label}" class="${r.name === today ? "today" : ""}" title="${esc(r.full || r.name)}">${esc(r.shown || r.name)}</td>
    <td data-label="Totale"><div class="bar"><i data-v="${(r.total / max * 100).toFixed(1)}"></i><b title="${fmt(r.total)}">${compact(r.total)}</b></div></td>
    <td class="num" data-label="Output" title="${fmt(r.output)}">${compact(r.output)}</td><td class="num" data-label="Cache scritta" title="${fmt(r.cache_creation)}">${compact(r.cache_creation)}</td>
    <td class="num" data-label="Cache letta" title="${fmt(r.cache_read)}">${compact(r.cache_read)}</td><td class="num" data-label="Input" title="${fmt(r.input)}">${compact(r.input)}</td></tr>`).join("") + "</tbody></table>";
}
var short = n => { const t = n.replace(/^-Users-[^-]+-/, "").replace(/^Desktop-/, "") || n; return t.length > 24 ? "\u2026" + t.slice(-23) : t; };
async function tokens() {
  const d = await get("/api/tokens"), now = new Date(), iso = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" + String(now.getDate()).padStart(2, "0");
  const days = d.days.slice().reverse().map(r => { const p = r.name.split("-").map(Number); return Object.assign({}, r, {full: r.name, shown: p.length === 3 ? new Date(p[0], p[1] - 1, p[2]).toLocaleDateString(LOC, {weekday: "short", day: "numeric", month: "short"}) : r.name}); });
  const projs = d.projects.slice(0, 12).map(r => Object.assign({}, r, {full: r.name, shown: short(r.name)}));
  $("tokdays").innerHTML = days.length ? tokTable(days, "Giorno", iso) : `<p class="empty"><b>Nessuna trascrizione nel periodo</b>Controlla transcripts_dir e token_days in config.json.</p>`;
  $("tokproj").innerHTML = projs.length ? tokTable(projs, "Progetto", null) : `<p class="empty"><b>Nessun progetto trovato</b></p>`;
  setVars($("usage"));
}

/* ---------- actions ---------- */
const formObj = f => { const o = {}; new FormData(f).forEach((v, k) => { if (v !== "") o[k] = v; }); return o; };
document.addEventListener("click", async ev => {
  const b = ev.target.closest("button[data-act]"); if (!b) return;
  const id = b.dataset.id;
  try {
    if (b.dataset.act === "start") await post(`/api/jobs/${id}/start`);
    else if (b.dataset.act === "finish") await post(`/api/jobs/${id}/finish`, {mixed: b.dataset.mixed === "1"});
    refresh();
  } catch (e) { toast(e.message); }
});
document.addEventListener("change", async ev => {
  const s = ev.target.closest("select[data-act=status]"); if (!s) return;
  const r = await post(`/api/jobs/${s.dataset.id}/status`, {status: s.value});
  if (r.error) toast(r.error); refresh();
});
$("rep").onclick = rep;
$("f").onsubmit = async e => {
  e.preventDefault();
  const r = await post("/api/jobs", formObj(e.target));
  if (r.error) { toast(r.error); return; }
  e.target.reset(); toast("Lavoro " + r.id + " aggiunto"); refresh();
};
$("sug").onclick = async () => {
  const f = new FormData($("f")), q = new URLSearchParams({kind: f.get("kind"), model: f.get("model"), est_5h: f.get("estimate_pct_5h"), est_7d: f.get("estimate_pct_7d")});
  try {
    const d = await get("/api/suggest?" + q);
    $("sugout").textContent = ["5h", "7d"].map(w => `${w}: grezza ${dash(d[w].raw)}, corretta ${dash(d[w].corrected)} (rapporto ${d[w].ratio}, ${d[w].samples} campioni, base ${d[w].basis})`).join("   ");
  } catch (e) { $("sugout").textContent = e.message; }
};
$("of").onsubmit = async e => {
  e.preventDefault();
  const o = formObj(e.target), chk = await post("/api/orders", Object.assign({dry_run: true}, o));
  if (chk.error) { toast(chk.error); return; }
  if (chk.conflicts.length && !confirm("Conflitti con la coda:\n" + chk.conflicts.map(c => `${c.a} + ${c.b} su ${c.shared.join(", ")}: ${PROP[c.proposal] || c.proposal} - ${c.reason}`).join("\n") + "\n\nMandare comunque l'ordine?")) return;
  const r = await post("/api/orders", o);
  if (r.error) toast(r.error); else { e.target.reset(); toast("Ordine mandato in uscita"); }
  orders();
};
window.addEventListener("unhandledrejection", e => { const er = $("err"); er.hidden = false; er.textContent = "Errore: " + ((e.reason && e.reason.message) || e.reason); });
/* ---------- tokens per 1%, sessions, runner ---------- */
async function perpct() {
  try {
    const d = await get("/api/usage"), el = $("perpct");
    const meas = d.status === "ok" ? `misurato ${compact(d.tokens_per_pct)} (${compact(d.weighted_tokens)} token pesati / ${d.pct}%)` : `misurato: ${esc(d.reason || "-")}`;
    el.innerHTML = `<b>1% della settimana &asymp; ${esc(compact(d.used_tokens_per_pct))} token pesati</b><small>${d.declared_tokens_per_pct ? "dichiarato " + esc(compact(d.declared_tokens_per_pct)) + " &middot; " : ""}${meas}</small>`;
  } catch (e) { $("perpct").textContent = e.message; }
}
async function sessions() {
  const d = await get("/api/sessions");
  $("sess").innerHTML = d.sessions.length
    ? `<table class="tbl"><thead><tr><th>Sessione</th><th>Progetto</th><th>Ultima</th><th class="num">Output</th><th class="num">Cache letta</th></tr></thead><tbody>${d.sessions.map(s => `<tr><td data-label="Sessione" class="id">${esc(s.session.slice(0, 8))}</td><td data-label="Progetto">${esc(short(s.project))}</td><td data-label="Ultima">${esc(clock(s.last))}</td><td class="num" data-label="Output">${compact(s.output)}</td><td class="num" data-label="Cache letta">${compact(s.cache_read)}</td></tr>`).join("")}</tbody></table>`
    : `<p class="empty"><b>Nessuna sessione attiva</b>Qui compaiono le sessioni con attività negli ultimi 30 minuti.</p>`;
}
async function runlog() {
  const d = await get("/api/runner");
  $("runlog").textContent = d.lines.length ? d.lines.join("\n") : "L'esecutore non è ancora partito (python -m ctm run).";
  $("runfile").textContent = d.file || "";
}
function refresh() { limits(); jobs(); cal(); conf(); orders(); perpct(); sessions(); runlog(); }
refresh(); tokens(); rep();
setInterval(limits, 60000); setInterval(orders, 30000); setInterval(sessions, 60000); setInterval(runlog, 60000);

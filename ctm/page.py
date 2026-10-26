"""The HTML shell. Styles and script live in ctm/static/ and are served by the local server."""

PAGE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>Claude Token Manager</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/app.css">
<script src="/static/app.js" defer></script>
</head><body>
<a class="skip" href="#main">Vai al contenuto</a>
<header class="top"><div class="wrap top-in">
  <h1 class="brand"><svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M5 23a11 11 0 0 1 22 0" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/><path d="M16 23l5.5-8" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/></svg>Claude Token Manager</h1>
  <nav aria-label="Sezioni"><a href="#limits">Limiti</a><a href="#queue">Coda</a><a href="#orders">Ordini</a><a href="#activity">Attività</a><a href="#usage">Token</a></nav>
  <div class="live" id="live" role="status"><span class="dot" aria-hidden="true"></span><span id="livetxt">Caricamento</span></div>
</div></header>
<main id="main" class="wrap">
<div id="err" class="banner bad" role="alert" hidden></div>
<div id="alerts" aria-live="polite"></div>

<section id="limits" class="band" aria-label="Limiti del piano"><div id="gauges" class="gauges"></div><p id="perpct" class="perpct mut"></p></section>

<div class="split">
  <section class="panel" aria-labelledby="h-hist"><div class="p-head"><h2 id="h-hist">Ultime 48 ore</h2><p class="mut" id="histnote"></p></div>
    <div class="chart-wrap" id="chartwrap"><div id="chart"></div><div id="tip" class="tip" hidden></div></div></section>
  <div class="stack">
    <section class="panel" aria-labelledby="h-conf"><div class="p-head"><h2 id="h-conf">Conflitti</h2></div><div id="conf"></div></section>
    <section class="panel" aria-labelledby="h-rep"><div class="p-head"><h2 id="h-rep">Resoconto prima del ripristino</h2><button class="btn ghost" id="rep" type="button">Aggiorna</button></div>
      <details id="repbox"><summary>Mostra resoconto</summary><pre id="repout"></pre></details></section>
  </div>
</div>

<section id="queue" class="panel" aria-labelledby="h-queue"><div class="p-head"><h2 id="h-queue">Coda dei lavori</h2><div id="qcount" class="counts"></div></div>
  <div id="jobs"></div>
  <details class="form-box" id="addbox"><summary>Aggiungi un lavoro</summary>
  <form id="f" class="grid-form" autocomplete="off">
    <label class="w2">Titolo<input name="title" required></label>
    <label>Progetto<input name="project"></label>
    <label>Tipo<input name="kind" list="kinds" placeholder="ricerca, sviluppo, audit"></label>
    <label>Modello<input name="model"></label>
    <label>Obiettivi<input name="targets" placeholder="separati da virgola"></label>
    <label>Priorità<input name="priority" type="number" min="1" placeholder="1 parte per primo"></label>
    <label>Stima, finestra 5 h (%)<input name="estimate_pct_5h" type="number" step="any" min="0"></label>
    <label>Stima, finestra 7 giorni (%)<input name="estimate_pct_7d" type="number" step="any" min="0"></label>
    <label>Stima (token)<input name="est_tokens" type="number" min="0"></label>
    <label class="w2">Note<input name="notes"></label>
    <datalist id="kinds"><option value="ricerca"><option value="sviluppo"><option value="audit"></datalist>
    <div class="form-actions w3"><button class="btn primary" type="submit">Aggiungi lavoro</button><button class="btn" type="button" id="sug">Suggerisci stima corretta</button><span id="sugout" class="mut" role="status"></span></div>
  </form></details>
</section>

<div class="split">
  <section id="orders" class="panel" aria-labelledby="h-orders"><div class="p-head"><h2 id="h-orders">Ordini all'orchestratore</h2></div>
    <p class="mut lede">Ogni ordine si salva come file markdown in <code>outbox_dir</code>. L'app non esegue mai niente: chi esegue l'ordine porta il suo <code>status</code> a taken (preso), done (fatto) o rejected (rifiutato).</p>
    <form id="of" class="grid-form" autocomplete="off">
      <label class="w3">Ordine<textarea name="text" rows="3" required></textarea></label>
      <label>Obiettivi<input name="targets" placeholder="separati da virgola"></label>
      <label>Priorità<input name="priority" type="number" min="1"></label>
      <label>Lavoro collegato<input name="job" placeholder="facoltativo, es. J3"></label>
      <div class="form-actions w3"><button class="btn primary" type="submit">Manda in uscita</button><span id="ordout" class="mut" role="status"></span></div>
    </form>
    <div id="orderlist"></div></section>
  <section class="panel" aria-labelledby="h-cal"><div class="p-head"><h2 id="h-cal">Precisione delle stime</h2></div>
    <p class="mut lede">Reale diviso stima, dai lavori finiti misurati senza sovrapposizioni. Le percentuali sono numeri interi, quindi i lavori piccoli hanno un margine di più o meno un punto.</p><div id="cal"></div></section>
</div>

<section id="activity" class="split even" aria-label="Attività">
  <div class="panel"><div class="p-head"><h2>Sessioni attive</h2><p class="mut">ultimi 30 minuti, dalle trascrizioni</p></div><div id="sess"></div></div>
  <div class="panel"><div class="p-head"><h2>Esecutore</h2><p class="mut" id="runfile"></p></div>
    <p class="mut lede">Facoltativo: <code>python -m ctm run</code> esegue i lavori approvati uno alla volta. La pagina da sola non esegue mai niente.</p><pre id="runlog" class="log"></pre></div>
</section>

<section id="usage" class="split even" aria-label="Consumo di token">
  <div class="panel"><div class="p-head"><h2>Token per giorno</h2><p class="mut">Scritti e Letti sono token della cache del prompt</p></div><div id="tokdays"></div></div>
  <div class="panel"><div class="p-head"><h2>Token per progetto</h2></div><div id="tokproj"></div></div>
</section>
</main>
<div id="toast" class="toast" role="status" hidden></div>
</body></html>
"""

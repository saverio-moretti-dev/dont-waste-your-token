# UI-DESIGN - dashboard di Claude Token Manager

## Direzione
Strumento da cruscotto per chi lavora ogni giorno con Claude Code: conta la lettura immediata dei numeri e lo stato a
colpo d'occhio, non la decorazione. Metafora scelta: **strumentazione**, non «SaaS card». L'oggetto memorabile e' la
*fascia di stato*: un unico pannello diviso in due strumenti (5h, 7d), ciascuno con numero grande, barra di livello
con tacche al 25/50/75, tacca ambra alla soglia di avviso e **settore tratteggiato** per la proiezione al ripristino.
Tutto il resto resta quieto: fogli bianchi con filetti, nessuna ombra, nessun gradiente, un solo movimento
(il riempimento delle barre al caricamento).
Scartati (default da evitare): crema+terracotta, nero+verde acido, card tutte uguali, etichette in maiuscolo,
font mono per le etichette, frecce sui link, animazioni a comparsa su ogni sezione.

## Palette (variabili CSS, `prefers-color-scheme`)
| ruolo | chiaro | scuro |
|---|---|---|
| carta / pannello | #eef1f3 / #ffffff | #0c1217 / #131b22 |
| inchiostro / attenuato | #13202a / #53626e | #e6ecf0 / #98a8b4 |
| finestra 5h (verde acqua) | #0a7a74 | #3cc4bc |
| finestra 7d (indaco) | #4553b5 | #93a4ff |
| ok / avviso / errore | #23724a / #a85600 / #bd2d27 | #5fcf93 / #f2a23c / #ff7d73 |
Le due finestre hanno un colore fisso ovunque (numero, barra, grafico). Stato = colore + testo + forma (mai solo colore).
Contrasto testo >= 4.5:1 in entrambi i temi (inchiostro, attenuato, ambra, rosso verificati sul pannello).

## Tipografia
Una sola famiglia, stack di sistema (`system-ui`, SF su Mac): nessun font scaricato, nessuna licenza da accompagnare.
Personalita' dal contrasto di peso: numeri grandi 84 px peso 300 con cifre tabulari, titoli 16 px peso 650, corpo 15 px,
note 13 px. Frasi in minuscolo con iniziale maiuscola. Righe < 80 caratteri.

## Griglia
Contenitore 1320 px, margine 24 px (16 su mobile). Fascia di stato a 2 colonne; sotto: grafico (1.9fr) + conflitti e
resoconto (1fr); coda a tutta larghezza; ordini + precisione stime; token per giorno e per progetto affiancati.
Breakpoint 1100 (una colonna), 900 (strumenti impilati), 720 (tabelle diventano schede con etichette).
Allineamento a sinistra, numeri a destra con cifre tabulari.

## Componenti
- **Strumento**: titolo, chip di stato, numero, barra, scala, tre fatti (ripristino, ritmo, proiezione).
- **Avviso**: striscia ambra/rossa sopra la fascia se una finestra supera la soglia (`warn_pct`, default 80, in config),
  se la proiezione supera il limite o se e' > soglia al ripristino. `role=status`/`alert`.
- **Grafico SVG** disegnato alla larghezza reale (nessuna deformazione), etichette dirette a fine linea invece della
  legenda, linea di soglia, tooltip al passaggio e con frecce da tastiera.
- **Chip di stato**: pallino pieno/anello/quadrato/barrato + testo (in coda, approvato, in corso, bloccato, fatto, annullato).
- **Tabelle**: stima e reale affiancati in % di 5h/7d, tag clean/mixed; importate dal markdown in sola lettura.
- **Form** con etichette visibili, campi a 3 colonne; esiti in toast `role=status`, niente `alert()`.

## Accessibilita'
Link «salta al contenuto», focus visibile (2 px), `role=meter` sulle barre, `aria-label` sui select di stato,
`prefers-reduced-motion` rispettato, target >= 28 px, nessun orizzontale a 390 px.

## Vincoli tecnici
Nessuna CDN ne' rete. CSS/JS in `ctm/static/`, serviti da una **whitelist fissa** di tre percorsi (nessun percorso
costruito dalla richiesta). CSP piu' stretta di prima: `script-src 'self'; style-src 'self'; img-src 'self'`, quindi
niente script, handler o `style=` inline: eventi per delega (`data-act`), larghezze scritte via CSSOM.

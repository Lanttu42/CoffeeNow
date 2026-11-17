// COFFEE!! frontend – ei frameworkkeja, ei ulkoisia kirjastoja.
// Turvallisuus: kaikki käyttäjän syöte (keittimen nimi) laitetaan DOMiin textContentina, ei koskaan innerHTML:nä.
"use strict";

// Järjestys = tilapainikkeiden järjestys
const STATUS = {
  brewing:      "Kahvia tulossa",
  ready:        "Kahvia on",
  low:          "Kahvi vähissä",
  empty:        "Kahvi loppu",
  supplies_low: "Tarvikkeet vähissä",
  supplies_out: "Tarvikkeet loppu",
  unknown:      "Ei tietoa vielä",
};

// ---------- paikallinen tila (vain tällä laitteella) ----------

const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* yksityinen tila tms. */ }
  },
};

function deviceId() {
  let id = store.get("deviceId", null);
  if (!id) { id = crypto.randomUUID(); store.set("deviceId", id); }
  return id;
}
const myMachines = () => store.get("machines", []);   // tällä laitteella lisätyt
const isLocal = (id) => myMachines().some((m) => m.id === id);
// Omat = tällä laitteella lisätyt + kaikkien omien organisaatioiden keittimet
const isMine = (id) => isLocal(id) || ME.machines.some((m) => m.id === id);
function addMine(m) {
  if (!isLocal(m.id)) store.set("machines", [...myMachines(), { id: m.id, name: m.name }]);
}
function removeMine(id) {
  store.set("machines", myMachines().filter((m) => m.id !== id));
}

// ---------- apurit ----------

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-Lang": LANG, "X-Device-Id": deviceId() },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let msg = t("Virhe {n}", { n: res.status });
    try { msg = (await res.json()).detail || msg; } catch {}
    if (typeof msg !== "string") msg = t("Virheellinen syöte");
    else if (msg === "login_required") msg = t("Kirjaudu ensin sisään");
    else if (msg === "not_member") msg = t("Ei pääsyä");
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

// ---------- kirjautunut käyttäjä ----------

let ME = { user: null, orgs: [], machines: [] };

async function loadMe() {
  try { ME = await api("/api/auth/me"); } catch { ME = { user: null, orgs: [], machines: [] }; }
  renderChrome();
}

const managedOrgs = () => ME.orgs.filter((o) => o.is_manager);

function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3000);
}

function ago(ts) {
  if (!ts) return "";
  const min = Math.round((Date.now() / 1000 - ts) / 60);
  if (min < 1) return t("juuri nyt");
  if (min < 60) return t("{n} min sitten", { n: min });
  const hrs = Math.round(min / 60);
  return hrs < 24 ? t("{n} h sitten", { n: hrs }) : t("yli vuorokausi sitten");
}

function statusText(m) {
  let s = t(STATUS[m.status] || STATUS.unknown);
  if (m.status === "brewing" || m.status === "ready") {
    const parts = [];
    if (m.cups) parts.push(t("{n} kuppia", { n: m.cups }));
    if (m.brew_type) parts.push(t(m.brew_type));
    if (parts.length) s += ` (${parts.join(", ")})`;
    if (m.estimated && parts.length) s += t(" (ehkä)");
  }
  return s;
}

function brewEta(m) {
  if (m.status !== "brewing" || !m.brew_ready_at) return "";
  const min = Math.ceil((m.brew_ready_at - Date.now() / 1000) / 60);
  return min <= 0 ? t("valmistuu ihan just") : t("valmis noin {n} min päästä", { n: min });
}

// ---------- sekuntiajastin (prosenttilaskurit); route() tyhjentää ----------

const tickers = new Set();
let tickTimer = null;

function addTicker(fn) {
  tickers.add(fn);
  if (!tickTimer) tickTimer = setInterval(() => { if (!document.hidden) for (const f of tickers) f(); }, 1000);
  return fn;
}

function stopTickers() {
  tickers.clear();
  clearInterval(tickTimer);
  tickTimer = null;
}

// Pannun edistyminen: palkki + prosentit + arvio. onDone kutsutaan, kun 100 % saavutetaan (palvelin vaihtaa tilan).
function brewProgress(m, onDone) {
  const fill = h("span", { class: "brew-fill" });
  const bar = h("div", { class: "brew-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
    "aria-label": t("Pannun edistyminen") }, fill);
  const pct = h("span", { class: "brew-pct" });
  const eta = h("span", { class: "brew-eta" });
  const el = h("div", { class: "brew-progress" }, bar, h("div", { class: "brew-info" }, pct, eta));
  let doneAt = 0;
  const update = () => {
    const now = Date.now() / 1000;
    const start = m.updated_at || now, end = m.brew_ready_at || now;
    const p = end > start ? Math.min(1, Math.max(0, (now - start) / (end - start))) : 1;
    const n = Math.floor(p * 100);
    fill.style.width = `${n}%`;
    bar.setAttribute("aria-valuenow", String(n));
    pct.textContent = `${n} %`;
    eta.textContent = brewEta(m);
    // Palvelin tarkistaa valmistumisen ~15 s välein: kysytään uutta tilaa korkeintaan 10 s välein
    if (p >= 1 && onDone && now - doneAt > 10) { doneAt = now; setTimeout(onDone, 3000); }
  };
  update();
  return { el, tick: addTicker(update) };
}

// Nousevat kuplat "tulossa"-kortin taustalla
function bubbles() {
  return h("div", { class: "bubbles", "aria-hidden": "true" },
    [1, 2, 3, 4, 5, 6, 7, 8].map((i) => h("span", { class: `b b${i}` })));
}

// Kortin tilaluokat: tulossa = animoitu reuna, on = kultainen reuna (tuore = hehkuu)
function markCard(card, m) {
  const mins = minutesSince(m.updated_at);
  card.classList.toggle("brewing", m.status === "brewing");
  card.classList.toggle("ready", m.status === "ready");
  card.classList.toggle("fresh", m.status === "ready" && mins !== null && mins < FRESH_MINUTES);
}

function go(path) {
  history.pushState(null, "", path);
  route();
}

const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

// ---------- push-ilmoitukset ----------

function b64urlToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function pushSubscription(create) {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub && create) {
    const cfg = await api("/api/config");
    if (!cfg.push_enabled) throw new Error(t("Palvelimella ei ole push-ilmoituksia käytössä"));
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: b64urlToBytes(cfg.vapid_public_key),
    });
  }
  return sub;
}

const followed = () => store.get("followed", []);

async function follow(machineId) {
  const perm = await Notification.requestPermission();   // pitää tulla napin painalluksesta (iOS)
  if (perm !== "granted") throw new Error(t("Ilmoituslupaa ei annettu"));
  const sub = await pushSubscription(true);
  await api(`/api/machines/${machineId}/follow`, {
    method: "POST",
    body: { device_id: deviceId(), subscription: sub.toJSON() },
  });
  store.set("followed", [...new Set([...followed(), machineId])]);
  renderBell();
}

async function unfollow(machineId) {
  const sub = await pushSubscription(false);
  if (sub) await api(`/api/machines/${machineId}/unfollow`, { method: "POST", body: { endpoint: sub.endpoint } });
  store.set("followed", followed().filter((id) => id !== machineId));
  renderBell();
}

// ---------- hiljennys (yläreunan kello) ----------

// muteUntil: ms-aikaleima, "forever" tai null
const muteUntil = () => {
  const u = store.get("muteUntil", null);
  return u === "forever" || (typeof u === "number" && u > Date.now()) ? u : null;
};

function muteStateText() {
  const u = muteUntil();
  if (!u) return t("Ilmoitukset päällä");
  if (u === "forever") return t("Ilmoitukset hiljennetty toistaiseksi");
  const d = new Date(u);
  const sameDay = d.toDateString() === new Date().toDateString();
  const when = d.toLocaleString(LANG, sameDay ? { hour: "2-digit", minute: "2-digit" } : { weekday: "short", hour: "2-digit", minute: "2-digit" });
  return t("Ilmoitukset hiljennetty {until} asti", { until: when });
}

function renderBell() {
  const bell = document.getElementById("bell");
  const show = "PushManager" in window && followed().length > 0;
  bell.classList.toggle("hidden", !show);
  if (!show) { closeMuteMenu(); return; }
  const muted = !!muteUntil();
  bell.classList.toggle("muted", muted);
  bell.replaceChildren(h("img", { src: muted ? "/static/icons/bell-off.svg" : "/static/icons/bell.svg", alt: "" }));
  bell.setAttribute("aria-label", muteStateText());
  bell.title = muteStateText();
}

async function setMute(minutes) {
  try {
    const sub = await pushSubscription(false);
    if (!sub) throw new Error(t("Ilmoitustilausta ei löydy"));
    if (minutes === 0) {
      await api("/api/push/unmute", { method: "POST", body: { endpoint: sub.endpoint } });
      store.set("muteUntil", null);
      toast(t("Hiljennys poistettu"));
    } else {
      await api("/api/push/mute", { method: "POST", body: { endpoint: sub.endpoint, minutes } });
      store.set("muteUntil", minutes ? Date.now() + minutes * 60000 : "forever");
      toast(t("Ilmoitukset hiljennetty"));
    }
  } catch (e) { toast(e.message); }
  closeMuteMenu();
  renderBell();
}

function openMuteMenu() {
  const menu = document.getElementById("mute-menu");
  const muted = !!muteUntil();
  const opt = (label, minutes) => h("button", { class: "secondary", role: "menuitem", onclick: () => setMute(minutes) }, label);
  menu.replaceChildren(
    h("p", { class: "state" }, muteStateText()),
    muted ? h("button", { class: "unmute", role: "menuitem", onclick: () => setMute(0) }, t("Poista hiljennys")) : "",
    h("div", { class: "muted" }, t("Hiljennä ilmoitukset")),
    h("p", {}),
    h("div", { class: "options" },
      opt(t("1 tunniksi"), 60), opt(t("4 tunniksi"), 240), opt(t("Päiväksi"), 1440), opt(t("Toistaiseksi"), null)),
    h("p", { class: "small-print" }, t("Samasta keittimestä tulee enintään yksi ilmoitus kahdessa minuutissa ja toiveista enintään yksi vartissa.")));
  menu.classList.remove("hidden");
  document.getElementById("bell").setAttribute("aria-expanded", "true");
}

function closeMuteMenu() {
  document.getElementById("mute-menu").classList.add("hidden");
  document.getElementById("bell").setAttribute("aria-expanded", "false");
}

document.getElementById("bell").addEventListener("click", (ev) => {
  ev.stopPropagation();
  document.getElementById("mute-menu").classList.contains("hidden") ? openMuteMenu() : closeMuteMenu();
});
document.addEventListener("click", (ev) => {
  if (!ev.target.closest("#mute-menu")) closeMuteMenu();
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeMuteMenu(); });

function pushHint() {
  if (isIOS() && !isStandalone()) {
    return h("div", { class: "hint" },
      t("iPhonella ilmoitukset toimivat vain, jos lisäät sivun kotivalikkoon: "),
      h("b", {}, t("Safari → Jaa → Lisää Koti-valikkoon")), ".");
  }
  if (!("PushManager" in window)) {
    return h("div", { class: "hint" }, t("Tämä selain ei tue push-ilmoituksia."));
  }
  return null;
}

// ---------- tilakuvakkeet (samasta originista -> CSP img-src 'self' riittää) ----------

const FRESH_MINUTES = 30;   // tätä tuoreempi "Kahvia on" = täysi pannu, vanhempi = puolikas
const minutesSince = (ts) => (ts ? Math.max(0, Math.floor((Date.now() / 1000 - ts) / 60)) : null);

// Kuvakkeet piirretty luonnosten pohjalta: /static/icons/<nimi>.svg (scripts/gen_icons.py)
function iconFile(m) {
  if (m.status === "ready") {
    // Tuore "Kahvia on" = täysi pannu, vanhempi = puolikas (vähissä-kuvake)
    const mins = minutesSince(m.updated_at);
    return mins !== null && mins < FRESH_MINUTES ? "ready" : "low";
  }
  return m.status in STATUS && m.status !== "unknown" ? m.status : "empty";
}

function statusIcon(m, cls = "") {
  const label = statusText(m);
  const known = m.status in STATUS && m.status !== "unknown";
  return h("img", {
    src: `/static/icons/${iconFile(m)}.svg`, alt: label, title: label, width: 36, height: 36,
    class: `status-icon ${cls}${known ? "" : " faded"}`,
  });
}

function minutesText(ts) {
  const min = minutesSince(ts);
  if (min === null) return "";
  if (min < 1) return t("nyt");
  if (min < 60) return t("{n} min", { n: min });
  if (min < 24 * 60) return t("{h} h {m} min", { h: Math.floor(min / 60), m: min % 60 });
  return t("yli vrk");
}

// ---------- näkymät ----------

let homeTimer = null;

async function viewHome(app) {
  const local = myMachines();
  const localIds = new Set(local.map((m) => m.id));
  const machines = [...local, ...ME.machines.filter((m) => !localIds.has(m.id))];
  // Ensikävijälle: mikä tää on?
  if (!store.get("introSeen", false)) {
    const intro = h("div", { class: "card intro" },
      h("h2", {}, t("Uusi täällä?")),
      h("p", {}, t("COFFEE!! kertoo työkavereille, onko kahvia, ja hälyttää, kun sitä on tulossa.")),
      h("div", { class: "row" },
        h("button", { onclick: () => go("/ohje") }, t("Mikä tää on?")),
        h("button", { class: "secondary", onclick: () => { store.set("introSeen", true); intro.remove(); } }, t("Sulje"))));
    app.append(intro);
  }

  app.append(h("h1", {}, t("Omat kahvinkeittimet")));

  if (!machines.length) {
    app.append(h("div", { class: "card" },
      h("p", {}, t("Sinulla ei ole vielä kahvinkeittimiä.")),
      h("p", { class: "muted" }, t("Skannaa keittimen QR-koodi puhelimen kameralla tai lisää uusi keitin."))));
  }

  const cards = [];
  const render = (c) => {
    if (c.tick) tickers.delete(c.tick);
    c.tick = null;
    const brewing = c.data.status === "brewing";
    const progress = brewing ? brewProgress(c.data, () => refresh(c)) : null;
    if (progress) c.tick = progress.tick;
    markCard(c.card, c.data);
    c.card.replaceChildren(
      brewing ? bubbles() : "",
      h("div", { class: "status-row" },
        h("a", { class: "title", href: `/m/${c.id}`, title: c.data.name }, c.data.name),
        statusIcon(c.data),
        h("span", { class: "mins" }, minutesText(c.data.updated_at))),
      h("div", { class: "row status-meta" },
        h("span", { class: "muted" }, statusText(c.data)),
        c.data.org ? h("span", { class: "badge" }, c.data.org.name) : null,
        c.data.wishes ? h("span", { class: "wishes" }, t("{n} toivoo kahvia", { n: c.data.wishes })) : null),
      progress ? progress.el : "",
    );
  };
  const refresh = (c) => api(`/api/machines/${c.id}`).then((data) => { c.data = data; render(c); }).catch(() => render(c));

  for (const m of machines) {
    const card = h("div", { class: "card swipe-card" },
      h("div", { class: "status-row" }, h("a", { class: "title", href: `/m/${m.id}` }, m.name)),
      h("p", { class: "muted" }, t("Haetaan…")));
    const wrap = h("div", { class: "swipe" },
      h("div", { class: "swipe-actions", "aria-hidden": "true" },
        h("div", { class: "act act-wish" }, t("Oispa kahvia!")),
        h("div", { class: "act act-brew" }, t("Kahvia tulossa!"))),
      card);
    app.append(wrap);
    const c = { id: m.id, card, data: null };
    api(`/api/machines/${m.id}`).then((data) => {
      c.data = data;
      cards.push(c);
      render(c);
      attachSwipe(wrap, card, {
        left: () => quickStatus(c.id, "brewing", { quick: true }).then(() => refresh(c)),
        right: () => doWish(c.id).then(() => refresh(c)),
      });
    }).catch((e) => {
      const p = card.querySelector("p");
      if (e.status === 401) {
        p.replaceChildren(t("Yksityinen keitin – "), h("a", { href: "/account" }, t("kirjaudu sisään")), ".");
      } else if (e.status === 403) {
        p.textContent = t("Sinulla ei ole enää pääsyä tähän keittimeen.");
      } else if (e.status === 404) {
        p.replaceChildren(t("Keitin on poistettu. "),
          h("button", { class: "secondary", onclick: () => { removeMine(m.id); route(); } }, t("Poista listalta")));
      } else {
        p.textContent = e.message;
      }
    });
  }

  if (machines.length) {
    app.append(h("p", { class: "swipe-hint" },
      t("Pyyhkäise keitintä vasemmalle, niin ilmoitat kahvin olevan tulossa. Oikealle pyyhkäisemällä toivot kahvia.")));
  }
  app.append(h("p", {}), h("button", { class: "secondary", onclick: () => go("/new") }, t("Lisää uusi kahvinkeitin")));
  app.append(h("p", { class: "guide-link" }, h("a", { href: "/ohje" }, t("Mikä tää on?"))));

  // Päivitetään tilat ja minuutit minuutin välein, kun etusivu on auki (ei kesken pyyhkäisyn)
  homeTimer = setInterval(() => {
    if (document.hidden) return;
    for (const c of cards) if (!c.card.classList.contains("dragging")) refresh(c);
  }, 60000);
}

async function quickStatus(id, status, extra = {}) {
  try {
    await api(`/api/machines/${id}/status`, { method: "POST", body: { status, ...extra, device_id: deviceId() } });
    toast(t("Kiitos! Seuraajat saavat ilmoituksen."));
  } catch (e) { toast(e.message); }
}

// iOS-tyylinen pyyhkäisy: kortti seuraa sormea; yli kynnyksen päästäminen laukaisee toiminnon.
// Pystysuunta jätetään selaimelle (touch-action: pan-y), joten sivu vierii normaalisti.
function attachSwipe(wrap, card, actions) {
  let id = null, startX = 0, startY = 0, dx = 0, axis = null, dragged = false;
  const threshold = () => Math.min(130, wrap.offsetWidth * 0.33);
  const reset = () => {
    card.classList.remove("dragging");
    card.style.transform = "";
    wrap.classList.remove("armed", "dir-left", "dir-right");
    id = null; axis = null; dx = 0;
  };
  card.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || id !== null) return;
    id = e.pointerId; startX = e.clientX; startY = e.clientY; dx = 0; axis = null; dragged = false;
  });
  card.addEventListener("pointermove", (e) => {
    if (e.pointerId !== id) return;
    const mx = e.clientX - startX, my = e.clientY - startY;
    if (!axis) {
      if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
      axis = Math.abs(mx) > Math.abs(my) ? "x" : "y";
      if (axis === "y") { id = null; return; }
      card.setPointerCapture(id);
      card.classList.add("dragging");
      dragged = true;
    }
    const max = wrap.offsetWidth * 0.6;
    dx = Math.abs(mx) > max ? Math.sign(mx) * (max + (Math.abs(mx) - max) * 0.2) : mx;   // jousto lopussa
    wrap.classList.toggle("dir-right", dx > 0);
    wrap.classList.toggle("dir-left", dx < 0);
    const armed = Math.abs(dx) >= threshold();
    if (armed !== wrap.classList.contains("armed")) {
      wrap.classList.toggle("armed", armed);
      if (armed && navigator.vibrate) navigator.vibrate(10);
    }
    card.style.transform = `translateX(${dx}px)`;
  });
  const end = (e) => {
    if (e.pointerId !== id) return;
    const fire = axis === "x" && Math.abs(dx) >= threshold() ? (dx < 0 ? actions.left : actions.right) : null;
    reset();
    if (fire) fire();
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", (e) => { if (e.pointerId === id) reset(); });
  // Vedon jälkeen ei avata keittimen linkkiä
  card.addEventListener("click", (e) => {
    if (dragged) { e.preventDefault(); e.stopPropagation(); dragged = false; }
  }, true);
}

async function doWish(id) {
  try {
    const m = await api(`/api/machines/${id}/wish`, { method: "POST", body: { device_id: deviceId() } });
    toast(m.wishes > 1 ? t("Toive lähetetty. Kahvia toivoo nyt {n} ihmistä.", { n: m.wishes }) : t("Toive lähetetty."));
  } catch (e) { toast(e.message); }
}

function viewNew(app) {
  const input = h("input", { type: "text", maxlength: "60", placeholder: t("esim. Toimiston kahvinkeitin"), required: true });

  // Managerille: valinta julkinen / oma organisaatio
  const wanted = new URLSearchParams(location.search).get("org");
  const orgSelect = managedOrgs().length ? h("select", {},
    h("option", { value: "" }, t("Julkinen – kuka tahansa QR-koodin skannaava")),
    managedOrgs().map((o) => h("option", { value: o.id, selected: o.id === wanted }, o.name))) : null;

  const form = h("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      const name = input.value.trim();
      if (!name) return;
      try {
        const body = { name, device_id: deviceId() };
        if (orgSelect && orgSelect.value) body.org_id = orgSelect.value;
        const m = await api("/api/machines", { method: "POST", body });
        addMine(m);
        go(`/m/${m.id}/qr`);
      } catch (e) { toast(e.message); }
    },
  },
    h("label", {}, t("Kahvinkeittimen nimi")), input,
    orgSelect ? [h("label", {}, t("Kuka näkee keittimen?")), orgSelect] : null,
    h("button", { type: "submit" }, t("Luo kahvinkeitin ja QR-koodi")));
  app.append(h("h1", {}, t("Uusi kahvinkeitin")), form);
  input.focus();
}

async function viewQr(app, id) {
  const m = await api(`/api/machines/${id}`);
  app.append(
    h("h1", { class: "no-print" }, t("QR-koodi on valmis")),
    h("p", { class: "muted no-print" }, m.org
      ? t("Tulosta koodi ja teippaa se keittimen kylkeen. Tilanteen voivat päivittää vain organisaation jäsenet.")
      : t("Tulosta koodi ja teippaa se keittimen kylkeen. Kuka tahansa koodin skannaava voi päivittää tilanteen.")),
    h("div", { class: "qr" },
      h("img", { src: `/api/machines/${id}/qr.svg`, alt: t("QR-koodi") }),
      h("div", { class: "name" }, m.name),
      h("div", { class: "muted" }, t("Skannaa ja kerro, onko kahvia"))),
    h("p", { class: "no-print" }),
    h("div", { class: "row no-print" },
      h("button", { onclick: () => window.print() }, t("Tulosta")),
      h("button", { class: "secondary", onclick: () => shareMachine(m) }, t("Jaa linkki")),
      h("button", { class: "secondary", onclick: () => go(`/m/${id}`) }, t("Avaa keitin"))),
  );
}

async function viewMachine(app, id) {
  let m;
  try { m = await api(`/api/machines/${id}`); }
  catch (e) {
    // Yksityinen keitin: nimeä tai organisaatiota ei näytetä ennen kuin oikeus on tarkistettu
    if (e.status === 401) {
      app.append(
        h("h1", {}, t("Yksityinen kahvinkeitin")),
        h("p", {}, t("Tämä keitin kuuluu organisaatiolle. Kirjaudu sisään, niin katsotaan, kuulutko siihen.")),
        authForms(() => route()));
    } else if (e.status === 403) {
      app.append(
        h("h1", {}, t("Ei pääsyä")),
        h("p", {}, t("Et kuulu organisaatioon, jolle tämä keitin kuuluu. Jos sait kutsukoodin, voit liittyä tässä:")),
        joinForm(() => route()));
    } else {
      app.append(h("h1", {}, t("Hups!")), h("p", {}, e.message));
    }
    return;
  }

  // Ensimmäinen skannaus: kysytään lisätäänkö omiin
  if (!isMine(id)) {
    app.append(
      h("h1", {}, m.name),
      h("div", { class: "card" },
        h("p", {}, t("Lisätäänkö tämä keitin omalle listallesi?")),
        h("div", { class: "row" },
          h("button", { onclick: () => { addMine(m); route(); } }, t("Kyllä, lisää")),
          h("button", { class: "secondary", onclick: () => go("/") }, t("Ei kiitos")))),
    );
    return;
  }

  const cupsInput = h("input", { id: "brew-cups", type: "number", min: "1", max: "999", inputmode: "numeric",
    value: String(m.last_brew_cups || m.full_pot_cups) });
  const lastType = m.coffee_types.includes(m.last_brew_type) ? m.last_brew_type : m.coffee_types[0];
  const typeSelect = h("select", { id: "brew-type" },
    m.coffee_types.map((ct) => h("option", { value: ct, selected: ct === lastType }, t(ct))));
  const cupsBox = h("div", { class: "card hidden" },
    h("label", { for: "brew-type" }, t("Mitä kahvia?")), typeSelect,
    h("label", { for: "brew-cups" }, t("Montako kuppia tulossa?")), cupsInput,
    h("button", {
      onclick: () => setStatus("brewing", { cups: parseInt(cupsInput.value, 10) || null, coffee_type: typeSelect.value }),
    }, t("Ilmoita")));

  async function setStatus(status, extra = {}) {
    try {
      await api(`/api/machines/${id}/status`, { method: "POST", body: { status, ...extra, device_id: deviceId() } });
      toast(t("Kiitos! Seuraajat saavat ilmoituksen."));
      route();
    } catch (e) { toast(e.message); }
  }

  const buttons = Object.entries(STATUS).filter(([k]) => k !== "unknown").map(([key, label]) =>
    h("button", {
      class: m.status === key ? "active" : "",
      onclick: () => key === "brewing" ? cupsBox.classList.toggle("hidden") : setStatus(key),
    }, statusIcon({ status: key, updated_at: Date.now() / 1000 }, "icon-btn"), h("span", {}, t(label))));

  const progress = m.status === "brewing" ? brewProgress(m, () => route()) : null;
  const statusCard = h("div", { class: "card status-card" },
    m.status === "brewing" ? bubbles() : "",
    h("div", { class: "status-big" }, statusIcon(m, "icon-big"), h("span", {}, statusText(m))),
    progress ? progress.el : "",
    h("div", { class: "row" },
      h("span", { class: "muted" }, m.updated_at ? t("Päivitetty {ago}", { ago: ago(m.updated_at) }) : ""),
      m.wishes ? h("span", { class: "wishes" }, t("{n} toivoo kahvia", { n: m.wishes })) : null));
  markCard(statusCard, m);

  const isFollowing = followed().includes(id);
  const followBtn = h("button", {
    class: "secondary",
    onclick: async () => {
      followBtn.disabled = true;
      try {
        if (isFollowing) { await unfollow(id); toast(t("Ilmoitukset pois päältä")); }
        else { await follow(id); toast(t("Ilmoitukset päällä")); }
        route();
      } catch (e) { toast(e.message); followBtn.disabled = false; }
    },
  }, isFollowing ? t("Lopeta seuraaminen") : t("Seuraa ja saa ilmoitukset"));

  app.append(
    h("h1", {}, m.name),
    m.org ? h("p", {}, h("a", { class: "badge", href: `/org/${m.org.id}` }, m.org.name)) : "",
    statusCard,
    h("button", { class: "wish", onclick: () => doWish(id).then(() => route()) }, t("Oispa kahvia!")),
    h("h2", {}, t("Päivitä tilanne")),
    h("div", { class: "grid" }, buttons),
    cupsBox,
    h("h2", {}, t("Ilmoitukset")),
    pushHint() || "",
    followBtn,
    topBrewers(id),
    m.can_edit ? machineSettings(m) : "",
    h("h2", {}, t("Muuta")),
    h("div", { class: "row" },
      h("button", { class: "secondary", onclick: () => go(`/m/${id}/qr`) }, t("Näytä QR-koodi")),
      isLocal(id) && h("button", {
        class: "secondary",
        onclick: async () => {
          if (followed().includes(id)) { try { await unfollow(id); } catch {} }
          removeMine(id);
          go("/");
        },
      }, t("Poista listalta"))),
  );
}

// Keittimen asetukset (luoja tai organisaation ylläpitäjä)
function machineSettings(m) {
  const pot = h("input", { id: "set-pot", type: "number", min: "1", max: "999", value: String(m.full_pot_cups), inputmode: "numeric", required: true });
  const brewTime = h("input", { id: "set-brew", type: "number", min: "0", max: "30", step: "0.5", inputmode: "decimal",
    value: String(m.brew_min_per_cup), required: true });
  // Oletustyypit näytetään käyttäjän kielellä; tallennettaessa käännös muutetaan takaisin avaimeksi
  const shown = m.coffee_types.map((ct) => t(ct));
  const types = h("textarea", { id: "set-types", rows: "4", maxlength: "300" });
  types.value = shown.join("\n");
  return h("details", { class: "card" },
    h("summary", {}, t("Keittimen asetukset")),
    h("form", {
      onsubmit: async (ev) => {
        ev.preventDefault();
        const coffee_types = types.value.split("\n").map((x) => x.trim()).filter(Boolean)
          .map((x) => { const i = shown.indexOf(x); return i >= 0 ? m.coffee_types[i] : x; });
        if (!coffee_types.length) { toast(t("Anna vähintään yksi kahvityyppi")); return; }
        try {
          await api(`/api/machines/${m.id}/settings`, { method: "POST", body: {
            full_pot_cups: parseInt(pot.value, 10),
            brew_min_per_cup: parseFloat(String(brewTime.value).replace(",", ".")),
            coffee_types,
          } });
          toast(t("Asetus tallennettu"));
          route();
        } catch (e) { toast(e.message); }
      },
    },
      h("label", { for: "set-pot" }, t("Täysi pannu (kuppia)")), pot,
      h("p", { class: "muted" }, t("Tätä käytetään pikailmoituksessa, jos keittimellä ei ole vielä aiempia pannuja.")),
      h("label", { for: "set-brew" }, t("Keittoaika (min / kuppi)")), brewTime,
      h("p", { class: "muted" }, t("Kun keittoaika on kulunut, tila vaihtuu itsestään: kahvia on. 0 = kahvi on valmista heti, kun keitin on ladattu.")),
      h("label", { for: "set-types" }, t("Kahvityypit (yksi per rivi, enintään 8)")), types,
      h("button", { type: "submit" }, t("Tallenna"))));
}

// TOP 10 Brewers: kirjautuneiden keittämät pannut tähän keittimeen
function topBrewers(id) {
  const box = h("div", {}, h("h2", {}, t("TOP 10 Brewers")));
  api(`/api/machines/${id}/top`).then(({ top, me }) => {
    if (top.length) {
      box.append(h("ol", { class: "card top-list" }, top.map((row, i) =>
        h("li", { class: row.username === me ? "me" : "" },
          h("span", { class: "rank" }, `${i + 1}.`),
          h("span", { class: "who" }, row.username, row.username === me ? h("span", { class: "you" }, t(" (sinä)")) : null),
          h("span", { class: "brews" }, row.brews === 1 ? t("1 pannu") : t("{n} pannua", { n: row.brews }))))));
    } else {
      box.append(h("p", { class: "muted" }, t("Listalla ei ole vielä ketään. Keitä pannu ja ilmoita se, niin pääset ensimmäiseksi.")));
    }
    if (!me) {
      box.append(h("p", { class: "muted" }, t("Pannut lasketaan vain kirjautuneille. "),
        h("a", { href: "/account" }, t("Kirjaudu sisään")), t(", niin keittämäsi pannut näkyvät listalla käyttäjätunnuksellasi.")));
    }
  }).catch(() => box.remove());
  return box;
}

// ---------- tunnukset ----------

function authForms(onSuccess) {
  const wrap = h("div");
  const show = (mode) => {
    wrap.replaceChildren(
      h("div", { class: "tabs" },
        h("button", { class: mode === "login" ? "active" : "", onclick: () => show("login") }, t("Kirjaudu")),
        h("button", { class: mode === "register" ? "active" : "", onclick: () => show("register") }, t("Luo tunnus"))),
      mode === "login" ? loginForm(onSuccess) : registerForm(onSuccess));
  };
  show("login");
  return wrap;
}

function loginForm(onSuccess) {
  const user = h("input", { type: "text", autocomplete: "username", autocapitalize: "none", required: true });
  const pass = h("input", { type: "password", autocomplete: "current-password", required: true });
  return h("form", {
    class: "card",
    onsubmit: async (ev) => {
      ev.preventDefault();
      try {
        await api("/api/auth/login", { method: "POST", body: { username: user.value.trim(), password: pass.value } });
        await loadMe();
        toast(t("Tervetuloa takaisin"));
        onSuccess();
      } catch (e) { toast(e.message); }
    },
  },
    h("label", {}, t("Käyttäjätunnus")), user,
    h("label", {}, t("Salasana")), pass,
    h("button", { type: "submit" }, t("Kirjaudu sisään")));
}

function registerForm(onSuccess) {
  const user = h("input", { type: "text", autocomplete: "username", autocapitalize: "none", minlength: "3", maxlength: "32", required: true });
  const pass = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  const pass2 = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  return h("form", {
    class: "card",
    onsubmit: async (ev) => {
      ev.preventDefault();
      if (pass.value !== pass2.value) { toast(t("Salasanat eivät täsmää")); return; }
      try {
        await api("/api/auth/register", {
          method: "POST", body: { username: user.value.trim(), password: pass.value, password2: pass2.value },
        });
        await loadMe();
        toast(t("Tunnus luotu"));
        onSuccess();
      } catch (e) { toast(e.message); }
    },
  },
    h("div", { class: "hint" },
      h("b", {}, t("Huom: ")),
      t("sähköpostia ei kysytä, joten unohtunutta salasanaa ei voi palauttaa. "),
      t("Jos salasana unohtuu, tunnus on menetetty, joten tallenna se vaikka salasanamanageriin.")),
    h("label", {}, t("Käyttäjätunnus (3–32 merkkiä)")), user,
    h("label", {}, t("Salasana (vähintään 10 merkkiä)")), pass,
    h("label", {}, t("Salasana uudelleen")), pass2,
    h("button", { type: "submit" }, t("Luo tunnus")));
}

function joinForm(onSuccess) {
  const code = h("input", { type: "text", placeholder: "XXX-XXX", maxlength: "9", autocapitalize: "characters", class: "code-input", required: true });
  return h("form", {
    class: "card",
    onsubmit: async (ev) => {
      ev.preventDefault();
      try {
        const org = await api("/api/orgs/join", { method: "POST", body: { code: code.value } });
        await loadMe();
        toast(t("Liityit organisaatioon {name}", { name: org.name }));
        onSuccess(org);
      } catch (e) { toast(e.message); }
    },
  },
    h("label", {}, t("Kutsukoodi")), code,
    h("button", { type: "submit" }, t("Liity organisaatioon")));
}

async function viewAccount(app) {
  if (!ME.user) {
    app.append(
      h("h1", {}, t("Tunnus")),
      h("p", { class: "muted" },
        t("Perusominaisuudet toimivat ilman tunnusta. Tunnuksella voit luoda organisaation, jonka kahvinkeittimet näkyvät vain jäsenille.")),
      authForms(() => route()));
    if (isIOS() && !isStandalone()) {
      app.append(h("div", { class: "hint" },
        t("Huom: iPhonen kotivalikkoon lisätty sovellus ei näe Safarin kirjautumista. Kirjaudu siis sovelluksessa vielä erikseen.")));
    }
    return;
  }

  app.append(h("h1", {}, ME.user.username));

  app.append(h("h2", {}, t("Organisaatiot")));
  if (!ME.orgs.length) app.append(h("p", { class: "muted" }, t("Et kuulu vielä mihinkään organisaatioon.")));
  for (const o of ME.orgs) {
    app.append(h("a", { class: "card cardlink", href: `/org/${o.id}` },
      h("div", { class: "row" },
        h("span", { class: "title" }, o.name),
        h("span", { class: "badge" }, o.is_founder ? t("Perustaja") : o.is_manager ? t("Ylläpitäjä") : t("Jäsen")))));
  }

  app.append(
    h("h2", {}, t("Liity organisaatioon")),
    joinForm((org) => go(`/org/${org.id}`)),
    h("p", { class: "muted" }, t("Voit liittyä useaan organisaatioon. Kaikkien organisaatioidesi keittimet näkyvät etusivulla.")));

  const name = h("input", { type: "text", maxlength: "60", placeholder: t("esim. Kofeiinikopla"), required: true });
  const auto = h("input", { type: "checkbox" });
  app.append(
    h("h2", {}, t("Luo organisaatio")),
    h("form", {
      class: "card",
      onsubmit: async (ev) => {
        ev.preventDefault();
        try {
          const org = await api("/api/orgs", { method: "POST", body: { name: name.value.trim(), auto_manager: auto.checked } });
          await loadMe();
          go(`/org/${org.id}`);
        } catch (e) { toast(e.message); }
      },
    },
      h("div", { class: "hint" },
        t("Vinkki: yrityksen oikeaa nimeä ei tarvita. Lempinimi riittää, esim. \"Kofeiinikopla\" tai \"3. kerroksen pannuporukka\".")),
      h("label", {}, t("Organisaation lempinimi")), name,
      h("label", { class: "check" }, auto, t(" Kaikki liittyjät ovat automaattisesti ylläpitäjiä")),
      h("button", { type: "submit" }, t("Luo organisaatio"))));

  // Salasanan vaihto ja tilin poisto
  const oldPw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const newPw = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  const newPw2 = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  const delPw = h("input", { type: "password", autocomplete: "current-password", required: true });

  app.append(
    h("h2", {}, t("Tili")),
    h("button", {
      class: "secondary",
      onclick: async () => { await api("/api/auth/logout", { method: "POST" }); await loadMe(); toast(t("Kirjauduit ulos")); go("/"); },
    }, t("Kirjaudu ulos")),
    h("details", { class: "card" },
      h("summary", {}, t("Vaihda salasana")),
      h("form", {
        onsubmit: async (ev) => {
          ev.preventDefault();
          try {
            await api("/api/auth/password", {
              method: "POST", body: { old_password: oldPw.value, new_password: newPw.value, new_password2: newPw2.value },
            });
            toast(t("Salasana vaihdettu"));
            route();
          } catch (e) { toast(e.message); }
        },
      },
        h("label", {}, t("Nykyinen salasana")), oldPw,
        h("label", {}, t("Uusi salasana")), newPw,
        h("label", {}, t("Uusi salasana uudelleen")), newPw2,
        h("button", { type: "submit" }, t("Vaihda salasana")))),
    h("details", { class: "card danger" },
      h("summary", {}, t("Poista tunnus")),
      h("p", { class: "muted" }, t("Tunnus, jäsenyydet ja ilmoitustilaukset poistetaan pysyvästi. Perustamasi organisaatiot pitää poistaa ensin.")),
      h("form", {
        onsubmit: async (ev) => {
          ev.preventDefault();
          try {
            await api("/api/auth/delete", { method: "POST", body: { password: delPw.value } });
            await loadMe();
            toast(t("Tunnus poistettu"));
            go("/");
          } catch (e) { toast(e.message); }
        },
      },
        h("label", {}, t("Vahvista salasanalla")), delPw,
        h("button", { type: "submit", class: "danger" }, t("Poista tunnus pysyvästi")))),
  );
}

// ---------- organisaatio ----------

async function viewOrg(app, id) {
  if (!ME.user) { go("/account"); return; }
  let o;
  try { o = await api(`/api/orgs/${id}`); }
  catch (e) { app.append(h("h1", {}, t("Hups!")), h("p", {}, e.message)); return; }

  const act = (fn, okText) => async () => {
    try { await fn(); if (okText) toast(okText); await loadMe(); route(); } catch (e) { toast(e.message); }
  };
  const post = (path, body) => api(`/api/orgs/${id}${path}`, { method: "POST", body });

  // Kutsukoodi
  app.append(
    h("h1", {}, o.name),
    h("div", { class: "card" },
      h("div", { class: "muted" }, t("Kutsukoodi – jaa tämä niille, jotka haluat mukaan:")),
      h("div", { class: "invite" }, o.invite_code),
      h("div", { class: "row" },
        h("button", {
          class: "secondary",
          onclick: async () => {
            try { await navigator.clipboard.writeText(o.invite_code); toast(t("Kopioitu")); } catch { toast(o.invite_code); }
          },
        }, t("Kopioi")),
        o.is_manager ? h("button", {
          class: "secondary", onclick: act(() => post("/invite/rotate"), t("Uusi kutsukoodi luotu. Vanha ei enää toimi.")),
        }, t("Vaihda koodi")) : null)),
  );

  // Keittimet
  app.append(h("h2", {}, t("Kahvinkeittimet")));
  if (!o.machines.length) app.append(h("p", { class: "muted" }, t("Ei vielä keittimiä.")));
  for (const m of o.machines) {
    app.append(h("div", { class: "card" },
      h("div", { class: "row" },
        h("a", { class: "title", href: `/m/${m.id}` }, m.name),
        o.is_manager ? h("button", {
          class: "secondary small",
          onclick: act(() => post(`/machines/${m.id}/unlink`), t("Keitin irrotettu. Se on nyt julkinen.")),
        }, t("Irrota")) : null),
      h("div", { class: "status-line" }, statusIcon(m, "icon-small"), h("span", {}, statusText(m)))));
  }

  if (o.is_manager) {
    const linked = new Set(o.machines.map((m) => m.id));
    const candidates = myMachines().filter((m) => !linked.has(m.id));
    app.append(
      h("button", { onclick: () => go(`/new?org=${id}`) }, t("Uusi keitin tähän organisaatioon")),
      candidates.length ? h("details", { class: "card" },
        h("summary", {}, t("Liitä olemassa oleva keitin")),
        h("p", { class: "muted" }, t("Liitetty keitin muuttuu yksityiseksi: sen QR-koodi toimii vain jäsenille, ja muiden ilmoitustilaukset poistetaan. Vain keittimen luoja voi liittää sen.")),
        candidates.map((m) => h("div", { class: "row line" },
          h("span", {}, m.name),
          h("button", {
            class: "secondary small",
            onclick: act(() => post("/machines", { machine_id: m.id, device_id: deviceId() }), t("Keitin liitetty")),
          }, t("Liitä"))))) : "",
    );
  }

  // Jäsenet
  app.append(h("h2", {}, t("Jäsenet ({n})", { n: o.members.length })));
  const list = h("div", { class: "card" });
  for (const mem of o.members) {
    const role = mem.is_founder ? t("Perustaja") : mem.is_manager ? t("Ylläpitäjä") : t("Jäsen");
    const controls = o.is_founder && !mem.is_founder ? [
      h("button", {
        class: "secondary small",
        onclick: act(() => post(`/members/${mem.id}/manager`, { is_manager: !mem.is_manager })),
      }, mem.is_manager ? t("Poista ylläpito-oikeus") : t("Tee ylläpitäjäksi")),
      h("button", { class: "secondary small", onclick: act(() => post(`/members/${mem.id}/remove`), t("Jäsen poistettu")) }, t("Poista")),
    ] : [];
    list.append(h("div", { class: "row line" },
      h("span", {}, mem.username, " ", h("span", { class: "badge" }, role)),
      h("span", { class: "row" }, controls)));
  }
  app.append(list);

  // Asetukset & vaara-alue
  if (o.is_founder) {
    const auto = h("input", { type: "checkbox", checked: o.auto_manager || null,
      onchange: act(() => post("/settings", { auto_manager: auto.checked }), t("Asetus tallennettu")) });
    const confirm = h("input", { type: "text", placeholder: o.name, required: true });
    app.append(
      h("h2", {}, t("Asetukset")),
      h("label", { class: "check card" }, auto, t(" Kaikki uudet liittyjät ovat automaattisesti ylläpitäjiä")),
      h("details", { class: "card danger" },
        h("summary", {}, t("Poista organisaatio")),
        h("p", { class: "muted" }, t("Organisaatio, sen jäsenyydet ja kaikki sen kahvinkeittimet poistetaan pysyvästi. Tulostetut QR-koodit lakkaavat toimimasta. Irrota keittimet ensin, jos haluat säilyttää ne julkisina.")),
        h("form", {
          onsubmit: async (ev) => {
            ev.preventDefault();
            try {
              await post("/delete", { confirm_name: confirm.value });
              await loadMe();
              toast(t("Organisaatio poistettu"));
              go("/account");
            } catch (e) { toast(e.message); }
          },
        },
          h("label", {}, t("Kirjoita organisaation nimi vahvistukseksi")), confirm,
          h("button", { type: "submit", class: "danger" }, t("Poista organisaatio pysyvästi")))),
    );
  } else {
    app.append(
      h("h2", {}, t("Muuta")),
      h("button", {
        class: "secondary",
        onclick: async () => {
          try { await post("/leave"); await loadMe(); toast(t("Erosit organisaatiosta")); go("/account"); }
          catch (e) { toast(e.message); }
        },
      }, t("Eroa organisaatiosta")));
  }
}

// ---------- ohje ensikävijälle ----------

function viewGuide(app) {
  store.set("introSeen", true);
  const step = (n, title, text, action) => h("div", { class: "card guide-step" },
    h("div", { class: "step-no" }, String(n)),
    h("div", {},
      h("h2", {}, title),
      h("p", {}, text),
      action || ""));
  const btn = (label, path) => h("button", { class: "secondary", onclick: () => go(path) }, label);
  app.append(
    h("h1", {}, t("Mikä tää on?")),
    h("p", { class: "muted" }, t("COFFEE!! on kahvinkeittimen ilmoitustaulu. Kun joku keittää kahvia, muut näkevät sen heti puhelimestaan.")),
    step(1, t("Ei tarvitse rekisteröityä"),
      t("Voit aloittaa heti ilman tunnusta. Omat keittimesi tallentuvat tämän laitteen selaimeen.")),
    step(2, t("Lisää keitin"),
      t("Anna keittimelle nimi, niin saat siitä QR-koodin."),
      btn(t("Lisää uusi kahvinkeitin"), "/new")),
    step(3, t("Jaa keitin muiden kanssa"),
      t("Tulosta QR-koodi ja teippaa se keittimen kylkeen. Kun työkaveri skannaa koodin puhelimellaan, hän näkee kahvitilanteen ja voi päivittää sitä. Linkin voi myös lähettää viestillä QR-koodisivun Jaa linkki -napista.")),
    step(4, t("Kerro ja seuraa"),
      t("Etusivulla pyyhkäise keitintä vasemmalle, kun kahvia on tulossa, ja oikealle, kun kaipaat kahvia. Keittimen sivulla voit laittaa ilmoitukset päälle. iPhonella ilmoitukset toimivat, kun lisäät sivun kotivalikkoon.")),
    step(5, t("Luo tunnus, jos tarvitset"),
      t("Tunnusta tarvitaan vain yksityisiin keittimiin ja TOP 10 Brewers -listalle. Sähköpostia ei kysytä."),
      ME.user ? "" : btn(t("Luo tunnus"), "/account")),
    step(6, t("Perusta organisaatio tiimillesi"),
      t("Organisaatio on porukka, jonka keittimet näkyvät vain sen jäsenille. Perusta se tilisivulla ja jaa kutsukoodi työkavereille. Kaikkien organisaatioidesi keittimet näkyvät etusivulla."),
      btn(t("Tilisivulle"), "/account")),
    h("p", {}),
    h("button", { onclick: () => go("/") }, t("Selvä, aloitetaan")),
  );
}

// Keittimen linkin jako: puhelimessa jakovalikko, muuten kopio leikepöydälle
async function shareMachine(m) {
  const url = `${location.origin}/m/${m.id}`;
  try {
    if (navigator.share) { await navigator.share({ title: m.name, url }); return; }
    await navigator.clipboard.writeText(url);
    toast(t("Linkki kopioitu"));
  } catch (e) {
    if (e && e.name === "AbortError") return;   // käyttäjä sulki jakovalikon
    toast(url);
  }
}

// ---------- alalaita, kieli ja versio ----------

const VERSION = document.querySelector('meta[name="app-version"]')?.content || "";
const BUILD = document.querySelector('meta[name="app-build"]')?.content || "";

function renderChrome() {
  document.getElementById("account-link").textContent = ME.user ? ME.user.username : t("Kirjaudu");
  renderBell();
  document.getElementById("footer").replaceChildren(
    h("p", {}, t("COFFEE!! on harrastusprojekti, joka tehtiin Clauden (Anthropicin tekoälyavustaja) kanssa alle tunnissa. Se toimii, mutta kaikkea ei ole viimeisen päälle hiottu.")),
    h("p", {}, t("Sähköpostia tai oikeaa nimeä ei kysytä. Oma keitinlistasi tallentuu vain tämän laitteen selaimeen. Kirjautuminen käyttää yhtä evästettä, ei seurantaa eikä mainoksia.")),
    h("p", { class: "lang-row" },
      Object.entries(LANGS).map(([code, name]) => h("button", {
        class: "lang" + (code === LANG ? " active" : ""), lang: code, "aria-pressed": String(code === LANG),
        onclick: () => { setLang(code); renderChrome(); route(); },
      }, name)),
      VERSION ? h("span", { class: "version" }, t("Versio {v}", { v: VERSION })) : null),
  );
}

// Uuden version tunnistus: palvelimen build eroaa siitä, jolla tämä sivu ladattiin -> pyydetään päivittämään
let updateShown = false;
async function checkVersion() {
  if (updateShown || !BUILD || document.hidden) return;
  try {
    const cfg = await api("/api/config");
    if (cfg.build && cfg.build !== BUILD) showUpdateBanner(cfg.version);
  } catch { /* offline tms. */ }
}

function showUpdateBanner(version) {
  updateShown = true;
  const bar = document.getElementById("update-banner");
  bar.replaceChildren(
    h("div", {},
      h("b", {}, t("Uusi versio on saatavilla.")), version ? ` (${version})` : "",
      h("div", { class: "small-print" }, t("Jos sivu ei päivity, sulje sovellus kokonaan ja avaa se uudelleen."))),
    h("button", { onclick: () => location.reload() }, t("Päivitä nyt")));
  bar.classList.remove("hidden");
}

document.addEventListener("visibilitychange", () => { if (!document.hidden) checkVersion(); });
setInterval(checkVersion, 5 * 60 * 1000);

// ---------- reititys ----------

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

async function route() {
  clearInterval(homeTimer);
  homeTimer = null;
  stopTickers();
  const app = document.getElementById("app");
  app.replaceChildren();
  const p = location.pathname;
  let match;
  try {
    if ((match = p.match(new RegExp(`^/m/(${UUID})/qr$`)))) await viewQr(app, match[1]);
    else if ((match = p.match(new RegExp(`^/m/(${UUID})$`)))) await viewMachine(app, match[1]);
    else if ((match = p.match(new RegExp(`^/org/(${UUID})$`)))) await viewOrg(app, match[1]);
    else if (p === "/account") await viewAccount(app);
    else if (p === "/new") viewNew(app);
    else if (p === "/ohje") viewGuide(app);
    else await viewHome(app);
  } catch (e) {
    app.append(h("h1", {}, t("Hups!")), h("p", {}, e.message));
  }
}

document.addEventListener("click", (ev) => {
  const a = ev.target.closest("a[href^='/']");
  if (a && !ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); go(a.getAttribute("href")); }
});
window.addEventListener("popstate", route);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
loadMe().then(route);

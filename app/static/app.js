// COFFEE!! ☕ frontend – ei frameworkkeja, ei ulkoisia kirjastoja.
// Turvallisuus: kaikki käyttäjän syöte (keittimen nimi) laitetaan DOMiin textContentina, ei koskaan innerHTML:nä.
"use strict";

const STATUS = {
  ready:        "☕ Kahvia on!",
  brewing:      "⏳ Kahvia tulossa",
  low:          "🫗 Kahvi on vähissä",
  empty:        "🚫 Kahvi loppu",
  supplies_low: "⚠️ Kahvitarvikkeet vähissä",
  supplies_out: "🆘 Kahvitarvikkeet loppu",
  unknown:      "❔ Ei tietoa vielä",
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
const myMachines = () => store.get("machines", []);
const isMine = (id) => myMachines().some((m) => m.id === id);
function addMine(m) {
  if (!isMine(m.id)) store.set("machines", [...myMachines(), { id: m.id, name: m.name }]);
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
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let msg = `Virhe ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch {}
    if (typeof msg !== "string") msg = "Virheellinen syöte";
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3000);
}

function ago(ts) {
  if (!ts) return "";
  const min = Math.round((Date.now() / 1000 - ts) / 60);
  if (min < 1) return "juuri nyt";
  if (min < 60) return `${min} min sitten`;
  const hrs = Math.round(min / 60);
  return hrs < 24 ? `${hrs} h sitten` : "yli vuorokausi sitten";
}

function statusText(m) {
  let t = STATUS[m.status] || STATUS.unknown;
  if (m.status === "brewing" && m.cups) t += ` (${m.cups} kuppia)`;
  return t;
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
    if (!cfg.push_enabled) throw new Error("Palvelimella ei ole push-ilmoituksia käytössä");
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
  if (perm !== "granted") throw new Error("Ilmoituslupaa ei annettu");
  const sub = await pushSubscription(true);
  await api(`/api/machines/${machineId}/follow`, {
    method: "POST",
    body: { device_id: deviceId(), subscription: sub.toJSON() },
  });
  store.set("followed", [...new Set([...followed(), machineId])]);
}

async function unfollow(machineId) {
  const sub = await pushSubscription(false);
  if (sub) await api(`/api/machines/${machineId}/unfollow`, { method: "POST", body: { endpoint: sub.endpoint } });
  store.set("followed", followed().filter((id) => id !== machineId));
}

function pushHint() {
  if (isIOS() && !isStandalone()) {
    return h("div", { class: "hint" },
      "📲 iPhonella ilmoitukset toimivat, kun lisäät sovelluksen kotivalikkoon: ",
      h("b", {}, "Safari → Jaa → Lisää Koti-valikkoon"), ".");
  }
  if (!("PushManager" in window)) {
    return h("div", { class: "hint" }, "Tämä selain ei tue push-ilmoituksia.");
  }
  return null;
}

// ---------- näkymät ----------

async function viewHome(app) {
  const machines = myMachines();
  app.append(h("h1", {}, "Omat kahvinkeittimet"));

  if (!machines.length) {
    app.append(h("div", { class: "card" },
      h("p", {}, "Sinulla ei ole vielä kahvinkeittimiä."),
      h("p", { class: "muted" }, "Skannaa keittimen QR-koodi puhelimen kameralla tai lisää uusi keitin.")));
  }

  for (const m of machines) {
    const card = h("div", { class: "card" }, h("a", { class: "title", href: `/m/${m.id}` }, m.name), h("p", { class: "muted" }, "Haetaan…"));
    app.append(card);
    api(`/api/machines/${m.id}`).then((data) => {
      card.replaceChildren(
        h("a", { class: "title", href: `/m/${m.id}` }, data.name),
        h("div", { class: "status-big" }, statusText(data)),
        h("div", { class: "row" },
          h("span", { class: "muted" }, ago(data.updated_at)),
          data.wishes ? h("span", { class: "wishes" }, `🙏 ${data.wishes}`) : null),
        h("p", {}),
        h("button", { class: "wish", onclick: () => doWish(m.id).then(() => route()) }, "Olispa kahvia! ☕"),
      );
    }).catch((e) => {
      card.querySelector("p").textContent = e.message;
    });
  }

  app.append(h("p", {}), h("button", { class: "secondary", onclick: () => go("/new") }, "➕ Lisää uusi kahvinkeitin"));
}

async function doWish(id) {
  try {
    const m = await api(`/api/machines/${id}/wish`, { method: "POST", body: { device_id: deviceId() } });
    toast(`Toive lähetetty! ${m.wishes} kaipaa kahvia 🙏`);
  } catch (e) { toast(e.message); }
}

function viewNew(app) {
  const input = h("input", { type: "text", maxlength: "60", placeholder: "esim. Toimiston kahvinkeitin", required: true });
  const form = h("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      const name = input.value.trim();
      if (!name) return;
      try {
        const m = await api("/api/machines", { method: "POST", body: { name } });
        addMine(m);
        go(`/m/${m.id}/qr`);
      } catch (e) { toast(e.message); }
    },
  },
    h("label", {}, "Kahvinkeittimen nimi"), input,
    h("button", { type: "submit" }, "Luo kahvinkeitin ja QR-koodi"));
  app.append(h("h1", {}, "Uusi kahvinkeitin"), form);
  input.focus();
}

async function viewQr(app, id) {
  const m = await api(`/api/machines/${id}`);
  app.append(
    h("h1", { class: "no-print" }, "QR-koodi valmis! 🎉"),
    h("p", { class: "muted no-print" }, "Tulosta ja kiinnitä kahvinkeittimeen. Koodin skannaamalla kuka tahansa voi päivittää tilan."),
    h("div", { class: "qr" },
      h("img", { src: `/api/machines/${id}/qr.svg`, alt: "QR-koodi" }),
      h("div", { class: "name" }, `☕ ${m.name}`),
      h("div", { class: "muted" }, "Skannaa ja kerro kahvitilanne!")),
    h("p", { class: "no-print" }),
    h("div", { class: "row no-print" },
      h("button", { onclick: () => window.print() }, "🖨️ Tulosta"),
      h("button", { class: "secondary", onclick: () => go(`/m/${id}`) }, "Siirry keittimeen →")),
  );
}

async function viewMachine(app, id) {
  let m;
  try { m = await api(`/api/machines/${id}`); }
  catch (e) { app.append(h("h1", {}, "Hups!"), h("p", {}, e.message)); return; }

  // Ensimmäinen skannaus: kysytään lisätäänkö omiin
  if (!isMine(id)) {
    app.append(
      h("h1", {}, m.name),
      h("div", { class: "card" },
        h("p", {}, "Lisätäänkö tämä kahvinkeitin omiin kahvinkeittimiisi?"),
        h("div", { class: "row" },
          h("button", { onclick: () => { addMine(m); route(); } }, "Kyllä, lisää ☕"),
          h("button", { class: "secondary", onclick: () => go("/") }, "Ei kiitos"))),
    );
    return;
  }

  const cupsInput = h("input", { type: "number", min: "1", max: "100", value: "10", inputmode: "numeric" });
  const cupsBox = h("div", { class: "card hidden" },
    h("label", {}, "Montako kuppia tulossa?"), cupsInput,
    h("button", { onclick: () => setStatus("brewing", parseInt(cupsInput.value, 10) || null) }, "⏳ Ilmoita: kahvia tulossa"));

  async function setStatus(status, cups = null) {
    try {
      await api(`/api/machines/${id}/status`, { method: "POST", body: { status, cups, device_id: deviceId() } });
      toast("Kiitos! Tieto välitetty seuraajille 📣");
      route();
    } catch (e) { toast(e.message); }
  }

  const buttons = Object.entries(STATUS).filter(([k]) => k !== "unknown").map(([key, label]) =>
    h("button", {
      class: m.status === key ? "active" : "",
      onclick: () => key === "brewing" ? cupsBox.classList.toggle("hidden") : setStatus(key),
    }, label));

  const isFollowing = followed().includes(id);
  const followBtn = h("button", {
    class: "secondary",
    onclick: async () => {
      followBtn.disabled = true;
      try {
        if (isFollowing) { await unfollow(id); toast("Ilmoitukset pois päältä 🔕"); }
        else { await follow(id); toast("Ilmoitukset päällä! 🔔"); }
        route();
      } catch (e) { toast(e.message); followBtn.disabled = false; }
    },
  }, isFollowing ? "🔕 Lopeta seuraaminen" : "🔔 Seuraa (ilmoitukset)");

  app.append(
    h("h1", {}, m.name),
    h("div", { class: "card" },
      h("div", { class: "status-big" }, statusText(m)),
      h("div", { class: "row" },
        h("span", { class: "muted" }, m.updated_at ? `Päivitetty ${ago(m.updated_at)}` : ""),
        m.wishes ? h("span", { class: "wishes" }, `🙏 ${m.wishes} kaipaa kahvia`) : null)),
    h("button", { class: "wish", onclick: () => doWish(id).then(() => route()) }, "Olispa kahvia! ☕"),
    h("h2", {}, "Päivitä tilanne"),
    h("div", { class: "grid" }, buttons),
    cupsBox,
    h("h2", {}, "Ilmoitukset"),
    pushHint() || "",
    followBtn,
    h("h2", {}, "Muuta"),
    h("div", { class: "row" },
      h("button", { class: "secondary", onclick: () => go(`/m/${id}/qr`) }, "🔳 Näytä QR-koodi"),
      h("button", {
        class: "secondary",
        onclick: async () => {
          if (followed().includes(id)) { try { await unfollow(id); } catch {} }
          removeMine(id);
          go("/");
        },
      }, "🗑️ Poista listalta")),
  );
}

// ---------- reititys ----------

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

async function route() {
  const app = document.getElementById("app");
  app.replaceChildren();
  const p = location.pathname;
  let match;
  try {
    if ((match = p.match(new RegExp(`^/m/(${UUID})/qr$`)))) await viewQr(app, match[1]);
    else if ((match = p.match(new RegExp(`^/m/(${UUID})$`)))) await viewMachine(app, match[1]);
    else if (p === "/new") viewNew(app);
    else await viewHome(app);
  } catch (e) {
    app.append(h("h1", {}, "Hups!"), h("p", {}, e.message));
  }
}

document.addEventListener("click", (ev) => {
  const a = ev.target.closest("a[href^='/']");
  if (a && !ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); go(a.getAttribute("href")); }
});
window.addEventListener("popstate", route);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();

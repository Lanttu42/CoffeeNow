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
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

// ---------- kirjautunut käyttäjä ----------

let ME = { user: null, orgs: [] };

async function loadMe() {
  try { ME = await api("/api/auth/me"); } catch { ME = { user: null, orgs: [] }; }
  const link = document.getElementById("account-link");
  link.textContent = ME.user ? `👤 ${ME.user.username}` : "👤 Kirjaudu";
}

const managedOrgs = () => ME.orgs.filter((o) => o.is_manager);

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
      const p = card.querySelector("p");
      if (e.status === 401) {
        p.replaceChildren("🔒 Yksityinen keitin – ", h("a", { href: "/account" }, "kirjaudu sisään"));
      } else if (e.status === 403) {
        p.textContent = "🔒 Sinulla ei ole enää pääsyä tähän keittimeen.";
      } else if (e.status === 404) {
        p.replaceChildren("Keitin on poistettu. ",
          h("button", { class: "secondary", onclick: () => { removeMine(m.id); route(); } }, "Poista listalta"));
      } else {
        p.textContent = e.message;
      }
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

  // Managerille: valinta julkinen / oma organisaatio
  const wanted = new URLSearchParams(location.search).get("org");
  const orgSelect = managedOrgs().length ? h("select", {},
    h("option", { value: "" }, "🌍 Julkinen (kuka tahansa QR:n skannaava)"),
    managedOrgs().map((o) => h("option", { value: o.id, selected: o.id === wanted }, `🔒 ${o.name}`))) : null;

  const form = h("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      const name = input.value.trim();
      if (!name) return;
      try {
        const body = { name };
        if (orgSelect && orgSelect.value) body.org_id = orgSelect.value;
        const m = await api("/api/machines", { method: "POST", body });
        addMine(m);
        go(`/m/${m.id}/qr`);
      } catch (e) { toast(e.message); }
    },
  },
    h("label", {}, "Kahvinkeittimen nimi"), input,
    orgSelect ? [h("label", {}, "Kuka näkee keittimen?"), orgSelect] : null,
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
  catch (e) {
    // Yksityinen keitin: nimeä tai organisaatiota ei näytetä ennen kuin oikeus on tarkistettu
    if (e.status === 401) {
      app.append(
        h("h1", {}, "🔒 Yksityinen kahvinkeitin"),
        h("p", {}, "Tämä kahvinkeitin kuuluu organisaatiolle. Kirjaudu sisään, niin tarkistetaan kuulutko siihen."),
        authForms(() => route()));
    } else if (e.status === 403) {
      app.append(
        h("h1", {}, "🔒 Ei pääsyä"),
        h("p", {}, "Et kuulu tämän kahvinkeittimen organisaatioon. Jos sait kutsukoodin, liity tästä:"),
        joinForm(() => route()));
    } else {
      app.append(h("h1", {}, "Hups!"), h("p", {}, e.message));
    }
    return;
  }

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
    m.org ? h("p", {}, h("a", { class: "badge", href: `/org/${m.org.id}` }, `🔒 ${m.org.name}`)) : "",
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

// ---------- tunnukset ----------

function authForms(onSuccess) {
  const wrap = h("div");
  const show = (mode) => {
    wrap.replaceChildren(
      h("div", { class: "tabs" },
        h("button", { class: mode === "login" ? "active" : "", onclick: () => show("login") }, "Kirjaudu"),
        h("button", { class: mode === "register" ? "active" : "", onclick: () => show("register") }, "Luo tunnus")),
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
        toast("Tervetuloa takaisin! ☕");
        onSuccess();
      } catch (e) { toast(e.message); }
    },
  },
    h("label", {}, "Käyttäjätunnus"), user,
    h("label", {}, "Salasana"), pass,
    h("button", { type: "submit" }, "Kirjaudu sisään"));
}

function registerForm(onSuccess) {
  const user = h("input", { type: "text", autocomplete: "username", autocapitalize: "none", minlength: "3", maxlength: "32", required: true });
  const pass = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  const pass2 = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  return h("form", {
    class: "card",
    onsubmit: async (ev) => {
      ev.preventDefault();
      if (pass.value !== pass2.value) { toast("Salasanat eivät täsmää"); return; }
      try {
        await api("/api/auth/register", {
          method: "POST", body: { username: user.value.trim(), password: pass.value, password2: pass2.value },
        });
        await loadMe();
        toast("Tunnus luotu! ☕");
        onSuccess();
      } catch (e) { toast(e.message); }
    },
  },
    h("div", { class: "hint" },
      h("b", {}, "🔒 Järjestelmä on anonyymi. "),
      "Sähköpostia ei kysytä, joten salasanan palautusta (\"Unohdin salasanan\") ei ole. ",
      "Jos salasana unohtuu, tunnus on menetetty. Tallenna salasana esim. salasanamanageriin."),
    h("label", {}, "Käyttäjätunnus (3–32 merkkiä)"), user,
    h("label", {}, "Salasana (vähintään 10 merkkiä)"), pass,
    h("label", {}, "Salasana uudelleen"), pass2,
    h("button", { type: "submit" }, "Luo tunnus"));
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
        toast(`Liityit: ${org.name} 🎉`);
        onSuccess(org);
      } catch (e) { toast(e.message); }
    },
  },
    h("label", {}, "Kutsukoodi"), code,
    h("button", { type: "submit" }, "Liity organisaatioon"));
}

async function viewAccount(app) {
  if (!ME.user) {
    app.append(
      h("h1", {}, "Tunnus"),
      h("p", { class: "muted" },
        "Perusominaisuudet toimivat ilman tunnusta. Tunnuksella voit luoda organisaation, jonka kahvinkeittimet näkyvät vain jäsenille."),
      authForms(() => route()));
    if (isIOS() && !isStandalone()) {
      app.append(h("div", { class: "hint" },
        "📲 Huom: iPhonen kotivalikkoon lisätty sovellus pitää kirjautumisen erillään Safarista. Kirjaudu sisään vielä kerran sovelluksesta."));
    }
    return;
  }

  app.append(h("h1", {}, `👤 ${ME.user.username}`));

  app.append(h("h2", {}, "Organisaatiot"));
  if (!ME.orgs.length) app.append(h("p", { class: "muted" }, "Et kuulu vielä mihinkään organisaatioon."));
  for (const o of ME.orgs) {
    app.append(h("div", { class: "card" },
      h("div", { class: "row" },
        h("a", { class: "title", href: `/org/${o.id}` }, `🔒 ${o.name}`),
        h("span", { class: "badge" }, o.is_founder ? "Perustaja" : o.is_manager ? "Manageri" : "Jäsen"))));
  }

  app.append(h("h2", {}, "Liity organisaatioon"), joinForm((org) => go(`/org/${org.id}`)));

  const name = h("input", { type: "text", maxlength: "60", placeholder: "esim. Kofeiinikopla", required: true });
  const auto = h("input", { type: "checkbox" });
  app.append(
    h("h2", {}, "Luo organisaatio"),
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
        "💡 Älä käytä yrityksesi oikeaa nimeä. Keksi mieluummin kiva yhteinen lempinimi, esim. \"Kofeiinikopla\" tai \"3. kerroksen pannuporukka\"."),
      h("label", {}, "Organisaation lempinimi"), name,
      h("label", { class: "check" }, auto, " Kaikki liittyjät ovat automaattisesti managereita"),
      h("button", { type: "submit" }, "Luo organisaatio")));

  // Salasanan vaihto ja tilin poisto
  const oldPw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const newPw = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  const newPw2 = h("input", { type: "password", autocomplete: "new-password", minlength: "10", required: true });
  const delPw = h("input", { type: "password", autocomplete: "current-password", required: true });

  app.append(
    h("h2", {}, "Tili"),
    h("button", {
      class: "secondary",
      onclick: async () => { await api("/api/auth/logout", { method: "POST" }); await loadMe(); toast("Kirjauduit ulos 👋"); go("/"); },
    }, "🚪 Kirjaudu ulos"),
    h("details", { class: "card" },
      h("summary", {}, "🔑 Vaihda salasana"),
      h("form", {
        onsubmit: async (ev) => {
          ev.preventDefault();
          try {
            await api("/api/auth/password", {
              method: "POST", body: { old_password: oldPw.value, new_password: newPw.value, new_password2: newPw2.value },
            });
            toast("Salasana vaihdettu 🔑");
            route();
          } catch (e) { toast(e.message); }
        },
      },
        h("label", {}, "Nykyinen salasana"), oldPw,
        h("label", {}, "Uusi salasana"), newPw,
        h("label", {}, "Uusi salasana uudelleen"), newPw2,
        h("button", { type: "submit" }, "Vaihda salasana"))),
    h("details", { class: "card danger" },
      h("summary", {}, "🗑️ Poista tunnus"),
      h("p", { class: "muted" }, "Tunnus, jäsenyydet ja ilmoitustilaukset poistetaan pysyvästi. Perustamasi organisaatiot pitää poistaa ensin."),
      h("form", {
        onsubmit: async (ev) => {
          ev.preventDefault();
          try {
            await api("/api/auth/delete", { method: "POST", body: { password: delPw.value } });
            await loadMe();
            toast("Tunnus poistettu");
            go("/");
          } catch (e) { toast(e.message); }
        },
      },
        h("label", {}, "Vahvista salasanalla"), delPw,
        h("button", { type: "submit", class: "danger" }, "Poista tunnus pysyvästi"))),
  );
}

// ---------- organisaatio ----------

async function viewOrg(app, id) {
  if (!ME.user) { go("/account"); return; }
  let o;
  try { o = await api(`/api/orgs/${id}`); }
  catch (e) { app.append(h("h1", {}, "Hups!"), h("p", {}, e.message)); return; }

  const act = (fn, okText) => async () => {
    try { await fn(); if (okText) toast(okText); await loadMe(); route(); } catch (e) { toast(e.message); }
  };
  const post = (path, body) => api(`/api/orgs/${id}${path}`, { method: "POST", body });

  // Kutsukoodi
  app.append(
    h("h1", {}, `🔒 ${o.name}`),
    h("div", { class: "card" },
      h("div", { class: "muted" }, "Kutsukoodi – jaa työkavereille:"),
      h("div", { class: "invite" }, o.invite_code),
      h("div", { class: "row" },
        h("button", {
          class: "secondary",
          onclick: async () => {
            try { await navigator.clipboard.writeText(o.invite_code); toast("Kopioitu 📋"); } catch { toast(o.invite_code); }
          },
        }, "📋 Kopioi"),
        o.is_manager ? h("button", {
          class: "secondary", onclick: act(() => post("/invite/rotate"), "Uusi kutsukoodi luotu. Vanha ei enää toimi."),
        }, "🔄 Vaihda koodi") : null)),
  );

  // Keittimet
  app.append(h("h2", {}, "Kahvinkeittimet"));
  if (!o.machines.length) app.append(h("p", { class: "muted" }, "Ei vielä keittimiä."));
  for (const m of o.machines) {
    app.append(h("div", { class: "card" },
      h("div", { class: "row" },
        h("a", { class: "title", href: `/m/${m.id}` }, m.name),
        o.is_manager ? h("button", {
          class: "secondary small",
          onclick: act(() => post(`/machines/${m.id}/unlink`), "Keitin irrotettu – se on nyt julkinen"),
        }, "Irrota") : null),
      h("div", {}, statusText(m))));
  }

  if (o.is_manager) {
    const linked = new Set(o.machines.map((m) => m.id));
    const candidates = myMachines().filter((m) => !linked.has(m.id));
    app.append(
      h("button", { onclick: () => go(`/new?org=${id}`) }, "➕ Uusi keitin tähän organisaatioon"),
      candidates.length ? h("details", { class: "card" },
        h("summary", {}, "🔗 Liitä olemassa oleva keitin"),
        h("p", { class: "muted" }, "Liitetty keitin muuttuu yksityiseksi: sen QR-koodi toimii vain jäsenille. Ulkopuolisten ilmoitustilaukset poistetaan."),
        candidates.map((m) => h("div", { class: "row line" },
          h("span", {}, m.name),
          h("button", {
            class: "secondary small",
            onclick: act(() => post("/machines", { machine_id: m.id }), "Keitin liitetty 🔒"),
          }, "Liitä")))) : "",
    );
  }

  // Jäsenet
  app.append(h("h2", {}, `Jäsenet (${o.members.length})`));
  const list = h("div", { class: "card" });
  for (const mem of o.members) {
    const role = mem.is_founder ? "Perustaja" : mem.is_manager ? "Manageri" : "Jäsen";
    const controls = o.is_founder && !mem.is_founder ? [
      h("button", {
        class: "secondary small",
        onclick: act(() => post(`/members/${mem.id}/manager`, { is_manager: !mem.is_manager })),
      }, mem.is_manager ? "Poista manageri" : "Tee manageriksi"),
      h("button", { class: "secondary small", onclick: act(() => post(`/members/${mem.id}/remove`), "Jäsen poistettu") }, "Poista"),
    ] : [];
    list.append(h("div", { class: "row line" },
      h("span", {}, mem.username, " ", h("span", { class: "badge" }, role)),
      h("span", { class: "row" }, controls)));
  }
  app.append(list);

  // Asetukset & vaara-alue
  if (o.is_founder) {
    const auto = h("input", { type: "checkbox", checked: o.auto_manager || null,
      onchange: act(() => post("/settings", { auto_manager: auto.checked }), "Asetus tallennettu") });
    const confirm = h("input", { type: "text", placeholder: o.name, required: true });
    app.append(
      h("h2", {}, "Asetukset"),
      h("label", { class: "check card" }, auto, " Kaikki uudet liittyjät ovat automaattisesti managereita"),
      h("details", { class: "card danger" },
        h("summary", {}, "🗑️ Poista organisaatio"),
        h("p", { class: "muted" }, "Organisaatio, sen jäsenyydet ja KAIKKI sen kahvinkeittimet poistetaan pysyvästi. Tulostetut QR-koodit lakkaavat toimimasta. Irrota keittimet ensin, jos haluat säilyttää ne julkisina."),
        h("form", {
          onsubmit: async (ev) => {
            ev.preventDefault();
            try {
              await post("/delete", { confirm_name: confirm.value });
              await loadMe();
              toast("Organisaatio poistettu");
              go("/account");
            } catch (e) { toast(e.message); }
          },
        },
          h("label", {}, "Kirjoita organisaation nimi vahvistukseksi"), confirm,
          h("button", { type: "submit", class: "danger" }, "Poista organisaatio pysyvästi"))),
    );
  } else {
    app.append(
      h("h2", {}, "Muuta"),
      h("button", {
        class: "secondary",
        onclick: async () => {
          try { await post("/leave"); await loadMe(); toast("Erosit organisaatiosta"); go("/account"); }
          catch (e) { toast(e.message); }
        },
      }, "🚪 Eroa organisaatiosta"));
  }
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
    else if ((match = p.match(new RegExp(`^/org/(${UUID})$`)))) await viewOrg(app, match[1]);
    else if (p === "/account") await viewAccount(app);
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
loadMe().then(route);

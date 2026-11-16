# COFFEE!! ☕

Yhteisöllinen kahvinkeitinseuranta. QR-koodi keittimen kylkeen, ja kuka tahansa voi kertoa muille, onko kahvia.

- Ei rekisteröitymistä, ei kirjautumista, ei organisaatioita
- Kahvinkeitin tunnistetaan satunnaisella UUID:llä (QR-koodi)
- Push-ilmoitukset seuraajille (Web Push, PWA): Android suoraan selaimessa, iPhone kun sovellus on lisätty kotivalikkoon
- **Luottamusmalli on analoginen:** ihmisCPU hoitaa valvonnan. Väärä "Kahvia on!" paljastuu kymmenen askeleen päässä. 😉

## Ominaisuudet

| Toiminto | Kuvaus |
|---|---|
| Lisää uusi kahvinkeitin | Nimi → UUID → tulostettava QR-koodi |
| Ensimmäinen skannaus | "Lisätäänkö tämä kahvinkeitin omiin kahvinkeittimiisi?" |
| Seuraavat skannaukset | Tilan päivitys suoraan |
| Tilat | ☕ Kahvia on · ⏳ Kahvia tulossa (kupit + aika) · 🫗 Vähissä · 🚫 Loppu · ⚠️ Tarvikkeet vähissä · 🆘 Tarvikkeet loppu |
| Olispa kahvia! | Kahvitoive, näkyy laskurina ja menee seuraajille ilmoituksena. Nollautuu kun kahvia on/tulossa, vanhenee 2 h:ssa |
| Seuraa | Push-ilmoitukset keittimen tilamuutoksista |

Omat keittimet ja anonyymi laite-ID tallennetaan vain puhelimen selaimeen (localStorage).

## Rakenne

```
app/
  main.py        FastAPI: API + SPA:n tarjoilu
  db.py          SQLite (keittimet, toiveet, push-tilaukset)
  push.py        Web Push -lähetys (pywebpush), kuolleiden tilausten siivous
  static/        PWA: index.html, app.js, sw.js, manifest.json, style.css, ikonit
deploy/
  Caddyfile      HTTPS + tietoturvaotsakkeet (CSP ym.)
  coffeenow.service  systemd-palvelu kovennuksilla
scripts/
  gen_vapid.py   VAPID-avainparin luonti
```

## Kehitys paikallisesti

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# → http://127.0.0.1:8000
```

Ilman VAPID-avaimia kaikki muu toimii, ilmoitukset vain kirjataan lokiin. Service worker toimii `localhost`issa myös ilman HTTPS:ää.

## Asennus Hetzneriin (Ubuntu/Debian)

Edellytys: domain (esim. `coffee.omadomain.fi`), jonka A-tietue osoittaa palvelimeen, ja portit 80/443 auki.

```bash
# 1. Oma käyttäjä palvelulle (ei rootia!)
sudo useradd --system --home /opt/coffeenow --shell /usr/sbin/nologin coffeenow
sudo git clone https://github.com/Lanttu42/CoffeeNow.git /opt/coffeenow
sudo mkdir -p /opt/coffeenow/data

# 2. Python-ympäristö
sudo apt install -y python3-venv caddy
sudo python3 -m venv /opt/coffeenow/venv
sudo /opt/coffeenow/venv/bin/pip install -r /opt/coffeenow/requirements.txt

# 3. Salaisuudet (.env on .gitignoressa)
sudo cp /opt/coffeenow/.env.example /opt/coffeenow/.env
sudo /opt/coffeenow/venv/bin/python /opt/coffeenow/scripts/gen_vapid.py | sudo tee -a /opt/coffeenow/.env
sudo nano /opt/coffeenow/.env        # poista tyhjät VAPID-rivit ylhäältä, aseta VAPID_SUBJECT
sudo chown -R coffeenow:coffeenow /opt/coffeenow
sudo chmod 600 /opt/coffeenow/.env

# 4. Palvelu
sudo cp /opt/coffeenow/deploy/coffeenow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coffeenow

# 5. HTTPS (vaihda ensin domain Caddyfileen)
sudo cp /opt/coffeenow/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Päivitys: `cd /opt/coffeenow && sudo -u coffeenow git pull && sudo systemctl restart coffeenow`

> ⚠️ Älä vaihda VAPID-avaimia tuotannossa ilman syytä: kaikki olemassa olevat push-tilaukset lakkaavat toimimasta.

## Tiekartta

- [ ] Rekisteröitymällä yksityiset kahvijärjestelmät (organisaatio/SSO, QR toimii vain oman organisaation jäsenille)
- [ ] Ilmoitusasetukset per tila (esim. vain "Kahvia on!")
- [ ] "Kahvia tulossa" → automaattinen "Kahvia on!" keittoajan jälkeen
- [ ] Tilastot: milloin kahvi loppuu tyypillisesti

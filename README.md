# COFFEE!!

Yhteisöllinen kahvinkeitinseuranta. QR-koodi keittimen kylkeen, ja kuka tahansa voi kertoa muille, onko kahvia.

- Perusversio: ei rekisteröitymistä eikä kirjautumista, pelkkä QR-koodi riittää
- Halutessaan: anonyymi tunnus + organisaatio, jonka keittimet näkyvät vain jäsenille
- Kahvinkeitin tunnistetaan satunnaisella UUID:llä (QR-koodi)
- Push-ilmoitukset seuraajille (Web Push, PWA): Android suoraan selaimessa, iPhone kun sovellus on lisätty kotivalikkoon
- Tiloja ei varmisteta mitenkään: väärä "Kahvia on" paljastuu viimeistään keittimellä.

Tehty Clauden kanssa alle tunnissa harrastusprojektina.

## Ominaisuudet

| Toiminto | Kuvaus |
|---|---|
| Lisää uusi kahvinkeitin | Nimi → UUID → tulostettava QR-koodi |
| Ensimmäinen skannaus | "Lisätäänkö tämä kahvinkeitin omiin kahvinkeittimiisi?" |
| Seuraavat skannaukset | Tilan päivitys suoraan |
| Tilat | Kahvia on · Kahvia tulossa (kupit + aika) · Vähissä · Loppu · Tarvikkeet vähissä · Tarvikkeet loppu |
| Kahvia tulossa | Keittimen sivulta: kahvityyppi + kuppimäärä. Pikana (pyyhkäisy etusivulla): edellisen pannun arvot, merkitään "(ehkä)" |
| Keittoaika | Asetus min/kuppi (oletus 1, 0 = heti valmis). Ajan kuluttua tila vaihtuu itsestään "Kahvia on" ja seuraajat saavat ilmoituksen. Etusivulla prosenttilaskuri, kuplat ja animoitu reuna; "Kahvia on" = kultainen reuna |
| Kahvityypit | Keitinkohtainen lista asetuksissa (oletus: Tavallinen, Tumma paahto, Kofeiiniton) |
| Olispa kahvia! | Kahvitoive, näkyy laskurina ja menee seuraajille ilmoituksena. Nollautuu kun kahvia on/tulossa, vanhenee 2 h:ssa |
| Seuraa | Push-ilmoitukset keittimen tilamuutoksista |

Omat keittimet ja anonyymi laite-ID tallennetaan vain puhelimen selaimeen (localStorage).

## Kielet ja versiot

- Suomi, ruotsi ja englanti. Kieli tunnistetaan selaimesta (englanti, jos ei tunnisteta) ja sen voi vaihtaa
  sivun alalaidasta. Tekstit kirjoitetaan koodiin suomeksi ja käännökset ovat tiedostoissa
  `app/static/i18n.js` (käyttöliittymä) ja `app/i18n.py` (virheet ja push-ilmoitukset, seuraajan omalla kielellä).
- Versionumero on tiedostossa `app/VERSION` ja näkyy alalaidassa. Palvelin laskee lisäksi staattisista tiedostoista
  build-tunnisteen; jos se muuttuu, avoinna oleva sovellus näyttää "Uusi versio on saatavilla" -ilmoituksen.

## Tunnukset ja organisaatiot (valinnainen)

**Tunnus** luodaan sovelluksessa: käyttäjätunnus + salasana kahteen kertaan. Sähköpostia ei kysytä, joten
**salasanan palautusta ei ole** – tästä kerrotaan rekisteröityessä. Salasanan voi vaihtaa ja tunnuksen poistaa.

**Organisaatio** on yksityinen keitinporukka. Nimeksi suositellaan lempinimeä, ei yrityksen oikeaa nimeä.

| Rooli | Oikeudet |
|---|---|
| Perustaja | Kaikki ylläpitäjän oikeudet + nimeää ylläpitäjät, poistaa jäseniä, asetus "kaikki liittyjät ylläpitäjiä", poistaa organisaation (nimi vahvistukseksi) |
| Ylläpitäjä | Liittää keittimiä organisaatioon / irrottaa niitä, luo keittimiä suoraan organisaatioon, vaihtaa kutsukoodin |
| Jäsen | Näkee ja päivittää organisaation keittimiä, seuraa niitä, näkee kutsukoodin, voi erota |

- Liittyminen kutsukoodilla `XXX-XXX` (ei sekoittuvia merkkejä 0/O, 1/I/L)
- Yksityisen keittimen QR-linkki on sama `/m/<uuid>` – se **ei paljasta keittimen eikä organisaation nimeä**.
  Kirjautumaton saa kirjautumispyynnön, ulkopuolinen "Ei pääsyä" + kutsukoodikentän.
- Julkisen keittimen voi liittää organisaatioon vain sen luoja (sama tunnus tai sama laite), jotta kukaan ei voi
  kaapata toimiston yhteistä keitintä omaan porukkaansa pelkän QR-koodin perusteella.
- Kun keitin liitetään organisaatioon, ulkopuolisten ilmoitustilaukset ja toiveet poistetaan.
  Kun jäsen eroaa tai poistetaan, hänen tilauksensa organisaation keittimiin poistetaan.
- Organisaation poisto poistaa myös sen keittimet (muuten ne muuttuisivat huomaamatta julkisiksi).

### Tietoturva

- Salasanat: `scrypt` (Pythonin vakiokirjasto), suola per käyttäjä, vakioaikainen vertailu
- Sessio: satunnainen token HttpOnly + Secure + SameSite=Lax -evästeessä, kantaan tallennetaan vain SHA-256-hash, 30 pv
- Brute force -suojat: kirjautuminen 10 virhettä / tunnus / 10 min, kutsukoodit 10 väärää / käyttäjä / 10 min
  (IP-kohtaiset rajat väljempiä, koska toimistossa kaikki voivat näkyä samana IP:nä)
- Kaikki käyttäjän syöte renderöidään `textContent`ina (ei XSS:ää), CSP estää inline-skriptit
- Nimistä poistetaan ohjaus- ja muotoilumerkit (rivinvaihdot, RTL-ohitus, näkymättömät merkit)
- Push-tilauksen osoitteeksi kelpaavat vain selainten push-palvelut (Google, Mozilla, Apple, Microsoft), jotta
  palvelinta ei voi käyttää lähettämään pyyntöjä mihin tahansa
- IP-kohtaiset rajat keittimien ja organisaatioiden luonnille sekä ilmoitustilauksille
- iPhone: kotivalikkoon lisätty sovellus pitää evästeet erillään Safarista → kirjaudu kerran sovelluksessa

## Rakenne

```
app/
  main.py        FastAPI: keittimet + SPA:n tarjoilu
  auth.py        Tunnukset: rekisteröinti, kirjautuminen, salasanan vaihto, tilin poisto
  orgs.py        Organisaatiot, roolit, kutsukoodit, keittimien liitos
  security.py    scrypt, sessiot, rate limit, kutsukoodit
  db.py          SQLite + automaattinen päivitys vanhasta kannasta
  push.py        Web Push -lähetys (pywebpush), kuolleiden tilausten siivous
  static/        PWA: index.html, app.js, sw.js, manifest.json, style.css, ikonit
deploy/
  Caddyfile      HTTPS + tietoturvaotsakkeet (CSP ym.)
  coffeenow.service  systemd-palvelu kovennuksilla
scripts/
  gen_vapid.py   VAPID-avainparin luonti
tests/
  test_api.py    API- ja oikeustestit (106 kpl), käynnistää oman palvelimen
  ui_flow.py     Käyttöliittymän läpiklikkaus (Playwright)
```

Testit: `python3 tests/test_api.py`

## Kehitys paikallisesti

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# → http://127.0.0.1:8000
```

Ilman VAPID-avaimia kaikki muu toimii, ilmoitukset vain kirjataan lokiin. Service worker toimii `localhost`issa myös ilman HTTPS:ää. Kirjautuminen paikallisesti ilman HTTPS:ää vaatii `COOKIE_SECURE=0` (älä koskaan tuotannossa).

## Asennus Dockerilla (Hetzner, Leafyn nginxin takana)

Tuotanto osoitteessa `lastdrop.carrata.fi` pyörii kontissa ilman julkisia portteja. HTTPS:n ja otsakkeet hoitaa
Leafyn nginx (`~/servers/leafy`), joka välittää `leafy_default`-verkossa konttiin `coffeenow:8000`.

```bash
cd ~/servers/coffeenow
python3 scripts/gen_vapid.py > .env && echo "VAPID_SUBJECT=https://lastdrop.carrata.fi" >> .env && chmod 600 .env
mkdir -p data && docker compose up -d --build
sh deploy/lastdrop-tls.sh     # varmenne Leafyn certbotilla + deploy/nginx-lastdrop.conf käyttöön
```

Päivitys: `git pull && docker compose up -d --build`. Data: `data/coffeenow.db`.

## Asennus ilman Dockeria (Ubuntu/Debian, Caddy + systemd)

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

- [x] Tunnukset ja yksityiset organisaatiot kutsukoodilla
- [ ] Omien keittimien lista synkronoituu tunnukseen (nyt laitekohtainen)
- [ ] Ilmoitusasetukset per tila (esim. vain "Kahvia on!")
- [ ] "Kahvia tulossa" → automaattinen "Kahvia on!" keittoajan jälkeen
- [ ] Tilastot: milloin kahvi loppuu tyypillisesti

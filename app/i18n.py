"""Kielet: suomi, ruotsi, englanti. Englanti, jos kieltä ei tunnisteta.

Palvelimen viestit kirjoitetaan koodiin suomeksi; virheviestit käännetään vastausta lähetettäessä
(ks. main.py: exception handler). Kieli tulee X-Lang-otsakkeesta (käyttäjän valinta sivulla) tai
selaimen Accept-Language-otsakkeesta.
"""
from fastapi import Request

LANGS = ("fi", "sv", "en")
DEFAULT = "en"

# Suomenkielinen viesti -> {"en": ..., "sv": ...}
MESSAGES: dict[str, dict[str, str]] = {
    # tilat (push-ilmoitukset)
    "Kahvia on!": {"en": "Coffee's ready!", "sv": "Det finns kaffe!"},
    "Kahvia tulossa": {"en": "Coffee is brewing", "sv": "Kaffe på gång"},
    "Kahvi vähissä": {"en": "Coffee is running low", "sv": "Kaffet håller på att ta slut"},
    "Kahvi loppu": {"en": "Coffee's gone", "sv": "Kaffet är slut"},
    "Kahvitarvikkeet vähissä": {"en": "Coffee supplies running low", "sv": "Kaffetillbehören håller på att ta slut"},
    "Kahvitarvikkeet loppu": {"en": "Coffee supplies are out", "sv": "Kaffetillbehören är slut"},
    "Joku toivoo kahvia.": {"en": "Someone is wishing for coffee.", "sv": "Någon önskar sig kaffe."},
    "{n} ihmistä toivoo kahvia.": {"en": "{n} people are wishing for coffee.", "sv": "{n} personer önskar sig kaffe."},
    " ({n} kuppia)": {"en": " ({n} cups)", "sv": " ({n} koppar)"},
    # virheet
    "Salasanan pitää olla vähintään 10 merkkiä": {"en": "The password must be at least 10 characters", "sv": "Lösenordet måste vara minst 10 tecken"},
    "Käyttäjätunnus: 3–32 merkkiä, kirjaimia, numeroita tai _ . -": {"en": "Username: 3–32 characters, letters, numbers or _ . -", "sv": "Användarnamn: 3–32 tecken, bokstäver, siffror eller _ . -"},
    "Kirjoita organisaation nimi täsmälleen vahvistukseksi": {"en": "Type the organisation's name exactly to confirm", "sv": "Skriv organisationens namn exakt för att bekräfta"},
    "Perustajaa ei voi poistaa. Poista koko organisaatio.": {"en": "The founder can't be removed. Delete the whole organisation instead.", "sv": "Grundaren kan inte tas bort. Ta bort hela organisationen i stället."},
    "Perustaja ei voi erota. Poista organisaatio, jos haluat lopettaa.": {"en": "The founder can't leave. Delete the organisation if you want to stop.", "sv": "Grundaren kan inte lämna. Ta bort organisationen om du vill sluta."},
    "Perustaja on aina ylläpitäjä": {"en": "The founder is always an admin", "sv": "Grundaren är alltid administratör"},
    "Salasanat eivät täsmää": {"en": "The passwords don't match", "sv": "Lösenorden matchar inte"},
    "Nykyinen salasana on väärä": {"en": "The current password is wrong", "sv": "Nuvarande lösenord är fel"},
    "Väärä käyttäjätunnus tai salasana": {"en": "Wrong username or password", "sv": "Fel användarnamn eller lösenord"},
    "Väärä salasana": {"en": "Wrong password", "sv": "Fel lösenord"},
    "Vain keittimen luoja voi liittää sen organisaatioon": {"en": "Only the person who created the coffee maker can add it to an organisation", "sv": "Bara den som skapade kaffebryggaren kan lägga till den i en organisation"},
    "Vain perustaja voi tehdä tämän": {"en": "Only the founder can do this", "sv": "Bara grundaren kan göra detta"},
    "Vain ylläpitäjä voi tehdä tämän": {"en": "Only an admin can do this", "sv": "Bara en administratör kan göra detta"},
    "Ei löydy": {"en": "Not found", "sv": "Hittades inte"},
    "Jäsentä ei löydy": {"en": "Member not found", "sv": "Medlemmen hittades inte"},
    "Kahvinkeitintä ei löydy": {"en": "Coffee maker not found", "sv": "Kaffebryggaren hittades inte"},
    "Keitin ei kuulu tähän organisaatioon": {"en": "The coffee maker doesn't belong to this organisation", "sv": "Kaffebryggaren hör inte till den här organisationen"},
    "Kutsukoodia ei löydy": {"en": "Invite code not found", "sv": "Inbjudningskoden hittades inte"},
    "Organisaatiota ei löydy": {"en": "Organisation not found", "sv": "Organisationen hittades inte"},
    "Käyttäjätunnus on jo käytössä": {"en": "That username is already taken", "sv": "Användarnamnet är redan upptaget"},
    "Keitin kuuluu jo toiseen organisaatioon": {"en": "The coffee maker already belongs to another organisation", "sv": "Kaffebryggaren hör redan till en annan organisation"},
    "Poista ensin perustamasi organisaatiot": {"en": "Delete the organisations you founded first", "sv": "Ta först bort organisationerna du har grundat"},
    "Hetkinen, kahvi ei keity nopeammin painamalla.": {"en": "Hang on, pressing faster won't make the coffee brew faster.", "sv": "Vänta lite, kaffet bryggs inte snabbare om du trycker mer."},
    "Liian monta uutta organisaatiota. Yritä myöhemmin.": {"en": "Too many new organisations. Try again later.", "sv": "För många nya organisationer. Försök igen senare."},
    "Liikaa kirjautumisyrityksiä. Odota 10 minuuttia.": {"en": "Too many login attempts. Wait 10 minutes.", "sv": "För många inloggningsförsök. Vänta 10 minuter."},
    "Liikaa vääriä kutsukoodeja. Odota 10 minuuttia.": {"en": "Too many wrong invite codes. Wait 10 minutes.", "sv": "För många felaktiga inbjudningskoder. Vänta 10 minuter."},
    "Liian monta uutta keitintä tästä osoitteesta. Yritä myöhemmin.": {"en": "Too many new coffee makers from this address. Try again later.", "sv": "För många nya kaffebryggare från den här adressen. Försök igen senare."},
    "Liikaa uusia tunnuksia tästä osoitteesta. Yritä myöhemmin.": {"en": "Too many new accounts from this address. Try again later.", "sv": "För många nya konton från den här adressen. Försök igen senare."},
    "Liian monta tilausta tästä osoitteesta. Yritä myöhemmin.": {"en": "Too many subscriptions from this address. Try again later.", "sv": "För många prenumerationer från den här adressen. Försök igen senare."},
    "Vain keittimen luoja tai ylläpitäjä voi muuttaa asetuksia": {"en": "Only the person who created the coffee maker or an admin can change its settings", "sv": "Bara den som skapade kaffebryggaren eller en administratör kan ändra inställningarna"},
    "Ilmoitustilausta ei löydy": {"en": "Notification subscription not found", "sv": "Notisprenumerationen hittades inte"},
    "Liikaa yrityksiä, odota hetki.": {"en": "Too many attempts, wait a moment.", "sv": "För många försök, vänta en stund."},
}


def pick(value: str | None) -> str | None:
    """'sv-FI' -> 'sv'; tuntematon -> None."""
    if not value:
        return None
    code = value.strip().lower()[:2]
    return code if code in LANGS else None


def lang_of(request: Request) -> str:
    chosen = pick(request.headers.get("x-lang"))
    if chosen:
        return chosen
    # Accept-Language: "sv-FI,sv;q=0.9,en;q=0.8" -> ensimmäinen tuettu painojärjestyksessä
    langs = []
    for i, part in enumerate(request.headers.get("accept-language", "").split(",")):
        code, _, q = part.strip().partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        langs.append((-weight, i, code))
    for _, _, code in sorted(langs):
        found = pick(code)
        if found:
            return found
    return DEFAULT


def tr(message: str, lang: str, **params) -> str:
    text = message if lang == "fi" else MESSAGES.get(message, {}).get(lang, message)
    return text.format(**params) if params else text

"""Käyttöliittymän läpiklikkaus (Playwright + Chrome). Palvelin pitää olla käynnissä osoitteessa BASE.

Ajo: python3 tests/ui_flow.py http://127.0.0.1:8767 /tmp/shots
"""
import re
import sys

from playwright.sync_api import sync_playwright

BASE, OUT = sys.argv[1], sys.argv[2]
PW = "kahvi-on-hyvaa-123"

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    errors = []

    def new_page():
        page = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and "401" not in m.text and "403" not in m.text and errors.append(m.text))
        return page

    def shot(page, name, full=False):
        page.wait_for_timeout(400)
        page.screenshot(path=f"{OUT}/{name}.png", full_page=full)

    # --- Alice: tunnus + organisaatio + yksityinen keitin ---
    alice = new_page()
    alice.goto(BASE + "/account")
    alice.click(".tabs >> text=Luo tunnus")
    shot(alice, "a1_register")
    inputs = alice.locator("form input")
    inputs.nth(0).fill("alice")
    inputs.nth(1).fill(PW)
    inputs.nth(2).fill(PW)
    alice.click("text=Luo tunnus >> nth=-1")
    alice.wait_for_selector("text=Luo organisaatio")
    alice.fill("input[placeholder='esim. Kofeiinikopla']", "Kofeiinikopla")
    alice.click("button:has-text('Luo organisaatio')")
    alice.wait_for_selector(".invite")
    code = alice.locator(".invite").inner_text().strip()
    assert re.fullmatch(r"[A-Z0-9]{3}-[A-Z0-9]{3}", code), code
    shot(alice, "a2_org", full=True)

    alice.click("text=Uusi keitin tähän organisaatioon")
    alice.fill("input[type=text]", "3. kerroksen Moccamaster")
    assert alice.locator("select").input_value() != "", "organisaation pitäisi olla valittuna"
    shot(alice, "a3_new_private")
    alice.click("text=Luo kahvinkeitin ja QR-koodi")
    alice.wait_for_selector(".qr img")
    machine_url = alice.url.removesuffix("/qr")

    # --- Bob: skannaa QR:n kirjautumatta ---
    bob = new_page()
    bob.goto(machine_url)
    bob.wait_for_selector("text=Yksityinen kahvinkeitin")
    body = bob.locator("main").inner_text()
    assert "Moccamaster" not in body and "Kofeiini" not in body, "nimi vuoti!"
    shot(bob, "b1_scan_login")

    bob.click(".tabs >> text=Luo tunnus")
    inputs = bob.locator("form input")
    inputs.nth(0).fill("bob")
    inputs.nth(1).fill(PW)
    inputs.nth(2).fill(PW)
    bob.click("text=Luo tunnus >> nth=-1")
    bob.wait_for_selector("text=Ei pääsyä")
    assert "Moccamaster" not in bob.locator("main").inner_text(), "nimi vuoti 403:ssa!"
    bob.fill(".code-input", code.lower())
    shot(bob, "b2_join")
    bob.click("text=Liity organisaatioon")
    bob.wait_for_selector("text=Lisätäänkö")
    bob.click("text=Kyllä, lisää")
    bob.wait_for_selector(".grid")
    shot(bob, "b3_machine")

    # --- Alice: jäsenlista ---
    alice.goto(BASE + "/account")
    alice.click("a.title >> text=Kofeiinikopla")
    alice.wait_for_selector("text=Jäsenet (2)")
    alice.click("text=Tee ylläpitäjäksi")
    alice.wait_for_selector("text=Poista ylläpito-oikeus")
    shot(alice, "a4_members", full=True)

    # --- Bob: etusivu ---
    bob.goto(BASE + "/")
    bob.wait_for_selector(".status-big")
    shot(bob, "b4_home")

    print("Kutsukoodi:", code)
    print("JS-virheet:", errors or "ei yhtään")
    browser.close()
    if errors:
        sys.exit(1)

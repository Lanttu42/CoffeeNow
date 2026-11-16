"""Generoi tilakuvakkeet (SVG) hakemistoon app/static/icons/.

Kaikki kuvakkeet jakavat saman kannun / suodatinlaatikon geometrian, joten ne pysyvät yhtenäisinä.
Ajo: python3 scripts/gen_icons.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"

INK = "#111"
OLIVE = "#6b5d00"
GLASS = "#ececec"
COFFEE = "#3d200c"
BOX_FRONT = "#a0440c"
BOX_SIDE = "#f0a05c"
BOX_TOP = "#7c3612"
FILTER = "#c8fff6"
RED = "#f23c0c"

# ---------- kannu ----------

BODY = ("M46,34 L94,32 L90,50 C104,62 116,72 116,84 C116,94 108,104 102,112 "
        "L40,112 C34,104 26,94 26,84 C26,72 36,62 46,52 Z")


def pot(level: float | None, drops: bool = False) -> str:
    """level = kahvin pinnan y-koordinaatti (None = tyhjä)."""
    coffee = ""
    if level is not None:
        y = level
        coffee = (f'<path clip-path="url(#glass)" fill="{COFFEE}" '
                  f'd="M10,{y} Q40,{y - 5} 71,{y} T132,{y} L132,130 L10,130 Z"/>')
    drop_svg = ""
    if drops:
        drop = "c-4,6 -5,10 -5,12 a5,5 0 0 0 10,0 c0,-2 -1,-6 -5,-12 z"
        drop_svg = (f'<path fill="{COFFEE}" stroke="{INK}" stroke-width="2.5" d="M70,40 {drop}"/>'
                    f'<path fill="{COFFEE}" stroke="{INK}" stroke-width="2.5" d="M70,60 {drop}"/>')
    handle = "M42,38 C30,41 17,51 13,63 C9,75 11,84 16,91"
    return f"""
  <clipPath id="glass"><path d="{BODY}"/></clipPath>
  <path d="{handle}" fill="none" stroke="{INK}" stroke-width="13" stroke-linecap="round"/>
  <path d="{handle}" fill="none" stroke="{OLIVE}" stroke-width="6.5" stroke-linecap="round"/>
  <path d="{BODY}" fill="{GLASS}"/>
  {coffee}
  {drop_svg}
  <path d="{BODY}" fill="none" stroke="{INK}" stroke-width="4.5" stroke-linejoin="round"/>
  <path d="M38,38 L56,26 L116,24 L109,35 L46,37 Z" fill="{OLIVE}" stroke="{INK}" stroke-width="4" stroke-linejoin="round"/>"""


# ---------- suodatinlaatikko ----------

FILTER_SHAPE = ("M26,86 L40,84 L43,78 L54,82 L66,80 L69,75 L80,80 L90,86 "
                "L72,120 L44,118 Z")


def box() -> str:
    filters = "".join(
        f'<path d="{FILTER_SHAPE}" transform="translate({dx},{dy})" fill="{FILTER}" '
        f'stroke="{INK}" stroke-width="4" stroke-linejoin="round"/>'
        for dx, dy in [(14, -3), (7, -1.5), (0, 0)]
    )
    return f"""
  <path d="M14,30 L30,39 L30,118 L15,108 Z" fill="{BOX_SIDE}" stroke="{INK}" stroke-width="4.5" stroke-linejoin="round"/>
  <path d="M30,39 L80,25 L82,104 L30,118 Z" fill="{BOX_FRONT}" stroke="{INK}" stroke-width="4.5" stroke-linejoin="round"/>
  <path d="M14,30 L62,16 L80,25 L30,39 Z" fill="{BOX_TOP}" stroke="{INK}" stroke-width="4.5" stroke-linejoin="round"/>
  {filters}"""


# ---------- merkit ----------

def bang(x: float = 0, y: float = 0) -> str:
    """Käsin piirretyn näköinen huutomerkki."""
    return (f'<g transform="translate({x},{y})">'
            f'<path d="M116,6 C113,16 108,28 104,38 L98,36 C101,25 105,14 108,3 Z" fill="{INK}" '
            f'stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
            f'<ellipse cx="98" cy="49" rx="6" ry="5.5" fill="{INK}" transform="rotate(-20 98 49)"/></g>')


def cross() -> str:
    return (f'<g fill="none" stroke="{RED}" stroke-width="8" stroke-linecap="round">'
            f'<path d="M92,58 C100,52 112,40 124,26"/>'
            f'<path d="M100,24 C104,34 110,46 114,60"/></g>')


# ---------- kokoaminen ----------

POT_T = 'transform="translate(4,12) scale(0.86)"'


def svg(title: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="{title}">'
            f"<title>{title}</title>{body}\n</svg>\n")


ICONS = {
    "brewing": ("Kahvia tulossa", f"<g {POT_T}>{pot(82, drops=True)}</g>"),
    "ready": ("Kahvia on", f"<g {POT_T}>{pot(58)}</g>"),
    "low": ("Kahvi on vähissä", f"<g {POT_T}>{pot(94)}</g>"),
    "empty": ("Kahvi loppu", f"<g {POT_T}>{pot(None)}</g>{bang(8, 0)}"),
    "supplies_low": ("Kahvitarvikkeet vähissä", f"{box()}{bang(0, 10)}"),
    "supplies_out": ("Kahvitarvikkeet loppu", f"{box()}{cross()}"),
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (title, body) in ICONS.items():
        (OUT / f"{name}.svg").write_text(svg(title, body), encoding="utf-8")
        print("✓", name)

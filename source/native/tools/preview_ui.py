"""Render ShiftDeck, BoostDeck and MHD Maps screenshots on the computer.

No car, SSH or QNX build is needed. The artwork comes from the same generator
code the installer uses, positions come from plugin/*.xml, and the on-screen
logic is a Python copy of shift_deck.cpp / plugin.cpp / mhd_maps.cpp.

Approximations: iDrive draws its own title bar, background, focus ring and the
text of native labels (BoostDeck readings, MHD status) in its own font. Those
are imitated here, so check the real screen while parked.

    python tools/preview_ui.py            (run from source/native)
    python tools/preview_ui.py --open     (also opens the page)
"""
import argparse
import json
import webbrowser
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

import make_bars
import make_drive_apps
import shift_art as art

NATIVE = Path(__file__).resolve().parents[1]
PLUGIN = NATIVE / "plugin"
RPM_STEP, RPM_MAX = 250, 8000


# ---------------------------------------------------------------- helpers
def screen(size, background):
    return Image.new("RGBA", size, background)


def native_font(size):
    """Stand-in for iDrive's own label font."""
    for path in ("C:/Windows/Fonts/segoeui.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def native_text(canvas, x, y, text, size=27, color="#e9eef4"):
    ImageDraw.Draw(canvas).text((x, y), text, font=native_font(size), fill=color)


def boxes(xml, state="100"):
    """{component element: (x, y, w, h)} and {model id: image id} from an app XML."""
    app = ET.parse(PLUGIN / xml).getroot().find("pluginApp")
    images = {int(m.get("id")): int(m.get("imageId")) for m in app.iter("imageIdModel")}
    found = []
    for c in app.find(f"hmiStates/hmiState[@id='{state}']/components"):
        p = {int(q.get("id")): q.get("value") for q in c.iter("property")}
        if 20 in p:
            found.append((c, (int(p[20]), int(p[21]), int(p[9]), int(p[10]))))
    return found, images


def place(canvas, bitmap, box):
    """Draw a bitmap in its XML box. Oversized art is shrunk to fit."""
    x, y, w, h = box
    scale = min(1.0, w / bitmap.width, h / bitmap.height)
    if scale < 1.0:
        bitmap = bitmap.resize((max(1, round(bitmap.width*scale)),
                                max(1, round(bitmap.height*scale))), Image.LANCZOS)
    canvas.alpha_composite(bitmap, (x + (w-bitmap.width)//2, y + (h-bitmap.height)//2))


# ---------------------------------------------------------------- ShiftDeck
def shift_models(rpm, kph, target_index):
    """Mirror ShiftDeck::draw(): which image each model shows."""
    target = art.TARGETS[target_index]
    shift = rpm >= target
    bars = min(32, (rpm + 125) // 250) if rpm > 0 else 0
    models = {54: (art.BAR_WARN if shift else art.BAR_BASE) + bars}
    approach = rpm - (target - 2000)
    models[55] = art.LIGHT_BASE + (13 if shift else approach*12//2000 if approach > 0 else 0)
    models[56] = art.TARGET_BASE + target_index
    models[57] = art.SHIFT_ON if shift else art.SHIFT_OFF
    models[68] = art.MARK_BASE + target_index

    def digits(model, width, text, base, center):
        offset = (width - len(text)) // 2 if center and len(text) < width else 0
        for i in range(width):
            c = text[i - offset] if offset <= i < offset + len(text) else " "
            models[model + i] = base + (11 if c == " " else 10 if c == "-" else int(c))

    shown = -1 if rpm < 0 else (rpm + 5) // 10 * 10
    digits(60, 4, "--" if shown < 0 else str(min(shown, 9999)),
           art.DIGIT_WARN if shift else art.DIGIT_BASE, True)
    speed = kph if art.METRIC_SPEED else (kph*621371 + 500000)//1000000
    digits(70, 3, "--" if kph < 0 else str(speed), art.DIGIT_BASE, False)
    return models


def render_shift(size, background, bitmaps, layout, rpm, target_index):
    found, images = layout
    kph = -1 if rpm < 0 else rpm * 30 // 1000  # rough 3rd-gear road speed
    models = shift_models(rpm, kph, target_index)
    canvas = screen(size, background)
    for c, box in found:
        model = int(c.get("model"))
        place(canvas, bitmaps[models.get(model, images[model])], box)
    return canvas


# ---------------------------------------------------------------- BoostDeck
# Raw collector units: psi, AFR, deg, C, C, bar, bar, C. None = no reading.
# A manual Supra has no automatic-gearbox ECU, so transmission stays blank.
BOOST_STATES = {
    "idle":  (-9.8, 14.7, 12.0, 31.0, None, 52.0, 2.1, 90.0),
    "boost": (17.4, 11.9, 7.5, 43.0, None, 238.0, 4.6, 96.0),
    "nodata": (None,) * 8,
}


def boost_label(i, raw):
    """Mirror BoostDeck::refresh() label text; boost always stays in psi."""
    name, unit, _, _ = make_bars.BARS[i]
    unit = unit.replace("\\260", "\u00b0")
    if raw is None:
        return f"{name}   --"
    shown = raw
    if not make_bars.METRIC:
        shown = raw*1.8 + 32 if i in (3, 4, 7) else raw*14.5037738 if i in (5, 6) else raw
    if i == 0 and abs(shown) < 0.05:
        shown = 0.0
    value = f"{shown:+.1f}" if i == 0 else f"{shown:.0f}" if i == 5 else f"{shown:.1f}"
    return f"{name}   {value}{' ' + unit if unit else ''}"


def boost_image(i, raw, theme):
    if raw is None:
        return make_bars.FIRST
    _, _, lo, hi = make_bars.BARS[i]
    step = int(max(0.0, min(1.0, (raw - lo) / (hi - lo))) * make_bars.STEPS + 0.5)
    if not step:
        return make_bars.FIRST
    palette = len(make_bars.COLORS) + i if theme == len(make_bars.COLORS) else theme
    return make_bars.FIRST + palette*make_bars.STEPS + step


def render_boost(size, background, bitmaps, state, theme):
    canvas = screen(size, background)
    found, _ = boxes("BoostDeck.xml")
    for c, box in found:
        identifier = int(c.get("id"))
        if c.tag == "image":
            i = identifier - 200
            place(canvas, bitmaps[boost_image(i, BOOST_STATES[state][i], theme)], box)
        elif c.tag == "label":
            i = identifier - 150
            native_text(canvas, box[0], box[1] + 2, boost_label(i, BOOST_STATES[state][i]))
    return canvas


# ---------------------------------------------------------------- MHD Maps
MHD_STATES = {"idle": "", "requesting": "Requesting map 2...", "confirmed": "Confirmed: Map 2"}


def render_mhd(size, background, bitmaps, state):
    canvas = screen(size, background)
    found, images = boxes("MhdMaps.xml")
    for c, box in found:
        if c.tag == "image":
            place(canvas, bitmaps[images[int(c.get("model"))]], box)
        elif c.tag == "button" and c.get("id") == "152":
            # Approximate iDrive's controller focus on Map 2.
            x, y, w, h = box
            ImageDraw.Draw(canvas).rounded_rectangle((x-4, y-4, x+w+3, y+h+3), radius=19,
                                                     outline="#d8e6f5", width=3)
        elif c.tag == "label" and MHD_STATES[state]:
            native_text(canvas, box[0], box[1] + 8, MHD_STATES[state])
    return canvas


# ---------------------------------------------------------------- HUD concept
# The HUD is not in this repository yet (README: "Coming Soon"). This is a
# design mock-up in the new style, based on the author's HUD photos: speed
# limit, speed, RPM, boost and AFR. It produces no installable assets.
HUD_THEMES = {"white": ("#f4f7fb", "#9aa6b4"), "orange": ("#ff8a3d", "#c0602a")}
# (km/h, speed limit, rpm, boost psi, AFR). AFR is the collector's
# gasoline-equivalent value (measured lambda x 14.7), as BoostDeck shows it.
HUD_STATES = {"cruise": (72, 50, 2400, -6.5, 14.7), "boost": (96, 80, 5200, 14.8, 11.9),
              "shift": (118, 100, 6650, 17.9, 11.6), "nodata": (None,) * 5}
HUD_LAYOUTS = {"track": "Track bar", "arc": "Sweep arc", "minimal": "Minimal"}
HUD_SHIFT_AT, HUD_REDLINE, HUD_RPM_MAX = 6500, 7000, 8000
HUD_BOOST_MAX = 25.0  # psi at full scale
HUD_RED = "#ff3b47"


def _speed_limit(hd, x, y, limit, r=42):
    hd.ellipse((x-r, y-r, x+r, y+r), fill="#f4f4f4", outline="#e0262d", width=8)
    text = "--" if limit is None else str(limit)
    hd.text((x, y), text, font=art.font(36 if len(text) < 3 else 28), fill="#111", anchor="mm")


def _readout(hd, x, y, text, caption, size, ink, dim, align):
    """Value with a small caption under it; baseline at y."""
    hd.text((x, y), text, font=art.font(size), fill=ink, anchor="ls" if align == "left" else "rs")
    art.label(hd, x, y + 8, caption, 16, color=dim, align=align, tracking=3)


def _engine_text(hd, x, y, psi, afr, ink, dim, align="left"):
    """Boost in psi with AFR stacked below it; boost baseline at y."""
    _readout(hd, x, y, "--" if psi is None else f"{psi:+.1f}", "PSI BOOST", 50, ink, dim, align)
    _readout(hd, x, y + 72, "--" if afr is None else f"{afr:.1f}", "AFR", 40, ink, dim, align)


def _hud_track(hd, cx, cy, kph, limit, rpm, psi, afr, ink, dim, shifting):
    # Segmented RPM bar across the top; redline segments tinted; all red to shift.
    n, x0, x1, top = 24, cx - 300, cx + 300, cy - 118
    pitch = (x1 - x0) / n
    for i in range(n):
        seg_rpm = (i + 1) * HUD_RPM_MAX / n
        a = x0 + i * pitch
        lit = rpm is not None and rpm >= seg_rpm - HUD_RPM_MAX / n / 2
        red = shifting or seg_rpm > HUD_REDLINE
        color = (HUD_RED if red else ink) if lit else ((HUD_RED if red else dim) + "45")
        hd.polygon([(a + 6, top), (a + pitch - 2, top), (a + pitch - 8, top + 22), (a, top + 22)], fill=color)
    hd.text((x0, top + 46), "0", font=art.font(20), fill=dim, anchor="ls")
    hd.text((x1, top + 46), "8", font=art.font(20), fill=HUD_RED, anchor="rs")
    art.label(hd, cx, top + 30, "RPM × 1000", 14, color=dim, align="center", tracking=3)
    hd.text((cx, cy + 50), "--" if kph is None else str(kph), font=art.font(120), fill=ink, anchor="ms")
    art.label(hd, cx, cy + 62, "KM/H", 20, color=dim, align="center", tracking=4)
    _speed_limit(hd, cx - 270, cy + 10, limit)
    _engine_text(hd, cx + 200, cy + 10, psi, afr, ink, dim)


def _hud_arc(hd, cx, cy, kph, limit, rpm, psi, afr, ink, dim, shifting):
    # RPM sweep arc wrapped around the speed, 150 deg -> 390 deg (240 deg sweep).
    r, start, sweep = 150, 150, 240
    box = (cx - r, cy - r + 30, cx + r, cy + r + 30)
    red_from = start + sweep * HUD_REDLINE / HUD_RPM_MAX
    hd.arc(box, start, red_from, fill=dim + "50", width=12)
    hd.arc(box, red_from, start + sweep, fill=HUD_RED + "70", width=12)
    if rpm:
        end = start + sweep * min(1.0, rpm / HUD_RPM_MAX)
        hd.arc(box, start, end, fill=HUD_RED if shifting else ink, width=12)
    hd.text((cx, cy + 60), "--" if kph is None else str(kph), font=art.font(110), fill=ink, anchor="ms")
    art.label(hd, cx, cy + 72, "KM/H", 20, color=dim, align="center", tracking=4)
    rpm_text = "--" if rpm is None else f"{(rpm + 50) // 100 * 100}"
    hd.text((cx, cy + 150), rpm_text, font=art.font(34), fill=HUD_RED if shifting else dim, anchor="ms")
    art.label(hd, cx, cy + 158, "RPM", 14, color=dim, align="center", tracking=3)
    _speed_limit(hd, cx - 290, cy + 30, limit)
    _engine_text(hd, cx + 210, cy + 20, psi, afr, ink, dim)


def _hud_minimal(hd, cx, cy, kph, limit, rpm, psi, afr, ink, dim, shifting):
    # Big speed; a thin RPM line beneath with a bright marker; red brackets to shift.
    hd.text((cx, cy + 40), "--" if kph is None else str(kph), font=art.font(130), fill=ink, anchor="ms")
    art.label(hd, cx, cy + 52, "KM/H", 20, color=dim, align="center", tracking=4)
    x0, x1, y = cx - 200, cx + 200, cy + 100
    red_x = x0 + (x1 - x0) * HUD_REDLINE / HUD_RPM_MAX
    hd.line((x0, y, red_x, y), fill=dim + "60", width=3)
    hd.line((red_x, y, x1, y), fill=HUD_RED + "90", width=3)
    if rpm:
        x = x0 + (x1 - x0) * min(1.0, rpm / HUD_RPM_MAX)
        hd.line((x0, y, x, y), fill=HUD_RED if shifting else ink, width=5)
        hd.rounded_rectangle((x - 3, y - 11, x + 3, y + 11), radius=3, fill=HUD_RED if shifting else ink)
    if shifting:
        for side in (-1, 1):
            bx = cx + side * 150
            hd.rounded_rectangle((bx - 6, cy - 70, bx + 6, cy + 30), radius=4, fill=HUD_RED)
    _speed_limit(hd, cx - 300, cy, limit, r=38)
    _engine_text(hd, cx + 330, cy + 0, psi, afr, ink, dim, align="right")


def render_hud(layout, theme, state):
    from PIL import ImageFilter
    ink, dim = HUD_THEMES[theme]
    kph, limit, rpm, psi, afr = HUD_STATES[state]
    shifting = rpm is not None and rpm >= HUD_SHIFT_AT
    w, h = 1280, 480
    # Night road seen through the windscreen.
    canvas = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(canvas)
    for y in range(h):
        d.line((0, y, w, y), fill=art.mix("#0a0c10", "#2a2b2d", y / h))
    d.line((180, h, 560, 140), fill="#6e6a3a", width=5)
    d.line((1100, h, 720, 140), fill="#3a3b3e", width=4)
    hud, hd = art.art((w, h))
    draw = {"track": _hud_track, "arc": _hud_arc, "minimal": _hud_minimal}[layout]
    draw(hd, w // 2, 230, kph, limit, rpm, psi, afr, ink, dim, shifting)

    # Projected light: soft glow and slight transparency.
    glow = hud.filter(ImageFilter.GaussianBlur(6))
    canvas.alpha_composite(glow)
    hud.putalpha(hud.getchannel("A").point(lambda v: int(v * 0.92)))
    canvas.alpha_composite(hud)
    return canvas


# ---------------------------------------------------------------- page
PAGE = """<!doctype html><meta charset="utf-8"><title>iDrive app preview</title>
<style>
body{background:#0e1116;color:#d6dde6;font:15px system-ui,sans-serif;margin:24px}
nav button{font:inherit;background:#1d2530;color:#d6dde6;border:1px solid #33404f;border-radius:8px;
 padding:8px 16px;margin-right:6px;cursor:pointer}nav button.on{background:#ffb340;color:#151a20;border-color:#ffb340}
section{margin-top:18px}section[hidden]{display:none}label{margin-right:18px}
img{display:block;max-width:100%;border:1px solid #2a3340;border-radius:6px;margin:14px 0}
input[type=range]{width:min(560px,80vw);vertical-align:middle}select{font:inherit}
p.note{color:#8a97a8;max-width:780px}
</style>
<h2>iDrive app preview</h2>
<nav><button data-tab="shift" class="on">ShiftDeck</button><button data-tab="boost">BoostDeck</button>
<button data-tab="mhd">MHD Maps</button><button data-tab="hud">HUD (concept)</button></nav>
<section id="shift">
<label>RPM <input id="rpm" type="range" min="-250" max="RPM_MAX" step="RPM_STEP" value="4500"> <b id="rpmv"></b></label>
<label>Shift light <select id="target"></select></label>
<label><input id="sweep" type="checkbox"> Sweep</label>
<img id="shiftimg" alt="ShiftDeck">
</section>
<section id="boost" hidden>
<label>Reading <select id="bstate"><option value="idle">Idle</option><option value="boost" selected>Under boost</option>
<option value="nodata">No data</option></select></label>
<label>Gauge color <select id="theme"></select></label>
<img id="boostimg" alt="BoostDeck">
<p class="note">Bars are the real artwork. The reading text is drawn by iDrive in its own font, so it is only
imitated here. Transmission shows -- because a manual car has no automatic gearbox ECU.</p>
</section>
<section id="mhd" hidden>
<label>State <select id="mstate"><option value="idle">Idle</option><option value="requesting">Requesting</option>
<option value="confirmed">Confirmed</option></select></label>
<img id="mhdimg" alt="MHD Maps">
<p class="note">The white outline imitates iDrive's controller focus; the status line is iDrive text.</p>
</section>
<section id="hud" hidden>
<label>Layout <select id="hlayout"><option value="track">Track bar</option><option value="arc">Sweep arc</option>
<option value="minimal">Minimal</option></select></label>
<label>Reading <select id="hstate"><option value="cruise">Cruise</option><option value="boost" selected>Under boost</option>
<option value="shift">Shift (6,650 RPM)</option><option value="nodata">No data</option></select></label>
<label>Color <select id="htheme"><option value="white">White</option><option value="orange">Orange</option></select></label>
<img id="hudimg" alt="HUD concept">
<p class="note">Design concept only. The HUD app is not in this repository yet ("Coming Soon" in the README),
so this cannot be installed. Speed in km/h, boost in psi, AFR as gasoline-equivalent (lambda × 14.7).</p>
</section>
<p class="note">Generated by tools/preview_ui.py. iDrive's own title bar is not shown.</p>
<script>
const $=id=>document.getElementById(id), targets=TARGETS, themes=THEMES;
targets.forEach((t,i)=>$("target").add(new Option(t.toLocaleString()+" RPM",i)));
themes.forEach((t,i)=>$("theme").add(new Option(t,i)));
$("target").value=DEFAULT;
function shift(){const r=Number($("rpm").value);$("rpmv").textContent=r<0?"no data":r;
 $("shiftimg").src="frames/shift_t"+$("target").value+"_"+(r<0?"none":r)+".png";}
function boost(){$("boostimg").src="frames/boost_"+$("theme").value+"_"+$("bstate").value+".png";}
function mhd(){$("mhdimg").src="frames/mhd_"+$("mstate").value+".png";}
function hud(){$("hudimg").src="frames/hud_"+$("hlayout").value+"_"+$("htheme").value+"_"+$("hstate").value+".png";}
$("rpm").oninput=$("target").onchange=shift;$("bstate").onchange=$("theme").onchange=boost;$("mstate").onchange=mhd;
$("hlayout").onchange=$("hstate").onchange=$("htheme").onchange=hud;
let dir=1;setInterval(()=>{if(!$("sweep").checked)return;let r=Number($("rpm").value)+dir*RPM_STEP;
 if(r>RPM_MAX||r<0){dir=-dir;r+=2*dir*RPM_STEP;}$("rpm").value=r;shift();},60);
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
 document.querySelectorAll("nav button").forEach(o=>o.classList.toggle("on",o===b));
 document.querySelectorAll("section").forEach(s=>s.hidden=s.id!==b.dataset.tab);});
shift();boost();mhd();hud();
</script>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=NATIVE / "preview")
    p.add_argument("--size", default="1280x480", help="iDrive output resolution, e.g. 1280x480")
    p.add_argument("--background", default="#0b1119", help="approximate iDrive page background")
    p.add_argument("--open", action="store_true", help="open the preview page in the browser")
    args = p.parse_args()

    size = tuple(int(n) for n in args.size.lower().split("x"))
    frames = args.out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for old in frames.glob("*.png"):
        old.unlink()

    def save(image, name):
        image.convert("RGB").save(frames / name, optimize=True)

    shift_bitmaps, shift_layout = dict(art.bitmaps()), boxes("ShiftDeck.xml")
    for t in range(len(art.TARGETS)):
        for rpm in [-1, *range(0, RPM_MAX + 1, RPM_STEP)]:
            save(render_shift(size, args.background, shift_bitmaps, shift_layout, rpm, t),
                 f"shift_t{t}_{'none' if rpm < 0 else rpm}.png")

    boost_bitmaps = dict(make_bars.bar_bitmaps())
    for theme in range(len(make_bars.THEMES)):
        for state in BOOST_STATES:
            save(render_boost(size, args.background, boost_bitmaps, state, theme),
                 f"boost_{theme}_{state}.png")

    mhd_bitmaps = dict(make_drive_apps.map_bitmaps())
    for state in MHD_STATES:
        save(render_mhd(size, args.background, mhd_bitmaps, state), f"mhd_{state}.png")

    for layout in HUD_LAYOUTS:
        for theme in HUD_THEMES:
            for state in HUD_STATES:
                save(render_hud(layout, theme, state), f"hud_{layout}_{theme}_{state}.png")

    page = (PAGE.replace("RPM_MAX", str(RPM_MAX)).replace("RPM_STEP", str(RPM_STEP))
            .replace("TARGETS", json.dumps(list(art.TARGETS)))
            .replace("THEMES", json.dumps(list(make_bars.THEMES)))
            .replace("DEFAULT", str(art.DEFAULT_INDEX)))
    index = args.out / "index.html"
    index.write_text(page, encoding="utf-8")
    print(f"Wrote {index}")
    if args.open:
        webbrowser.open(index.resolve().as_uri())


if __name__ == "__main__":
    main()

"""Bitmap assets for ShiftDeck's native iDrive 6 image components.

Every bitmap is drawn at exactly the size of the XML box that shows it
(see make_drive_apps.shift_app), so the layout does not depend on how iDrive
scales or crops oversized images.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BAR_BASE, BAR_WARN = 55800, 55833
LIGHT_BASE, TARGET_BASE = 55866, 55880
DIGIT_BASE, DIGIT_WARN = 55890, 55902
SHIFT_OFF, SHIFT_ON = 55914, 55915
SPEED_LEGEND, RPM_LEGEND, SCALE, LOWER = range(55916, 55920)
MARK_BASE = 55920
TARGETS = (5800, 6000, 6250, 6500, 6700)
METRIC_SPEED = True  # km/h; False shows mph
DEFAULT_INDEX = TARGETS.index(6500)

INK, MUTED, FAINT = "#eef3f9", "#8a9bb0", "#2c3a4b"
TRACK, TRACK_RED = "#151e29", "#2b1219"
AMBER, RED = "#ffb340", "#ff3b47"
# Bar colour by RPM: cool blue through cyan, amber approaching the limit, red past 7,000.
RAMP = ((0, "#1f78ff"), (3500, "#31b4ff"), (5200, "#7ee6ff"),
        (6000, "#ffc04d"), (6800, "#ff6a3d"), (8000, "#ff2e4d"))

# Bitmap sizes; make_drive_apps.py uses the same values for the XML boxes.
BAR_WIDTH, BAR_HEIGHT = 1104, 90
BAR_START, BAR_PITCH, BAR_SLANT = 4, (BAR_WIDTH - 28) / 16, 20
BAR_TOP, BAR_BOTTOM = 10, 80
LIGHTS_SIZE = (1104, 76)
TARGET_SIZE = (153, 135)
SHIFT_SIZE = (350, 42)
DIGIT_SIZE = (66, 120)
SPEED_LEGEND_SIZE = (200, 170)
RPM_LEGEND_SIZE = (68, 35)
SCALE_SIZE = (1104, 42)
LOWER_SIZE = (1104, 10)
MARK_SIZE = (1104, 9)


# Bundled so every host renders identical artwork. SIL Open Font License: fonts/OFL.txt.
FONT_FILE = Path(__file__).resolve().parent / "fonts" / "SairaSemiCondensed-SemiBold.ttf"


@lru_cache
def font(size):
    return ImageFont.truetype(str(FONT_FILE), size)


def art(size):
    image = Image.new("RGBA", size)
    return image, ImageDraw.Draw(image)


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(rgb(a), rgb(b)))


def ramp(rpm):
    for (r0, c0), (r1, c1) in zip(RAMP, RAMP[1:]):
        if rpm <= r1:
            return mix(c0, c1, max(0.0, (rpm - r0) / (r1 - r0)))
    return rgb(RAMP[-1][1])


def glow(image, radius, strength=0.75):
    """Put a soft halo of the image's own colours underneath it."""
    halo = image.convert("RGBa").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")
    halo.putalpha(halo.getchannel("A").point(lambda v: min(255, int(v * strength))))
    out = Image.new("RGBA", image.size)
    out.alpha_composite(halo)
    out.alpha_composite(image)
    return out


def hairline(image, y, color, x0=0, x1=None, width=2, fade=160):
    """Horizontal line that fades out towards both ends."""
    x1 = image.width if x1 is None else x1
    mask = Image.new("L", image.size)
    md = ImageDraw.Draw(mask)
    for x in range(x0, x1):
        edge = min(x - x0, x1 - 1 - x)
        md.line((x, y, x, y + width - 1), fill=int(255 * min(1.0, edge / fade)))
    image.alpha_composite(Image.composite(Image.new("RGBA", image.size, color),
                                          Image.new("RGBA", image.size), mask))


def label(draw, x, y, value, size, color=MUTED, align="left", tracking=2):
    """Small capitals with letter spacing. y is the top of the text."""
    face = font(size)
    widths = [face.getlength(c) for c in value]
    total = sum(widths) + tracking * (len(value) - 1)
    x = x - total if align == "right" else x - total / 2 if align == "center" else x
    for c, w in zip(value, widths):
        draw.text((x, y), c, font=face, fill=color, anchor="lt")
        x += w + tracking


@lru_cache
def digit_font():
    """Largest digit size whose '0' fits the digit box with some margin."""
    width, height = DIGIT_SIZE
    for size in range(150, 40, -2):
        left, top, right, bottom = font(size).getbbox("0", anchor="ls")
        if right - left <= width - 6 and bottom - top <= height - 24:
            return font(size)
    return font(40)


def digit(char, color, glow_radius=0):
    image, d = art(DIGIT_SIZE)
    if char == "-":
        cx, cy = DIGIT_SIZE[0] / 2, DIGIT_SIZE[1] / 2
        d.rounded_rectangle((cx - 13, cy - 3, cx + 13, cy + 3), radius=3, fill=color)
    elif char != " ":
        face = digit_font()
        _, top, _, bottom = face.getbbox("0", anchor="ls")
        baseline = DIGIT_SIZE[1] / 2 - (top + bottom) / 2
        d.text((DIGIT_SIZE[0] / 2, baseline), char, font=face, fill=color, anchor="ms")
    return glow(image, glow_radius, 0.9) if glow_radius else image


def segments(draw, fill_to=None, colors=None):
    """Draw the 16 slanted bar segments, optionally only up to x = fill_to."""
    left = BAR_START
    for seg in range(16):
        a = left + seg * BAR_PITCH
        b = a + BAR_PITCH - 4
        end = b if fill_to is None else min(fill_to, b)
        if end <= a:
            continue
        color = colors(seg) if colors else 255
        draw.polygon([(a + BAR_SLANT, BAR_TOP), (end + BAR_SLANT, BAR_TOP),
                      (end, BAR_BOTTOM), (a, BAR_BOTTOM)], fill=color)


@lru_cache
def bar_fill(alert):
    """Full-width bar colour: the RPM ramp, or solid red when shifting."""
    image = Image.new("RGBA", (BAR_WIDTH, BAR_HEIGHT))
    d = ImageDraw.Draw(image)
    span = 16 * BAR_PITCH
    for x in range(BAR_WIDTH):
        rpm = max(0.0, (x - BAR_START - BAR_SLANT / 2) / span * 8000)
        d.line((x, 0, x, BAR_HEIGHT), fill=rgb(RED) if alert else ramp(rpm))
    # Soft sheen across the top third.
    sheen = Image.new("L", image.size)
    sd = ImageDraw.Draw(sheen)
    for y in range(BAR_TOP, BAR_BOTTOM):
        sd.line((0, y, BAR_WIDTH, y), fill=int(70 * max(0.0, 1 - (y - BAR_TOP) / 28)))
    return Image.composite(Image.new("RGBA", image.size, "#ffffff"), image, sheen)


def bar(step, alert):
    image, d = art((BAR_WIDTH, BAR_HEIGHT))
    segments(d, colors=lambda seg: TRACK_RED if seg >= 14 else TRACK)
    if step:
        fill_to = BAR_START + step * BAR_PITCH / 2
        mask = Image.new("L", image.size)
        segments(ImageDraw.Draw(mask), fill_to)
        lit = Image.new("RGBA", image.size)
        lit.paste(bar_fill(alert), mask=mask)
        image.alpha_composite(glow(lit, 7, 0.8 if alert else 0.55))
        x = min(BAR_START + 16 * BAR_PITCH - 6, fill_to)
        edge, ed = art(image.size)
        ed.line([(x + BAR_SLANT, BAR_TOP - 3), (x, BAR_BOTTOM + 3)], fill=INK, width=4)
        image.alpha_composite(glow(edge, 5, 1.0))
    return image


def lights(step):
    """12 shift lights; step 13 lights all of them red (shift now)."""
    image, d = art(LIGHTS_SIZE)
    lit, ld = art(LIGHTS_SIZE)
    width, gap, top, bottom = 44, 10, 14, 40
    x0 = (LIGHTS_SIZE[0] - (12 * width + 11 * gap)) // 2
    for i in range(12):
        base = "#2fe08a" if i < 5 else "#ffc042" if i < 9 else RED
        x = x0 + i * (width + gap)
        box = (x, top, x + width, bottom)
        if step == 13 or i < step:
            ld.rounded_rectangle(box, radius=7, fill=RED if step == 13 else base)
        else:
            d.rounded_rectangle(box, radius=7, fill=rgb(base) + (34,),
                                outline=rgb(base) + (70,), width=1)
    image.alpha_composite(glow(lit, 8, 0.9))
    hairline(image, 60, RED if step == 13 else FAINT, x0 - 120, x0 + 12 * width + 11 * gap + 120,
             width=2 if step == 13 else 1, fade=140)
    return image


def target(rpm):
    image, d = art(TARGET_SIZE)
    right = TARGET_SIZE[0] - 2
    label(d, right, 6, "SHIFT AT", 20, align="right")
    d.text((right, 98), str(rpm), font=font(62), fill=AMBER, anchor="rs")
    label(d, right, 108, "RPM", 18, align="right")
    return image


def mark(rpm):
    image, d = art(MARK_SIZE)
    x = BAR_START + BAR_SLANT + round(16 * BAR_PITCH * rpm / 8000)
    d.polygon([(x - 7, 0), (x + 7, 0), (x, 8)], fill=AMBER)
    return image


def shift_banner(on):
    image, d = art(SHIFT_SIZE)
    if on:
        d.rounded_rectangle((1, 1, SHIFT_SIZE[0] - 2, SHIFT_SIZE[1] - 2), radius=9, fill=RED)
        label(d, SHIFT_SIZE[0] / 2, 7, "SHIFT", 28, color="#1c0509", align="center", tracking=10)
    return image


def speed_legend():
    image, d = art(SPEED_LEGEND_SIZE)
    label(d, 2, 2, "SPEED", 20)
    label(d, 2, 150, "KM/H" if METRIC_SPEED else "MPH", 18)
    return image


def rpm_legend():
    image, d = art(RPM_LEGEND_SIZE)
    label(d, 2, 4, "RPM", 20)
    return image


def scale():
    image, d = art(SCALE_SIZE)
    for i in range(17):
        x = BAR_START + round(i * BAR_PITCH)
        red = i >= 14
        if i % 2 == 0:
            d.line((x, 2, x, 12), fill=RED if red else INK, width=2)
            d.text((max(8, x), 16), str(i // 2), font=font(21),
                   fill=RED if red else MUTED, anchor="mt")
        else:
            d.line((x, 2, x, 7), fill=RED if red else FAINT, width=2)
    return image


def lower():
    image, _ = art(LOWER_SIZE)
    hairline(image, 4, FAINT, 120, LOWER_SIZE[0] - 120, width=1, fade=220)
    return image


def bitmaps():
    for alert in (False, True):
        for step in range(33):
            yield (BAR_WARN if alert else BAR_BASE) + step, bar(step, alert)
    for step in range(14):
        yield LIGHT_BASE + step, lights(step)
    for i, rpm in enumerate(TARGETS):
        yield TARGET_BASE + i, target(rpm)
        yield MARK_BASE + i, mark(rpm)
    for color, base, halo in ((INK, DIGIT_BASE, 0), (RED, DIGIT_WARN, 6)):
        for i, c in enumerate("0123456789- "):
            yield base + i, digit(c, color, halo)
    yield SHIFT_OFF, shift_banner(False)
    yield SHIFT_ON, shift_banner(True)
    yield SPEED_LEGEND, speed_legend()
    yield RPM_LEGEND, rpm_legend()
    yield SCALE, scale()
    yield LOWER, lower()


def save_assets(out, constants):
    with ZipFile(out,"w",ZIP_DEFLATED) as archive:
        for identifier,image in bitmaps():
            data=BytesIO()
            image.save(data,format="PNG",optimize=True)
            archive.writestr(ZipInfo(f"{identifier}.png"),data.getvalue(),ZIP_DEFLATED)
    constants.write_text(
        "// Generated by tools/make_drive_apps.py.\n"
        + "".join(f"static const unsigned {key} = {value}u;\n" for key,value in (
            ("kBarBase",BAR_BASE),("kBarWarn",BAR_WARN),("kLightBase",LIGHT_BASE),
            ("kTargetBase",TARGET_BASE),("kDigitBase",DIGIT_BASE),("kDigitWarn",DIGIT_WARN),
            ("kMarkBase",MARK_BASE),
            ("kShiftOff",SHIFT_OFF),("kShiftOn",SHIFT_ON),("kDefaultTarget",DEFAULT_INDEX),
            ("kMetricSpeed",int(METRIC_SPEED))))
        + "static const unsigned kShiftTargets[] = {"
        + ", ".join(map(str,TARGETS)) + "};\n"
    )

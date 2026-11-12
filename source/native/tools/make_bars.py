"""Generate shared, compact bar frames and the BoostDeck RHMI screens."""
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image, ImageDraw

import shift_art as art

ROOT = Path(__file__).resolve().parents[1]
# Metric readings (C, bar) for everything except boost, which always stays in psi.
# Bar ranges are in the collector's raw units (psi, AFR, deg, C, C, bar, bar, C)
# and do not depend on this switch.
METRIC = True
_TEMP, _PRESSURE = (r'\260C', 'bar') if METRIC else (r'\260F', 'psi')
BARS = (
    ('Boost', 'psi', 0, 35), ('AFR', '', 9, 19), ('Timing', 'deg', -20, 50),
    ('Intake air', _TEMP, -20, 100), ('Transmission', _TEMP, 0, 150),
    ('Rail pressure', _PRESSURE, 0, 250), ('Oil pressure', _PRESSURE, 0, 8), ('Coolant', _TEMP, 0, 130),
)
COLORS = (
    ('BMW Orange', '#ff4f1a'), ('Blue', '#58ceff'), ('Green', '#6cdb93'), ('White', '#e2eaf0'),
    ('Purple', '#ad72ff'), ('Pink', '#ff6cba'), ('Cyan', '#20f1e6'), ('Red', '#ff4545'),
)
MULTICOLOR = ('#58ceff', '#5de0bf', '#cbacff', '#ffbe70',
              '#6cdb93', '#60c5ff', '#ffb76a', '#6fd9ba')
THEMES = tuple(name for name, _ in COLORS) + ('Multicolor',)
STEPS, FIRST = 32, 55000

def add_props(component, x, y, w, h, image=False):
    props = ET.SubElement(component, 'properties')
    for prop, value in [(20,x), (21,y), (9,w), (10,h)] + ([(2,'false'),(3,'true')] if image else []):
        ET.SubElement(props, 'property', id=str(prop), value=str(value))

def label(parent, identifier, model):
    return ET.SubElement(parent, 'label', id=str(identifier), model=str(model))

def button(parent, identifier, action, text):
    ET.SubElement(parent,'button',id=str(identifier),action=str(action),text=text)

BAR_SIZE = (512, 17)  # same as the XML image box, so iDrive never rescales it
TRACK_TOP, TRACK_BOTTOM = 4, 13


def bar(fill, step):
    """One gauge bar: a dark notched track, filled to step/STEPS with the theme colour."""
    width, height = BAR_SIZE
    image = Image.new('RGBA', BAR_SIZE)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, TRACK_TOP, width-1, TRACK_BOTTOM), radius=5, fill='#18212c')
    for quarter in (1, 2, 3):
        x = round(width*quarter/4)
        draw.line((x, TRACK_TOP, x, TRACK_BOTTOM), fill='#0b1017', width=2)
    if step:
        end = max(12, round(width*step/STEPS))
        mask = Image.new('L', BAR_SIZE)
        ImageDraw.Draw(mask).rounded_rectangle((0, TRACK_TOP, end-1, TRACK_BOTTOM), radius=5, fill=255)
        colour = Image.new('RGBA', BAR_SIZE)
        cd = ImageDraw.Draw(colour)
        for x in range(end):
            # Darker at the start, full brightness at the leading edge.
            cd.line((x, 0, x, height), fill=art.mix('#0b1017', fill, 0.45 + 0.55*x/max(1, end-1)))
        cd.line((0, TRACK_TOP+1, end, TRACK_TOP+1), fill=art.mix(fill, '#ffffff', 0.45)+(140,))
        cd.rounded_rectangle((end-5, TRACK_TOP, end-1, TRACK_BOTTOM), radius=2,
                             fill=art.mix(fill, '#ffffff', 0.55))
        lit = Image.new('RGBA', BAR_SIZE)
        lit.paste(colour, mask=mask)
        image.alpha_composite(art.glow(lit, 3, 0.7))
    return image


def bar_bitmaps():
    for color, fill in enumerate(tuple(fill for _, fill in COLORS) + MULTICOLOR):
        for step in range(STEPS+1):
            if color and not step:
                continue
            yield FIRST+color*STEPS+step, bar(fill, step)


def build_assets():
    with ZipFile(ROOT/'plugin/BoostDeck_ALL_Images.zip','w', compression=ZIP_DEFLATED) as archive:
        for identifier, image in bar_bitmaps():
            data = BytesIO()
            image.save(data,format='PNG',optimize=True)
            archive.writestr(ZipInfo(f'{identifier}.png'),data.getvalue(),ZIP_DEFLATED)

def main():
    root = ET.Element('pluginApps')
    app = ET.SubElement(root,'pluginApp',applicationType='VehicleInformation',applicationWeight='210',text='BoostDeck')
    entry = ET.SubElement(app,'entryButton',id='1',action='10')
    ET.SubElement(ET.SubElement(entry,'properties'),'property',id='23',value='2')
    actions = ET.SubElement(app,'actions')
    for identifier, target in ((10,100),(31,104)):
        ET.SubElement(actions,'hmiAction',id=str(identifier),target=str(target))
    for identifier in range(40,40+len(THEMES)):
        ET.SubElement(actions,'raAction',id=str(identifier))
    models = ET.SubElement(app,'models')
    states = ET.SubElement(app,'hmiStates')
    gauges = ET.SubElement(states,'hmiState',id='100',text='BoostDeck')
    options = ET.SubElement(gauges,'optionComponents')
    button(options,33,31,'Gauge color')
    content = ET.SubElement(gauges,'components')
    for i in range(len(BARS)):
        ET.SubElement(models,'raDataModel',id=str(50+i),modelType='Richtext')
        ET.SubElement(models,'imageIdModel',id=str(60+i),imageId=str(FIRST))
        bar = ET.SubElement(content,'image',id=str(200+i),model=str(60+i))
        reading = label(content,150+i,50+i)
        x,y = 52+568*(i//4),22+101*(i%4)
        add_props(bar,x,y,512,17,True)
        add_props(reading,x,y+23,516,55)
    ET.SubElement(models,'raDataModel',id='110',modelType='Richtext')
    color_screen = ET.SubElement(states,'hmiState',id='104',parentId='100',text='Gauge color')
    content = ET.SubElement(color_screen,'components')
    label(content,389,110)
    for i,name in enumerate(THEMES):
        button(content,390+i,40+i,name)
    ET.indent(root,space='  ')
    ET.ElementTree(root).write(ROOT/'plugin/BoostDeck.xml',encoding='utf-8',xml_declaration=True)
    scales = [f'    {{{lo}, {hi}, "{name}", "{unit}"}}' for name,unit,lo,hi in BARS]
    colors = ', '.join(f'"{name}"' for name in THEMES)
    (ROOT/'src/dial_scales.h').write_text(
        '// Generated by tools/make_bars.py.\n'
        f'static const unsigned kDialSteps = {STEPS};\n'
        f'static const unsigned kBarFirst = {FIRST};\n'
        f'static const unsigned kMulticolor = {len(COLORS)};\n'
        f'static const int kMetricUnits = {int(METRIC)}; /* boost always stays in psi */\n'
        f'static const char *const kColorNames[] = {{{colors}}};\n'
        'static const struct DialScale { double minimum, maximum; const char *name, *unit; } kDialScales[] = {\n'
        + ',\n'.join(scales) + '\n};\n')
    build_assets()

if __name__ == '__main__':
    main()

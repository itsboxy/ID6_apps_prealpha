"""Generate RHMI resources for the standalone ShiftDeck and MHD Maps apps."""
from pathlib import Path
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from PIL import Image, ImageDraw, ImageFont
import shift_art as art

ROOT = Path(__file__).resolve().parents[1] / "plugin"


def save(doc, name):
    ET.indent(doc, space="  ")
    ET.ElementTree(doc).write(ROOT / name, encoding="utf-8", xml_declaration=True)


def entry(name, title, weight, connector, root_id, images=False):
    manifest = ET.parse(ROOT / "BoostDeck.manifest").getroot()
    manifest.find("name").text = name
    manifest.find("file").text = "{pluginDir}/" + name
    manifest.find("config/dependencies/pluginAppConnector/id").text = str(connector)
    params = manifest.find("config/parameters")
    params.find("DescFile").text = f"{{pluginDir}}/{name}.xml"
    if images:
        params.find("ImageDBFile").text = f"{{pluginDir}}/{name}_ALL_Images.zip"
    else:
        params.remove(params.find("ImageDBFile"))
    save(manifest, f"{name}.manifest")

    doc = ET.Element("pluginApps")
    app = ET.SubElement(doc, "pluginApp", applicationType="VehicleInformation",
                        applicationWeight=str(weight), text=title)
    entry_button = ET.SubElement(app, "entryButton", id="1", action="10")
    ET.SubElement(ET.SubElement(entry_button, "properties"), "property", id="23", value="2")
    actions = ET.SubElement(app, "actions")
    ET.SubElement(actions, "hmiAction", id="10", target=str(root_id))
    models = ET.SubElement(app, "models")
    states = ET.SubElement(app, "hmiStates")
    state = ET.SubElement(states, "hmiState", id=str(root_id), text=title)
    return doc, actions, models, states, state


def position(component, x, y, width, height):
    properties = ET.SubElement(component, "properties")
    for prop, value in ((20,x),(21,y),(9,width),(10,height)):
        ET.SubElement(properties, "property", id=str(prop), value=str(value))


def button(group, identifier, action, text):
    ET.SubElement(group, "button", id=str(identifier), action=str(action), text=text)


def shift_app():
    name="ShiftDeck"
    doc, actions, models, states, page = entry(name,"ShiftDeck",207,104,100,images=True)
    ET.SubElement(actions,"hmiAction",id="40",target="101")
    for i in range(len(art.TARGETS)):
        ET.SubElement(actions,"raAction",id=str(41+i))
    ET.SubElement(models,"raDataModel",id="53",modelType="Richtext")

    options=ET.SubElement(page,"optionComponents")
    button(options,30,40,"Shift light setting")
    content=ET.SubElement(page,"components")

    def graphic(model, asset, x, y, w, h):
        ET.SubElement(models,"imageIdModel",id=str(model),imageId=str(asset))
        image=ET.SubElement(content,"image",id=str(200+model-54),model=str(model))
        position(image,x,y,w,h)
        props=image.find("properties")
        ET.SubElement(props,"property",id="2",value="false")
        ET.SubElement(props,"property",id="3",value="true")

    # Every box matches its bitmap size in shift_art.py exactly.
    dw, dh = art.DIGIT_SIZE
    rpm_x = 604 - 2*dw  # four RPM digits centred on the bar (x 52..1156)
    graphic(54,art.BAR_BASE,52,287,art.BAR_WIDTH,art.BAR_HEIGHT)
    graphic(55,art.LIGHT_BASE,52,10,*art.LIGHTS_SIZE)
    graphic(56,art.TARGET_BASE+art.DEFAULT_INDEX,1003,113,*art.TARGET_SIZE)
    graphic(57,art.SHIFT_OFF,604-art.SHIFT_SIZE[0]//2,232,*art.SHIFT_SIZE)
    for i in range(4):
        graphic(60+i,art.DIGIT_BASE+(10 if i in (1,2) else 11),rpm_x+i*dw,100,dw,dh)
    graphic(64,art.SPEED_LEGEND,53,96,*art.SPEED_LEGEND_SIZE)
    graphic(65,art.RPM_LEGEND,rpm_x+4*dw+10,182,*art.RPM_LEGEND_SIZE)
    graphic(66,art.SCALE,52,377,*art.SCALE_SIZE)
    graphic(67,art.LOWER,52,422,*art.LOWER_SIZE)
    graphic(68,art.MARK_BASE+art.DEFAULT_INDEX,52,278,*art.MARK_SIZE)
    for i in range(3):
        graphic(70+i,art.DIGIT_BASE+(10 if i==2 else 11),53+i*dw,122,dw,dh)

    settings=ET.SubElement(states,"hmiState",id="101",parentId="100",text="Shift light setting")
    items=ET.SubElement(settings,"components")
    ET.SubElement(items,"label",id="300",model="53")
    for i,rpm in enumerate(art.TARGETS):
        button(items,301+i,41+i,f"{rpm:,} RPM")

    save(doc,f"{name}.xml")
    art.save_assets(ROOT/f"{name}_ALL_Images.zip",ROOT.parent/"src"/"shift_assets.h")


def map_app():
    name="MhdMaps"
    doc, actions, models, _, page = entry(name,"MHD Maps",208,103,100,images=True)
    ET.SubElement(models,"raDataModel",id="50",modelType="Richtext")
    content=ET.SubElement(page,"components")
    def graphic(component, model, asset, x, y, w, h):
        ET.SubElement(models,"imageIdModel",id=str(model),imageId=str(asset))
        image=ET.SubElement(content,"image",id=str(component),model=str(model))
        position(image,x,y,w,h)
        ET.SubElement(image.find("properties"),"property",id="2",value="false")
        ET.SubElement(image.find("properties"),"property",id="3",value="true")

    graphic(200,60,56000,420,35,360,48)
    graphic(201,61,56001,174,118,852,192)
    ET.SubElement(models,"imageIdModel",id="62",imageId="56002")
    for slot in range(1,5):
        ET.SubElement(actions,"raAction",id=str(20+slot))
        # iDrive places native button text at the top left. Keep its focus
        # outline and action, and draw the centered labels on the tile art.
        tile=ET.SubElement(content,"button",id=str(150+slot),action=str(20+slot),imageModel="62")
        position(tile,174+(slot-1)*220,118,192,192)
    position(ET.SubElement(content,"label",id="150",model="50"),174,337,850,53)
    save(doc,f"{name}.xml")

    with ZipFile(ROOT/f"{name}_ALL_Images.zip","w",ZIP_DEFLATED) as archive:
        for identifier,image in map_bitmaps():
            data=BytesIO()
            image.save(data,format="PNG",optimize=True)
            archive.writestr(ZipInfo(f"{identifier}.png"),data.getvalue(),ZIP_DEFLATED)


MAP_ACCENTS = ("#5cc8ff", "#2fe08a", "#ffb340", "#ff3b47")


def map_bitmaps():
    """MHD Maps title (360x48), four 192px tiles on a 852x192 strip, and a
    transparent button icon. Tile x positions must match the button boxes."""
    logo, d = art.art((360, 48))
    art.label(d, 180, 8, "MHD MAPS", 30, color=art.INK, align="center", tracking=6)
    art.hairline(logo, 44, art.FAINT, 40, 320, width=1, fade=80)

    tiles, _ = art.art((852, 192))
    for slot, x in enumerate(range(0, 4*220, 220), 1):
        card, cd = art.art((192, 192))
        mask = Image.new("L", (192, 192))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 191, 191), radius=16, fill=255)
        shade = Image.new("RGBA", (192, 192))
        sd = ImageDraw.Draw(shade)
        for y in range(192):
            sd.line((0, y, 191, y), fill=art.mix("#1d2938", "#0f161f", y/191))
        card.paste(shade, mask=mask)
        cd.rounded_rectangle((0, 0, 191, 191), radius=16, outline="#33455a", width=2)
        accent = MAP_ACCENTS[slot-1]
        strip, sd = art.art((192, 192))
        sd.rounded_rectangle((22, 18, 70, 23), radius=3, fill=accent)
        card.alpha_composite(art.glow(strip, 4, 0.8))
        art.label(cd, 22, 36, "MAP", 22, color=art.MUTED, tracking=4)
        cd.text((170, 176), str(slot), font=art.font(118), fill=art.INK, anchor="rs")
        tiles.alpha_composite(card, (x, 0))
    return ((56000, logo), (56001, tiles), (56002, Image.new("RGBA", (1, 1))))


if __name__=="__main__":
    shift_app()
    map_app()

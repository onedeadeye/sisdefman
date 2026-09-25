"""Synthetic item definitions shaped like a real crate setup:

    1        crate (lists the non-epic items in its description)
    100      exchange entry point  -> 198 / 199
    101-104  one generator per rarity, listing the items of that rarity
    105      tag-based exchange recipe
    109      tag generator
    110-117  crate items (3 common, 2 uncommon, 1 rare, 2 epic)
    118-119  dummy items left over from removed items
    197-199  root / standard / overlay generators
    999999   playtime generator
"""

import copy
import json
import os

APPID = 480

RARITY_COLORS = {"common": "d2d2d2", "uncommon": "5e90e0", "rare": "eb7ce9", "epic": "f08f35"}

ITEMS = [
    ("Pistol | Red", "common", "pistol"),
    ("Rifle | Blue", "common", "rifle"),
    ("Shotgun | Green", "common", "shotgun"),
    ("Pistol | Stripes", "uncommon", "pistol"),
    ("Rifle | Camo", "uncommon", "rifle"),
    ("Shotgun | Cracks", "rare", "shotgun"),
    ("Pistol | Flames", "epic", "pistol"),
    ("Rifle | Sparks", "epic", "rifle"),
]


def skin(itemdefid, name, rarity, weapon, series="crate1"):
    finish = name.split(" | ")[1]
    return {
        "itemdefid": itemdefid,
        "type": "item",
        "name": name,
        "description": f"Applies the {finish} appearance to the {name.split(' | ')[0]}.",
        "name_color": RARITY_COLORS[rarity],
        "background_color": "292929",
        "tradable": True,
        "marketable": True,
        "tags": f"type:skin;series:{series};rarity:{rarity};weapon:{weapon}",
    }


def dummy(itemdefid):
    return {
        "itemdefid": itemdefid,
        "type": "item",
        "name": f"Dummy Item #{itemdefid}",
        "description": "This is a dummy item.",
        "name_color": "d2d2d2",
        "background_color": "292929",
        "icon_url": "",
        "icon_url_large": "",
        "tradable": False,
        "marketable": False,
    }


def crate_file():
    skins = [skin(110 + n, name, rarity, weapon) for n, (name, rarity, weapon) in enumerate(ITEMS)]
    listed = "\n".join(s["name"] for s in skins if "rarity:epic" not in s["tags"])
    by_rarity = {r: ";".join(str(s["itemdefid"]) for s in skins if f"rarity:{r}" in s["tags"])
                 for r in RARITY_COLORS}
    items = [
        {
            "itemdefid": 1,
            "type": "item",
            "name": "Test Crate",
            "description": f"Contains an item from the Test Series.\n\n{listed}\n\n...or something rare!",
            "tags": "type:lootbox;series:crate1",
        },
        {"itemdefid": 100, "type": "generator", "exchange": "1", "bundle": "198x199;199x1", "name": "Entry"},
        {"itemdefid": 101, "type": "generator", "bundle": by_rarity["common"], "name": "Commons"},
        {"itemdefid": 102, "type": "generator", "bundle": by_rarity["uncommon"], "name": "Uncommons"},
        {"itemdefid": 103, "type": "generator", "bundle": by_rarity["rare"], "name": "Rares"},
        {"itemdefid": 104, "type": "generator", "bundle": by_rarity["epic"], "name": "Epics"},
        {"itemdefid": 105, "type": "generator", "exchange": "series:crate1*5,rarity:common*5",
         "bundle": "102", "name": "Trade-up"},
    ]
    items += skins
    items += [dummy(118), dummy(119)]
    items += [
        {"itemdefid": 197, "type": "generator", "bundle": "101x1000;102x200;103x40;104x8", "name": "Root"},
        {"itemdefid": 198, "type": "generator", "bundle": "197", "name": "Standard"},
        {"itemdefid": 199, "type": "generator", "bundle": "197", "name": "Overlay", "tag_generators": "109"},
    ]
    return {"appid": APPID, "items": items}


def extras_file():
    return {
        "appid": APPID,
        "items": [
            {"itemdefid": 109, "type": "tag_generator", "name": "Overlay tags",
             "tag_generator_name": "overlay", "tag_generator_values": "a;b;c"},
            {"itemdefid": 999999, "type": "playtimegenerator", "bundle": "1x20", "name": "Playtime"},
        ],
    }


def write_files(directory):
    paths = []
    for name, doc in (("crate.json", crate_file()), ("extras.json", extras_file())):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=4)
        paths.append(path)
    return paths


def all_items():
    return {it["itemdefid"]: copy.deepcopy(it) for doc in (crate_file(), extras_file()) for it in doc["items"]}

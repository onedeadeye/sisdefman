# sisdefman

A command-line tool for managing Steam Inventory Service item definitions that are
organised into **series**, such as the items in a crate. It keeps every definition in one
project file and exports one Steam-ready JSON file.

- Writes each series item's series name and position into its description
  (`First Series #8`). The text stays correct when items are added, inserted, moved or
  removed.
- Inserts items in the middle of a series. The items after the new one are renumbered, and
  every reference to them (`bundle`, `exchange`, `tag_generators`) is rewritten to match.
- Keeps rarity generators and the item list in a crate's description up to date.
- Exports dummy placeholders for IDs a series no longer uses, so an old definition never
  lingers on Steam.
- Has a **prerelease** and a **release** mode. In release mode, any change that would alter
  an item players may already own shows a warning and needs explicit confirmation.

## Install

Needs Python 3.8 or newer. There are no other dependencies.

```sh
pip install -e path/to/sisdefman   # installs the `sisdefman` command
python -m sisdefman --help         # or run it from the repository without installing
```

## Quick start

```sh
sisdefman import crate1.json crate2.json playtime.json tag_generators.json
sisdefman check
sisdefman export                   # writes itemdefs.json: upload this file in Steamworks
```

`import` creates `sisdefman.json` and prints what it detected. To use a different project
file, pass `-p FILE` or set `SISDEFMAN_PROJECT`. From then on, `sisdefman.json` is the file
you keep and edit, and `itemdefs.json` is generated from it.

Suppose crate 1 is an item whose description lists its contents, and its items are laid out
like this. The import then sets up the following:

| Found | Becomes |
| --- | --- |
| Items tagged `series:crate1` at IDs 110-134 | series `crate1`, items #1-#25 |
| Dummy items at 135-143 | IDs the series has used (`allocated_through: 143`), still exported as dummies |
| The next unrelated ID, 197 | the end of the series' range (`last_id: 196`) |
| `Contains ... from the First Series.` in crate 1 | the display name `First Series` |
| The list of names in crate 1's description | `{contents}`, generated from every item not tagged `rarity:epic` |
| Generators 101-104, whose bundles hold exactly one rarity | rules such as `rarity:common`, so new items join them automatically |

If a crate's list is out of date, the import prints the difference, for example when a crate
still lists `Rifle | Old Name` after the item was renamed to `Rifle | Camo`.

## Series

A series is a named range of itemdefids (`first_id` to `last_id`). Every definition in the
range belongs to the series. An item's **index** is its position in the range, counting
from 1 at the lowest ID and skipping unused IDs. The part of the range after the last item
is free space for new items.

### What is generated on export

Everything else is exported exactly as stored, including field order.

| What | Stored in the project as | Exported as |
| --- | --- | --- |
| Descriptions of series items | the plain description | the description template filled in (below) |
| Descriptions of containers (crates) | text containing `{contents}` | `{contents}` replaced by the names of the series' items, one per line, leaving out items with an `exclude` tag |
| Bundles of the generators listed in a series' `generators` | a tag rule, e.g. `rarity:common` | every series item with that tag, in series order |
| Unused IDs from `first_id` to `allocated_through` | nothing | dummy items built from `settings.dummy_item` |

Steam keeps its current definition for any itemdefid that an upload leaves out, so an ID a
series stops using is exported as a dummy item instead of being dropped.

### Description template

The default is `{description}\n\n{series_name} #{index}`. It uses Python format syntax and
these fields:

| Field | Value |
| --- | --- |
| `{description}` | the item's stored description |
| `{series_name}` / `{series}` | display name / key of the series |
| `{index}` / `{count}` | position in the series / number of items in it (e.g. `{index:03d}` pads to 3 digits) |
| `{name}` / `{itemdefid}` | the item's name / ID |
| `{tags[rarity]}` | the value of one of the item's tags |

```sh
sisdefman template '{description}\n\n{series_name} #{index}'   # \n is a line break
sisdefman series set crate2 --template '{description}\n\n{series_name} - {index} of {count}'
sisdefman series set crate2 --template ''                       # back to the global one
sisdefman show 117                                              # preview one exported item
```

## Adding, inserting, moving and removing items

```sh
# Append (safe: nothing is renumbered)
sisdefman add crate1 --like 121 \
    --set "name=Rifle | Hot Rod" \
    --set "description=Applies the Hot Rod appearance to the Rifle." \
    --set icon_url=https://example.com/rifle_hotrod_small.png \
    --set icon_url_large=https://example.com/rifle_hotrod.png

# Insert at a position (renumbers everything after it)
sisdefman add crate1 --after 116 --from new_common.json
sisdefman add crate1 --position 8 --from several_items.json

sisdefman move 124 --before 122
sisdefman remove 118              # later items move down to close the gap
sisdefman remove 118 --no-shift   # leave a gap (exported as a dummy)
```

- **Item fields.** `--from FILE` reads an item object, or a list of items that are added in
  order. `--like ID` starts from a copy of an existing definition. `--set FIELD=TEXT` and
  `--set-json FIELD=JSON` (for `true`, numbers, etc.) set fields.
- **IDs and tags.** The itemdefid comes from the item's position. The `series:` tag is set
  for you.
- **Renumbering.** Only the consecutive run of items after the change is renumbered. An
  unused ID absorbs the shift.
- **Removing referenced items.** `remove` refuses while another definition refers to the
  item. Generators with a series rule don't count.
- **Previewing.** Add `-n` / `--dry-run` to any of these commands to see the effect without
  saving.
- **Series size.** When a series runs out of room, raise its limit with
  `sisdefman series set crate1 --last-id N`. The IDs after it must be free.

To edit anything else (names, icons, tags, crate descriptions, generators outside series),
edit `sisdefman.json` directly and run `sisdefman check`.

## Prerelease and release mode

Steam inventories store itemdefids. If the definition behind an ID changes to a different
item, every player who owns that ID ends up with the new item. Inserting, moving or removing
series items does exactly that.

- **`prerelease`** (the default) lets these operations happen freely and reports what was
  renumbered.
- **`release`** records which definitions are live, meaning every item players can own.
  Any change that would alter one of them then stops with a warning listing every affected
  ID (`#117 'Rifle | Camo' -> 'Pistol | Stripes'`). To go ahead, you
  must type `CHANGE PLAYER INVENTORIES` or pass `--accept-inventory-changes`. Without a
  terminal and without the flag, the change is refused.

Release mode checks each change twice:

1. **At the operation.** `add`, `move` and `remove` check the IDs they would change.
2. **At export.** `export` compares the whole export with the live record, which also
   catches changes made by editing the project file by hand. Use `sisdefman diff` to see
   the same comparison without exporting.

```sh
sisdefman mode release         # when the game launches; records the live baseline
sisdefman export --mark-live   # export and record it as live (use right before uploading)
sisdefman mark-live            # or record it after uploading
sisdefman mode prerelease      # turns protection off; needs the same confirmation
```

After you upload an export, run `mark-live` (or export with `--mark-live`) so the next
comparison starts from what is actually on Steam. Forgetting is safe: you will just see the
same warning again.

If a live item is replaced by a dummy (`remove --no-shift`), players still hold that ID, so
it stays protected. In release mode, `add` without a position skips such retired IDs and
uses the next free one. Appending is always safe.

## Other commands

| Command | Does |
| --- | --- |
| `sisdefman list [SERIES]` | series overview, or the items of one series with index and ID (`--all` for every definition) |
| `sisdefman show ID [--stored]` | one definition as exported (or as stored) |
| `sisdefman check` | validates the project: unparsable bundles, references to undefined IDs, overlapping series, `series:` tags outside their series, etc. |
| `sisdefman diff [--against FILE ...] [-v]` | what changed compared with the live baseline, or with Steam item definition files |
| `sisdefman series` | shows each series' range, containers and generator rules |
| `sisdefman series new KEY --first-id N --last-id N [--name NAME]` | creates an empty series |
| `sisdefman series set KEY [--name] [--last-id] [--template] [--container ID [--exclude TAG]] [--generator ID=TAGS] [--remove ID]` | configures one |
| `sisdefman import FILE ... [--replace]` | adds more files to an existing project |

## The project file

```jsonc
{
    "sisdefman": 1,
    "appid": 480,
    "mode": "prerelease",
    "settings": {
        "description_template": "{description}\n\n{series_name} #{index}",
        "dummy_item": { "type": "item", "name": "Dummy Item #{itemdefid}", "description": "This is a dummy item.", ... }
    },
    "series": {
        "crate1": {
            "name": "First Series",
            "first_id": 110,
            "last_id": 196,               // the series may grow up to here
            "allocated_through": 143,     // highest ID it has used; unused ones export as dummies
            "containers": { "1": { "exclude": ["rarity:epic"] } },
            "generators": { "101": "rarity:common", "102": "rarity:uncommon", "103": "rarity:rare", "104": "rarity:epic" }
        }
    },
    "items": [ ... every definition, in Steam's format ... ],
    "live": null                          // release mode: { "recorded_at": ..., "items": { "110": "Pistol | Red", ... } }
}
```

Series items store their plain description, and crates store `{contents}` where the item
list goes. Dummy items inside a series' range are not stored. A generator bundle listed
under `generators` is rewritten whenever the project is saved.

## Development

```sh
python -m unittest discover -s tests -t .
```

# sisdefman

A tool for managing Steam Inventory Service item definitions. It works like a small
database: every definition lives in one project file, you edit it in a browser GUI or from
the command line, and it exports one Steam-ready JSON file.

- **Derived fields.** Describe a family of items once as a *kind*. For example, a weapon
  skin could be defined by its weapon, finish, material ID, rarity and flavor text.
  sisdefman then builds the name, description, icon URLs, colours and tags from those
  fields and from lookup tables (weapon → display name).
- **Series numbering.** Each item's series name and position go into its description
  (`First Series #8`). The text stays correct when items are added, inserted, moved or
  removed.
- **Insertion in the middle of a series.** Later items are renumbered. Every reference to
  them (`bundle`, `exchange`, `tag_generators`), rarity generators and crate item lists are
  kept up to date.
- **Prerelease and release modes.** In release mode, any change that would alter an item
  players may already own shows a warning and needs explicit confirmation.
- **Dummy placeholders.** IDs a series no longer uses are exported as dummy items, so an old
  definition never lingers on Steam.
- **Standard colours.** Define each colour once by keyword (`@rare`) and use the keyword
  wherever a colour goes.
- **New series in one step.** A new series can start as a copy of another series' crate,
  generators and exchange recipes, with IDs, references and names moved over.

## Install

Needs Python 3.8 or newer. There are no other dependencies.

```sh
pip install -e path/to/sisdefman   # installs the `sisdefman` command
python -m sisdefman --help         # or run it from the repository without installing
```

## Quick start

```sh
sisdefman import crate1.json crate2.json playtime.json tag_generators.json
sisdefman gui                      # opens the editor in your browser
sisdefman export                   # writes itemdefs.json: upload this file in Steamworks
```

`import` merges any number of Steam item definition files into `sisdefman.json` and prints
what it detected. To use a different project file, pass `-p FILE` or set
`SISDEFMAN_PROJECT`. From then on, `sisdefman.json` is the file you keep (in version
control, ideally) and `itemdefs.json` is generated from it.

## The GUI

`sisdefman gui` starts a local web app and opens it in your browser. It only listens on your
own machine. Keep the terminal open while you use it, and stop it with Ctrl+C or with
**Quit** in the page.

### Choosing a project

The GUI opens the project in the current folder (`sisdefman.json`, or the one given with
`-p`). If there is none, it starts with a **project chooser**:

- **Recent projects.** The projects you opened before, one click away.
- **Browse.** A folder browser that marks sisdefman projects, with an Open button on each.
- **New project in this folder.** Tick Steam item definition files to import them, as
  `sisdefman import` would, or create an empty project.

```sh
sisdefman gui              # this folder's project, or the chooser
sisdefman gui --choose     # always start with the chooser
sisdefman-gui              # the chooser from anywhere, e.g. from a desktop shortcut
sisdefman-gui mygame.json  # open that project
```

Click the project's file name in the top bar to go back to the chooser and open another
project. The list of recent projects is kept in `recent.json` in your settings folder:

- Windows: `%APPDATA%\sisdefman`
- macOS: `~/Library/Application Support/sisdefman`
- other systems: `~/.config/sisdefman`

### Working in the GUI

- **Items.** Browse all items, one series, one kind, or the definitions outside any series,
  and search by name, ID, tag or field. Select an item to edit it:
  - fields of its kind, with drop-downs for lookup-table values;
  - its derived fields, each of which you can override for that one item;
  - any other Steam fields;
  - a live preview of the exact JSON Steam will get.
- **Several items at once.** Tick items to set a field on all of them, convert them to a
  kind, or detach them.
- **Series actions.** Add an item at the end or at a chosen position, duplicate, move or
  remove items.
- **Item kinds and Lookup tables.** Edit the rules and tables. Previews update as you type.
  **Import CSV…** brings in reference data such as an Unreal DataTable export (below).
- **Colors.** The colour palette, with swatches, the number of values using each colour and
  **Convert existing colours…** (see [Colours](#colours)).
- **Series setup.** Set each series' ID range, display name, description template, crates
  and generators. **+ New series** (also in the sidebar) creates a series, empty or as a
  copy of another one's setup (see [Creating a series](#creating-a-series)).
- **Settings & export.**
  - Switch between prerelease and release mode.
  - Record the live baseline.
  - Export, and import more Steam files.
  - Edit the series line and the dummy item.
  - Open another project, or quit.
- **Check.** Lists problems, each linked to its item.
- **Undo.** Covers every change made in the GUI to the open project; switching projects
  starts a fresh history.

In release mode, the GUI shows the same warning and asks for the same typed confirmation as
the command line.

## Kinds and derived fields

A kind has **fields**, which you enter for each item, and **rules**, which build Steam fields
from them. A **lookup table** maps keys to values. A field of type `ref` holds a table key,
and a template reads that row's columns.

```jsonc
{
    "tables": {
        "weapon": {"columns": ["name", "class"], "rows": {"pistol": {"name": "Pistol", "class": "Basic"}}},
        "rarity": {"columns": ["name", "color"], "rows": {"common": {"name": "Common", "color": "d2d2d2"}}}
    },
    "kinds": {
        "skin": {
            "fields": {
                "weapon": {"type": "ref", "table": "weapon"},
                "finish": {"type": "text"},
                "mat_id": {"type": "text"},
                "rarity": {"type": "ref", "table": "rarity"},
                "flavor": {"type": "multiline", "optional": true}
            },
            "derive": {
                "type": "item",
                "name": "{weapon.name} | {finish}",
                "display_type": "{rarity.name} {weapon.class} Weapon",
                "description": ["Applies the {finish} appearance to the {weapon.name}.", "{flavor}"],
                "name_color": "{rarity.color}",
                "icon_url": "https://example.com/{weapon}_{mat_id}_small.png",
                "icon_url_large": "https://example.com/{weapon}_{mat_id}.png",
                "tradable": true,
                "marketable": true,
                "tags": "type:skin;series:{series};rarity:{rarity};weapon:{weapon}",
                "mat_id": "{mat_id}"
            }
        }
    }
}
```

With that schema, a skin is stored as just its fields:

```json
{"itemdefid": 110, "kind": "skin", "weapon": "pistol", "finish": "Red", "mat_id": "red", "rarity": "common"}
```

Everything else is derived on export. Change a weapon's display name in the table, and
every skin of that weapon follows.

- **Field types:**
  - `text` and `multiline`;
  - `number`;
  - `bool`;
  - `ref`, which needs a `table`.

  Mark a field `optional` if it may be empty. A `default` is a template used when the field
  is empty. Fields are not exported unless a rule outputs them, like `"mat_id": "{mat_id}"`
  above.
- **Rules:**
  - A **template** string.
  - A **list of templates**, one per paragraph. Empty paragraphs are dropped and the rest
    are joined with a blank line.
  - Any other JSON value (`true`, a number), which is exported as it is.

  Rules are exported in the order they are listed.
- **Templates** use Python's format syntax:

  | Syntax | Gives |
  | --- | --- |
  | `{field}` | a field's value |
  | `{weapon.name}` | a column of the table row that a `ref` field points to |
  | `{series}`, `{series.name}`, `{series.index}`, `{series.count}` | the item's series key, display name, position and size |
  | `{series.count_no_secret}`, `{series.count_secret}` | the series' size without secret rares, and its number of secret rares (see [Secret rares](#secret-rares-and-item-counts)) |
  | `{itemdefid}` | the item's ID |
  | `{name}` | another derived field (here, the name) |
  | `{series.index:03d}` | a number padded to three digits |
  | `{{` | a literal brace |

- **Overrides.** A value stored on an item for a derived field replaces the rule for that
  item only: `sisdefman set 110 name_color=ff0000`. Remove it again with
  `sisdefman set 110 --unset name_color`.

### Flavor text

Give the kind an optional `multiline` field (`flavor` above) and put `"{flavor}"` as a
paragraph of the description rule. Then set it in the GUI's text box, or from the command
line:

```sh
sisdefman set 117 "flavor=Found in the jungle. Still warm."
```

Items without flavor text keep a description without the extra paragraph. The series line
still comes last:

```text
Applies the Camo appearance to the Rifle.

Found in the jungle. Still warm.

First Series #8
```

### Converting existing items (adopt)

Write the schema, load it, and let sisdefman work out each item's fields from its current
definition:

```sh
sisdefman schema import skin-schema.json
sisdefman adopt skin --where tags.type=skin -v
```

`adopt` matches each rule against the item's stored value. For example, `{weapon.name} |
{finish}` against `Pistol | Red` gives `finish = Red`. The values it learns fill in the
lookup tables, so the tables can start empty. Matching repeats across all selected items, so
a value that is ambiguous in one item is settled by another.

**The export never changes:**

- A stored value that a rule doesn't reproduce is kept as an override and listed in the
  report.
- An item whose export would change anyway is left alone and listed with the reason.

`sisdefman detach IDS` turns items back into plain definitions.

## Reference data from CSV files

Game data exported to CSV can be added to a lookup table for reference. For example, an
Unreal Engine DataTable of weapons:

```sh
sisdefman table import weapon weapons.csv --skip ActorClass \
    --fill name=DisplayName --fill class=Category
```

- **Keys.** The first column (Unreal's `---`) holds the row keys. They match existing rows
  ignoring case, so `Pistol` in the file updates the row `pistol`. New keys follow
  the table's case.
- **Unreal notation is cleaned up:**
  - `NSLOCTEXT("…", "…", "Handgun")` becomes `Handgun`;
  - row handles `(DataTable=…,RowName="Basic")` become `Basic`;
  - asset and class paths become their object name (`BP_Pistol_Pickup_C`).

  `--raw` keeps values as they are.
- **Nothing your items use changes by default.** Each CSV column is stored as a table column
  of the same name (`DisplayName`, `Description`, `Range`...).
  - `--map CSVCOL=COLUMN` stores a column under another name. Mapping onto a column that
    templates use changes the items, and release mode asks first.
  - `--fill COLUMN=CSVCOL` copies a CSV column into a table column only where that column
    is empty. It lists every row where the two disagree, for example a weapon whose Steam
    name differs from its in-game name.
- **Other options:** `--only`/`--skip` choose columns, `--key` picks the key column, and
  `-n` previews without saving.

The imported columns can then be used like any other:

- in templates (`{weapon.Description}`);
- in queries (`sisdefman query -w weapon.Range=LONG -f id,name,weapon.DisplayName`);
- in the GUI, which shows a weapon's row under the weapon field.

In the GUI, use **Lookup tables → Import CSV…**. Columns that look like class references
start unticked there.

## Colours

The project has a **palette** of standard colours, each with a keyword:

```jsonc
"colors": { "common": "d2d2d2", "uncommon": "5e90e0", "rare": "eb7ce9", "epic": "f08f35", "background": "292929" }
```

A colour field (`name_color`, `background_color`, or any other field ending in `_color`)
can then hold `@keyword` instead of a hex value. On export it becomes the palette's hex
value, so changing a colour in the palette changes every item that uses it. Keywords work
everywhere a colour is stored:

- on an item: `sisdefman set 110 name_color=@rare`;
- in a lookup table: `rarity.color = @rare`, used by a rule such as `"name_color": "{rarity.color}"`;
- in a kind's rule: `"background_color": "@background"`, or even `"name_color": "@{rarity}"`;
- in the dummy item.

**Converting.** `sisdefman colors convert` (or **Colors → Convert existing colours…** in the
GUI) puts every hex colour of the project into the palette and replaces it with its
keyword. The export does not change. Keywords are named after what uses the colour: a lookup
table row (`common`), a tag shared by the items using it (`rarity:epic` gives `epic`),
`background`, `white`/`black`, or else the hex value (`color_1a2b3c`). Rename any of them
afterwards; references follow.

```sh
sisdefman colors                         # the palette, with swatches and usage counts
sisdefman colors convert -n              # preview the conversion
sisdefman colors set legendary ffd700    # add or change a colour
sisdefman colors rename epic secret_rare # renames every @epic too
sisdefman colors delete legendary        # refused while in use (--force to delete anyway)
```

`check` reports keywords missing from the palette. Changing a colour only restyles the
items that use it, so release mode doesn't ask for confirmation: players keep the same items.

## Database-style commands

```sh
sisdefman query -w rarity=epic -w weapon=pistol -f id,index,name,mat_id
sisdefman query -w "name~jungle" --format json
sisdefman query -w series=crate1 -w '!flavor' --format csv > missing-flavor.csv

sisdefman set 110-114 "flavor=Painted by hand."    # a range of IDs
sisdefman set --where tags.rarity=epic marketable:=false
sisdefman set 110 --unset name_color

sisdefman table show weapon -c name,class,Range
sisdefman table set weapon pistol name=Handgun class=Basic
sisdefman table rename weapon pistol handgun        # updates every item that uses it
sisdefman schema                                    # summary of tables and kinds
sisdefman schema export -o schema.json
```

- **Conditions** (`-w`/`--where`, all must match):
  - `FIELD=VALUE`, `FIELD!=VALUE`;
  - `FIELD~TEXT` (contains, ignoring case), `FIELD!~TEXT`;
  - `FIELD<N`, `>`, `<=`, `>=`;
  - a bare `FIELD` (is set) or `!FIELD` (is empty).
- **What conditions can test:**
  - the exported fields;
  - a kind's fields;
  - a column of the table row that a `ref` field points to (`weapon.Range`);
  - one tag (`tags.rarity`);
  - `id`, `kind`, `series`, `index` and `dummy`.
- **Values:** `FIELD=TEXT` stores text; `FIELD:=JSON` stores `true`, `5`, etc.

## Series

A series is a named range of itemdefids (`first_id` to `last_id`). Every definition in the
range belongs to the series. An item's **index** is its position in the range, counting
from 1 at the lowest ID and skipping unused IDs. The part of the range after the last item
is free space for new items.

From definitions laid out like the table below, `import` sets up the following:

| Found | Becomes |
| --- | --- |
| Items tagged `series:crate1` at IDs 110-134 | series `crate1`, items #1-#25 |
| Dummy items at 135-143 | IDs the series has used (`allocated_through: 143`), still exported as dummies |
| The next unrelated ID, 197 | the end of the series' range (`last_id: 196`) |
| `Contains ... from the First Series.` in crate 1 | the display name `First Series` |
| The list of names in crate 1's description | `{contents}`, generated from every item not tagged `rarity:epic` |
| Generators 101-104, whose bundles hold exactly one rarity | rules such as `rarity:common`, so new items join them automatically |

If a crate's list is out of date, the import prints the difference.

In prerelease mode the items of a series don't have to be consecutive. The import still
sets up the series and warns about each discontinuity:

- other definitions between its items (they join the series; `check` then reports that
  they lack its `series:` tag);
- unused IDs inside it (exported as dummy items);
- an item tagged with the series but far from the rest (left out; add it with
  `sisdefman series set KEY --container ID` if it is the crate).

In release mode such a series is not set up automatically; create it with
`sisdefman series new`.

### What is generated on export

| What | Stored in the project as | Exported as |
| --- | --- | --- |
| Items of a kind | the kind's fields and any overrides | the kind's rules applied |
| Descriptions of series items | the plain description (or the kind's rule) | the series line added (below) |
| Descriptions of containers (crates) | text containing `{contents}` (or `{contents_no_secret}` / `{contents_secret}`) | the names of the series' items, one per line: `{contents}` leaves out items with an `exclude` tag, `{contents_no_secret}` leaves out the secret rares, `{contents_secret}` lists only the secret rares; `{count}`, `{count_no_secret}`, `{count_secret}` and `{series_name}` filled in |
| Bundles of the generators listed in a series' `generators` | a tag rule, e.g. `rarity:common` | every series item with that tag, in series order |
| Unused IDs from `first_id` to `allocated_through` | nothing | dummy items built from `settings.dummy_item` |

Everything else is exported exactly as stored, including field order. Steam keeps its
current definition for any itemdefid that an upload leaves out, so an ID a series stops
using is exported as a dummy item instead of being dropped.

### The series line

The default is `{description}\n\n{series_name} #{index}`. Besides the item's own fields,
the template can use:

| Field | Value |
| --- | --- |
| `{description}` | the item's description (derived or stored) |
| `{series_name}` / `{series}` | display name / key of the series |
| `{index}` / `{count}` | position in the series / number of items in it (`{index:03d}` pads to 3 digits) |
| `{count_no_secret}` / `{count_secret}` | number of items without the secret rares / number of secret rares |
| `{name}` / `{itemdefid}` | the item's name / ID |
| `{tags[rarity]}` | the value of one of the item's tags |

```sh
sisdefman template '{description}\n\n{series_name} #{index}'   # \n is a line break
sisdefman series set crate2 --template '{description}\n\n{series_name} - {index} of {count}'
sisdefman series set crate2 --template ''                       # back to the global one
```

### Secret rares and item counts

Each series knows which of its items are secret rares, so templates can count with or without
them:

| Property | Series line and crates | Kind templates |
| --- | --- | --- |
| every item, secret rares included | `{count}` | `{series.count}` |
| items without the secret rares | `{count_no_secret}` | `{series.count_no_secret}` |
| secret rares only | `{count_secret}` | `{series.count_secret}` |

By default the secret rares are the items the series' crate leaves out of its list (its
`exclude` tags, e.g. `rarity:epic`). To set them explicitly, use
`sisdefman series set crate1 --secret rarity:epic`, or the Series setup page; `--secret ""`
goes back to the default. `sisdefman list` shows the split, e.g. `25 items (15 + 10 secret)`.

A crate's description can use the counts, and list the items with or without the secret
rares:

| Keyword | Lists |
| --- | --- |
| `{contents}` | every item except those with one of the container's `exclude` tags |
| `{contents_no_secret}` | every item except the secret rares |
| `{contents_secret}` | only the secret rares |

```text
Contains one of {count_no_secret} appearances from the {series_name}.

{contents_no_secret}

...or one of {count_secret} Secret Rare Special Appearances!
```

### Creating a series

```sh
sisdefman series new crate3 --name "Third Series" --copy-from crate2 -n   # preview
sisdefman series new crate3 --name "Third Series" --copy-from crate2
sisdefman series new crate3 --name "Third Series" --first-id 310 --last-id 396   # empty
```

The ID range defaults to the next block after the last series, of the same size (crate 2 at
210-296 gives 310-396). With `--copy-from`, the new series gets a copy of the other series'
setup, but none of its items:

- **What is copied:** its containers and generators, and the other definitions in its ID
  block (e.g. 200-299): exchange entry points, trade-up recipes, root and overlay
  generators. Definitions that something outside the block also uses (a shared tag
  generator, say) are not copied unless you choose them with `--copy-ids`.
- **New IDs:** each copy keeps its place in the block (205 → 305). A container gets the
  next free ID after the original (crate 2 → 3); `--new-id 2=50` picks another one.
- **What changes in the copies:**
  - references between them (`bundle`, `exchange`, `tag_generators`);
  - `series:crate2` in tags and exchange recipes becomes `series:crate3`;
  - text: `Crate 2` → `Crate 3`, `crate_2` → `crate_3`, `Second Series` → `Third Series`
    and `Second` → `Third` are worked out from the keys and names; add more with
    `--replace FIND=REPLACE`.
- **What is set up:** the copied containers and generators become the new series'
  containers and generators. Its description template and secret-rare rule are copied too.

Nothing that already exists changes, so this needs no confirmation in release mode. A
definition outside the block that should also grant the new crate (a playtime drop, say) is
left for you to edit.

In the GUI, **+ New series** fills in the next key and ID block, and starts from a copy of
the last series. The **Start from** card lists every definition it will copy, with its new
ID. Untick definitions, change their new IDs or edit the text replacements there, or choose
**An empty series** to set up containers and generators yourself.

### Adding, inserting, moving and removing items

```sh
sisdefman add crate1 --kind skin weapon=rifle finish=Gold mat_id=gold rarity=common   # append: safe
sisdefman add crate1 --like 121 finish=Rust mat_id=rust                               # copy, then change
sisdefman add crate1 --after 116 --from new_item.json                                 # insert: renumbers
sisdefman move 124 --before 122
sisdefman remove 118              # later items move down to close the gap
sisdefman remove 118 --no-shift   # leave a gap (exported as a dummy)
```

- **Item fields.** Items are given as `FIELD=VALUE` / `FIELD:=JSON`, with `--kind`, as a
  copy of another item (`--like ID`), or from a JSON file with one item or a list
  (`--from FILE`).
- **IDs and tags.** The itemdefid comes from the item's position. A plain item gets the
  `series:` tag set for you; a kind's tags rule normally includes `series:{series}`.
- **Renumbering.** Only the consecutive run of items after the change is renumbered. An
  unused ID absorbs the shift.
- **Removing referenced items.** `remove` refuses while another definition refers to the
  item. Generators with a series rule don't count.
- **Previewing.** Add `-n` / `--dry-run` to any change to see its effect without saving.
- **Series size.** When a series runs out of room, raise its limit with
  `sisdefman series set crate1 --last-id N`. The IDs after it must be free.

## Prerelease and release mode

Steam inventories store itemdefids. If the definition behind an ID changes to a different
item, every player who owns that ID ends up with the new item. Inserting, moving or removing
series items does exactly that, and so does renaming a live item through a table or a rule.

- **`prerelease`** (the default) lets these changes happen freely and reports what was
  renumbered.
- **`release`** records which definitions are live, meaning every item players can own.
  Any change that would alter one of them then stops with a warning listing every affected
  ID (`#117 'Rifle | Camo' -> 'Pistol | Stripes'`). To go ahead, you must type
  `CHANGE PLAYER INVENTORIES` (in the terminal or the GUI) or pass
  `--accept-inventory-changes`. Without a terminal and without the flag, the change is
  refused.

Release mode checks each change twice:

1. **At the change itself.** This covers `add`, `move`, `remove`, `set`, `table`, `schema
   import`, `adopt`, and every edit in the GUI.
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
it stays protected. In release mode, appending to a series skips such retired IDs and uses
the next free one. Appending is always safe.

## All commands

| Command | Does |
| --- | --- |
| `import FILE ... [--replace]` | create the project, or add more Steam files to it |
| `export [-o FILE] [--mark-live]` | write every definition to one Steam-ready file |
| `gui [--choose] [--port N] [--no-browser]` | open the graphical editor (with the project chooser if there is no project here) |
| `check` | validate the project (schema, templates, references, series, colours...) |
| `list [SERIES] [--all]` / `show ID [--stored]` | series overview / one definition as exported |
| `query` / `set` | find and change items (see above) |
| `add` / `move` / `remove` | change a series (see above) |
| `adopt KIND IDS` / `detach IDS` | convert items to a kind and back |
| `schema [export \| import FILE]` | show, export or import tables and kinds |
| `table [show \| new \| set \| delete \| rename \| import]` | edit lookup tables; import CSV reference data |
| `series [new \| set]` | show, create (empty or `--copy-from` another series) or configure series (range, name, template, secret rares, containers, generators) |
| `colors [set \| rename \| delete \| convert]` | show or edit the colour palette; convert hex colours to it |
| `template [TEXT]` | show or set the series line |
| `mode [prerelease \| release]` / `mark-live` / `diff [--against FILE]` | release protection |

## The project file

```jsonc
{
    "sisdefman": 3,
    "appid": 480,
    "mode": "prerelease",
    "settings": {
        "description_template": "{description}\n\n{series_name} #{index}",
        "dummy_item": { "type": "item", "name": "Dummy Item #{itemdefid}", "name_color": "@common", ... }
    },
    "colors": { "common": "d2d2d2", "background": "292929", ... },   // the palette (see above)
    "tables": { ... },                    // lookup tables (see above)
    "kinds": { ... },                     // item kinds (see above)
    "series": {
        "crate1": {
            "name": "First Series",
            "first_id": 110,
            "last_id": 196,               // the series may grow up to here
            "allocated_through": 143,     // highest ID it has used; unused ones export as dummies
            "secret": "rarity:epic",      // optional: which items are secret rares
            "containers": { "1": { "exclude": ["rarity:epic"] } },
            "generators": { "101": "rarity:common", "102": "rarity:uncommon", "103": "rarity:rare", "104": "rarity:epic" }
        }
    },
    "items": [
        {"itemdefid": 1, "type": "item", "name": "First Crate", "description": "...\n\n{contents}\n\n..."},
        {"itemdefid": 110, "kind": "skin", "weapon": "pistol", "finish": "Red", "mat_id": "red", "rarity": "common"}
    ],
    "live": null                          // release mode: { "recorded_at": ..., "items": { "110": "Pistol | Red", ... } }
}
```

- An item without a `kind` holds its Steam definition as it is.
- An item with a `kind` holds the kind's fields plus any overrides.
- Series items store their plain description, and crates store `{contents}` where the item
  list goes.
- Dummy items inside a series' range are not stored.
- The project file can be edited by hand. `sisdefman check` reports mistakes.
- Older project files (versions 1 and 2) are upgraded automatically.

## Development

```sh
python -m unittest discover -s tests -t .
```

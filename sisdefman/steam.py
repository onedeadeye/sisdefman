"""Helpers for reading and rewriting fields of the Steam Inventory Service
item definition format.

Only the parts sisdefman needs are modelled here: tags, and the three
properties that refer to other item definitions (``bundle``, ``exchange``
and ``tag_generators``).
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

ITEM_TYPES = ("item", "bundle", "generator", "playtimegenerator", "tag_generator")

# Properties whose values contain itemdefid references.
REFERENCE_FIELDS = ("bundle", "exchange", "tag_generators")

_ID_QTY = re.compile(r"^(\d+)([xX]\d+)?$")
_ID_ONLY = re.compile(r"^\d+$")
_HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")


class SyntaxProblem(ValueError):
    """A reference field could not be parsed."""


# --------------------------------------------------------------------- tags


def parse_tags(value) -> List[Tuple[str, str]]:
    """Split ``"cat:value;cat:value"`` into ``[(cat, value), ...]``."""
    if not value or not isinstance(value, str):
        return []
    out = []
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        cat, _, val = part.partition(":")
        out.append((cat.strip(), val.strip()))
    return out


def format_tags(tags: Iterable[Tuple[str, str]]) -> str:
    return ";".join(f"{cat}:{val}" for cat, val in tags)


def tag_strings(item: dict) -> List[str]:
    """The item's tags as ``"cat:value"`` strings, in their original order."""
    return [f"{cat}:{val}" for cat, val in parse_tags(item.get("tags"))]


def has_tags(item: dict, wanted: Iterable[str]) -> bool:
    """True if the item carries every ``"cat:value"`` tag in ``wanted``."""
    own = set(tag_strings(item))
    return all(t in own for t in wanted)


def tag_values(item: dict, category: str) -> List[str]:
    return [val for cat, val in parse_tags(item.get("tags")) if cat == category]


def set_single_tag(item: dict, category: str, value: str) -> bool:
    """Make ``category:value`` the only tag of that category on the item,
    keeping the position of the first existing tag of that category.
    Returns True if the tags changed."""
    tags = parse_tags(item.get("tags"))
    out: List[Tuple[str, str]] = []
    placed = False
    for cat, val in tags:
        if cat == category:
            if not placed:
                out.append((category, value))
                placed = True
            continue
        out.append((cat, val))
    if not placed:
        # Keep "type:" first if present, as the existing definitions do.
        pos = 1 if out and out[0][0] == "type" else 0
        out.insert(pos, (category, value))
    new = format_tags(out)
    if new != (item.get("tags") or ""):
        item["tags"] = new
        return True
    return False


def parse_tag_rule(rule) -> List[str]:
    """A rule is a tag string (``"rarity:common"`` or ``"a:b;c:d"``) or a list
    of such strings; all tags must match."""
    if isinstance(rule, str):
        return tag_strings({"tags": rule})
    if isinstance(rule, list):
        out: List[str] = []
        for part in rule:
            out.extend(tag_strings({"tags": part}))
        return out
    raise SyntaxProblem(f"tag rule must be a string or list, not {type(rule).__name__}")


# --------------------------------------------------------------- references


def _map_tokens(text: str, sep: str, token_fn: Callable[[str], str]) -> str:
    if text.strip() == "":
        return text
    return sep.join(token_fn(tok.strip()) for tok in text.split(sep))


def _rewrite(field: str, value: str, fn: Callable[[int], Optional[int]]) -> str:
    """Apply ``fn`` to every itemdefid referenced in ``value``.

    ``fn`` returns the replacement id (or the same id to leave it alone).
    Raises SyntaxProblem if the value cannot be parsed.
    """

    def id_qty(tok: str) -> str:
        m = _ID_QTY.match(tok)
        if not m:
            raise SyntaxProblem(f"{field}: cannot parse {tok!r} (expected ITEMDEFID or ITEMDEFIDxN)")
        return f"{fn(int(m.group(1)))}{m.group(2) or ''}"

    if field == "bundle":
        return _map_tokens(value, ";", id_qty)

    if field == "exchange":

        def material(tok: str) -> str:
            if ":" in tok:
                # tag-based material, e.g. "rarity:common*10"
                if "*" not in tok:
                    raise SyntaxProblem(f"exchange: tag material {tok!r} is missing a *COUNT")
                return tok
            return id_qty(tok)

        return _map_tokens(value, ";", lambda recipe: _map_tokens(recipe, ",", material))

    if field == "tag_generators":

        def only_id(tok: str) -> str:
            if not _ID_ONLY.match(tok):
                raise SyntaxProblem(f"tag_generators: cannot parse {tok!r} (expected ITEMDEFID)")
            return str(fn(int(tok)))

        return _map_tokens(value, ";", only_id)

    raise KeyError(field)


def references(item: dict) -> List[Tuple[str, int]]:
    """Every ``(field, itemdefid)`` reference made by the item.

    Raises SyntaxProblem for unparsable fields.
    """
    found: List[Tuple[str, int]] = []
    for field in REFERENCE_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value.strip():

            def collect(i: int, _field=field) -> int:
                found.append((_field, i))
                return i

            _rewrite(field, value, collect)
    return found


def remap_references(item: dict, mapping: Dict[int, int]) -> List[str]:
    """Rewrite references according to ``mapping`` (old id -> new id).

    All replacements happen simultaneously, so swaps are safe. Returns the
    names of the fields that changed.
    """
    changed = []
    for field in REFERENCE_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            new = _rewrite(field, value, lambda i: mapping.get(i, i))
            if new != value:
                item[field] = new
                changed.append(field)
    return changed


def bundle_is_plain_list(value) -> Optional[List[int]]:
    """If ``value`` is a bundle of unweighted ids (``"1;2;3"``), return them."""
    if not isinstance(value, str) or not value.strip():
        return None
    ids = []
    for tok in value.split(";"):
        tok = tok.strip()
        if not _ID_ONLY.match(tok):
            return None
        ids.append(int(tok))
    return ids


def is_hex_color(value) -> bool:
    return isinstance(value, str) and bool(_HEX_COLOR.match(value))

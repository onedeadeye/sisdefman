"""JSON output in the style of Steam's item definition files (four-space
indent), with short lists of plain values kept on one line."""

from __future__ import annotations

import json

INDENT = 4
WIDTH = 100


def dumps(obj) -> str:
    return _fmt(obj, 0) + "\n"


def _fmt(o, level: int) -> str:
    pad = " " * (INDENT * level)
    inner = " " * (INDENT * (level + 1))
    if isinstance(o, dict):
        if not o:
            return "{}"
        parts = [f"{inner}{json.dumps(str(k), ensure_ascii=False)}: {_fmt(v, level + 1)}" for k, v in o.items()]
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    if isinstance(o, list):
        if not o:
            return "[]"
        if all(not isinstance(x, (dict, list)) for x in o):
            one = "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in o) + "]"
            if len(inner) + len(one) <= WIDTH:
                return one
        parts = [inner + _fmt(x, level + 1) for x in o]
        return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
    return json.dumps(o, ensure_ascii=False)

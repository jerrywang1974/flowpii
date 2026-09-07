from __future__ import annotations

import re
import unicodedata


_WS_RE = re.compile(r"[\s\u3000]+")


def collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text.strip())


def normalize_method(method: str) -> str:
    """Normalize transfer method for comparison (not for display)."""
    m = collapse_ws(method)
    key = m.lower().replace("－", "-").replace("—", "-")
    aliases = {
        "e-mail": "email",
        "email": "email",
        "e mail": "email",
        "line": "line",
        "fax": "fax",
        "傳真": "fax",
    }
    return aliases.get(key, key)


def normalize_cell(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize multiple spaces but keep newlines (node notes)
    parts = [collapse_ws(p) for p in text.split("\n")]
    return "\n".join(p for p in parts if p)


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    out = {k: normalize_cell(row.get(k)) for k in ("A", "B", "G", "H", "AF", "AG", "AH")}
    # Method compare key stored separately by callers if needed
    return out


def row_match_key(row: dict[str, str]) -> tuple[str, ...]:
    n = normalize_row(row)
    return (
        n["A"],
        n["B"],
        n["G"],
        n["H"],
        n["AF"],
        n["AG"],
        normalize_method(n["AH"]),
    )

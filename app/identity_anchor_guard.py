from __future__ import annotations

import re
from typing import Any


STRICT_CITY_RE = re.compile(
    r"(?<![А-Яа-яЁё])(?:г\.\s*|г\s+|город\s+)([А-ЯЁ][А-Яа-яЁё-]{2,40})(?![А-Яа-яЁё-])",
    re.IGNORECASE,
)


def strict_cities_from_evidence(text: str, *, limit: int = 3) -> tuple[str, ...]:
    """Extract city names only after an explicit city marker.

    Unlike the legacy permissive pattern, this cannot interpret the leading
    Cyrillic `Г` in words such as `Главная` or `Гигиена` as abbreviation `г.`.
    """
    cities: list[str] = []
    seen: set[str] = set()
    for match in STRICT_CITY_RE.finditer(text):
        raw = match.group(1).strip(" ,.;")
        value = raw[:1].upper() + raw[1:]
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cities.append(value)
        if len(cities) >= limit:
            break
    return tuple(cities)


def guard_identity_anchors(anchors: Any, evidence_text: str):
    """Replace permissively parsed city anchors with strictly evidenced cities."""
    from app.entity_resolution.service import _valid_inn, _valid_ogrn
    strict_cities = strict_cities_from_evidence(evidence_text)
    inn = getattr(anchors, "inn", None)
    ogrn = getattr(anchors, "ogrn", None)
    cls = anchors.__class__
    return cls(
        domain=getattr(anchors, "domain", None),
        legal_name=getattr(anchors, "legal_name", None),
        inn=inn if inn and _valid_inn(inn) else None,
        ogrn=ogrn if ogrn and _valid_ogrn(ogrn) else None,
        cities=strict_cities,
        phones=tuple(getattr(anchors, "phones", ()) or ()),
    )

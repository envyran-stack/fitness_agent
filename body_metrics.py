"""신체 측정 항목 정의 및 포맷 헬퍼."""

from __future__ import annotations

import re
import unicodedata

DEFAULT_BODY_METRIC_FIELDS: list[dict[str, str | bool]] = [
    {"key": "weight_kg", "label": "몸무게", "unit": "kg", "builtin": True},
    {"key": "body_fat_pct", "label": "체지방", "unit": "%", "builtin": True},
    {"key": "muscle_mass_kg", "label": "근육량", "unit": "kg", "builtin": True},
]

RESERVED_BODY_KEYS = frozenset({"id", "date"})


def slugify_metric_key(label: str) -> str:
    """항목 이름으로 저장용 key 생성."""
    text = unicodedata.normalize("NFKC", label.strip())
    slug = re.sub(r"\s+", "_", text)
    slug = re.sub(r"[^\w가-힣]+", "", slug, flags=re.UNICODE)
    if not slug:
        raise ValueError("항목 이름을 입력해 주세요.")
    if slug[0].isdigit():
        slug = f"m_{slug}"
    return slug[:40]


def format_metric_value(value: float, unit: str) -> str:
    text = f"{value:g}"
    return f"{text}{unit}" if unit else text


def format_metric_change(first: float, last: float, unit: str) -> str:
    delta = last - first
    sign = f"{delta:+.1f}" if isinstance(delta, float) else f"{delta:+d}"
    return f"{sign}{unit}" if unit else sign


def field_label_with_unit(field: dict) -> str:
    unit = str(field.get("unit") or "").strip()
    label = str(field["label"])
    return f"{label} ({unit})" if unit else label


def summarize_body_entry(entry: dict, fields: list[dict]) -> str:
    parts: list[str] = []
    for field in fields:
        key = field["key"]
        if key in entry and entry[key] is not None:
            parts.append(
                f"{field['label']} {format_metric_value(float(entry[key]), str(field.get('unit') or ''))}"
            )
    return ", ".join(parts) if parts else "(측정값 없음)"

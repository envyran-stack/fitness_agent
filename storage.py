"""Fitness data persistence (JSON)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from body_metrics import DEFAULT_BODY_METRIC_FIELDS, RESERVED_BODY_KEYS, slugify_metric_key

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_FILE = DATA_DIR / "fitness.json"

DEFAULT_DATA: dict[str, Any] = {
    "body_metric_fields": [dict(field) for field in DEFAULT_BODY_METRIC_FIELDS],
    "body_metrics": [],
    "workouts": [],
}


def _ensure_data_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text(
            json.dumps(DEFAULT_DATA, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _ensure_record_ids(data: dict[str, Any]) -> bool:
    changed = False
    for key in ("body_metrics", "workouts"):
        for entry in data.get(key, []):
            if not entry.get("id"):
                entry["id"] = _new_id()
                changed = True
    return changed


def _ensure_body_metric_fields(data: dict[str, Any]) -> bool:
    if data.get("body_metric_fields"):
        return False
    data["body_metric_fields"] = [dict(field) for field in DEFAULT_BODY_METRIC_FIELDS]
    return True


def load_data() -> dict[str, Any]:
    _ensure_data_file()
    with DATA_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    changed = _ensure_body_metric_fields(data)
    if _ensure_record_ids(data):
        changed = True
    if changed:
        save_data(data)
    return data


def get_body_metric_fields() -> list[dict[str, Any]]:
    data = load_data()
    return list(data.get("body_metric_fields", DEFAULT_BODY_METRIC_FIELDS))


def _field_keys(fields: list[dict[str, Any]] | None = None) -> set[str]:
    fields = fields or get_body_metric_fields()
    return {str(field["key"]) for field in fields}


def add_body_metric_field(label: str, unit: str = "") -> dict[str, Any]:
    label = label.strip()
    if not label:
        raise ValueError("항목 이름을 입력해 주세요.")

    data = load_data()
    fields = data.setdefault("body_metric_fields", [])
    key = slugify_metric_key(label)
    existing_keys = {str(field["key"]) for field in fields}
    if key in existing_keys:
        suffix = 2
        base = key
        while f"{base}_{suffix}" in existing_keys:
            suffix += 1
        key = f"{base}_{suffix}"

    field = {"key": key, "label": label, "unit": unit.strip(), "builtin": False}
    fields.append(field)
    save_data(data)
    return field


def remove_body_metric_field(key: str) -> bool:
    data = load_data()
    fields = data.get("body_metric_fields", [])
    target = None
    for field in fields:
        if field["key"] == key:
            target = field
            break
    if target is None:
        return False
    if target.get("builtin"):
        raise ValueError("기본 항목(몸무게·체지방·근육량)은 삭제할 수 없습니다.")

    data["body_metric_fields"] = [field for field in fields if field["key"] != key]
    for entry in data.get("body_metrics", []):
        entry.pop(key, None)
    save_data(data)
    return True


def _sanitize_body_metrics(metrics: dict[str, float], fields: list[dict[str, Any]] | None = None) -> dict[str, float]:
    allowed = _field_keys(fields)
    cleaned: dict[str, float] = {}
    for key, value in metrics.items():
        if key not in allowed or key in RESERVED_BODY_KEYS:
            continue
        if value is None:
            continue
        cleaned[key] = float(value)
    if not cleaned:
        raise ValueError("저장할 측정값이 없습니다.")
    return cleaned


def save_data(data: dict[str, Any]) -> None:
    _ensure_data_file()
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def today_str() -> str:
    return date.today().isoformat()


def parse_date(value: str | None) -> str:
    if not value or not str(value).strip():
        return today_str()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return today_str()


def _find_index(records: list[dict], record_id: str) -> int | None:
    for i, entry in enumerate(records):
        if entry.get("id") == record_id:
            return i
    return None


def add_body_metric(
    metrics: dict[str, float],
    record_date: str | None = None,
) -> dict[str, Any]:
    data = load_data()
    fields = data.get("body_metric_fields", DEFAULT_BODY_METRIC_FIELDS)
    cleaned = _sanitize_body_metrics(metrics, fields)
    entry: dict[str, Any] = {
        "id": _new_id(),
        "date": parse_date(record_date),
        **cleaned,
    }
    data["body_metrics"].append(entry)
    data["body_metrics"].sort(key=lambda x: x["date"])
    save_data(data)
    return entry


def add_workout(
    exercise: str,
    sets: int,
    reps: int,
    weight_kg: float = 0.0,
    record_date: str | None = None,
) -> dict[str, Any]:
    data = load_data()
    entry = {
        "id": _new_id(),
        "date": parse_date(record_date),
        "exercise": exercise.strip(),
        "sets": int(sets),
        "reps": int(reps),
        "weight_kg": float(weight_kg),
    }
    data["workouts"].append(entry)
    data["workouts"].sort(key=lambda x: (x["date"], x["exercise"]))
    save_data(data)
    return entry


def update_body_metric(
    record_id: str,
    metrics: dict[str, float],
    record_date: str | None = None,
) -> dict[str, Any] | None:
    data = load_data()
    idx = _find_index(data["body_metrics"], record_id)
    if idx is None:
        return None

    fields = data.get("body_metric_fields", DEFAULT_BODY_METRIC_FIELDS)
    cleaned = _sanitize_body_metrics(metrics, fields)
    entry = data["body_metrics"][idx]
    entry["date"] = parse_date(record_date) if record_date else entry["date"]
    for key in _field_keys(fields):
        entry.pop(key, None)
    entry.update(cleaned)
    data["body_metrics"].sort(key=lambda x: x["date"])
    save_data(data)
    return entry


def update_workout(
    record_id: str,
    exercise: str,
    sets: int,
    reps: int,
    weight_kg: float = 0.0,
    record_date: str | None = None,
) -> dict[str, Any] | None:
    data = load_data()
    idx = _find_index(data["workouts"], record_id)
    if idx is None:
        return None

    entry = data["workouts"][idx]
    entry["date"] = parse_date(record_date) if record_date else entry["date"]
    entry["exercise"] = exercise.strip()
    entry["sets"] = int(sets)
    entry["reps"] = int(reps)
    entry["weight_kg"] = float(weight_kg)
    data["workouts"].sort(key=lambda x: (x["date"], x["exercise"]))
    save_data(data)
    return entry


def delete_body_metric(record_id: str) -> bool:
    data = load_data()
    idx = _find_index(data["body_metrics"], record_id)
    if idx is None:
        return False
    data["body_metrics"].pop(idx)
    save_data(data)
    return True


def delete_workout(record_id: str) -> bool:
    data = load_data()
    idx = _find_index(data["workouts"], record_id)
    if idx is None:
        return False
    data["workouts"].pop(idx)
    save_data(data)
    return True


def get_records_within_days(days: int = 30) -> dict[str, list]:
    return get_records_filtered(days=days)


def get_records_filtered(
    *,
    days: int | None = 30,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> dict[str, list]:
    data = load_data()

    def _entry_date(entry: dict) -> date | None:
        try:
            return date.fromisoformat(entry["date"])
        except (KeyError, ValueError):
            return None

    def _to_date(value: str | date | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    start = _to_date(start_date)
    end = _to_date(end_date)
    if start and end and start > end:
        start, end = end, start

    if days is not None and start is None and end is None:
        cutoff = date.today().toordinal() - max(days - 1, 0)

        def _within(entry: dict) -> bool:
            entry_dt = _entry_date(entry)
            return entry_dt is not None and entry_dt.toordinal() >= cutoff
    else:

        def _within(entry: dict) -> bool:
            entry_dt = _entry_date(entry)
            if entry_dt is None:
                return False
            if start and entry_dt < start:
                return False
            if end and entry_dt > end:
                return False
            return True

    return {
        "body_metrics": [e for e in data["body_metrics"] if _within(e)],
        "workouts": [e for e in data["workouts"] if _within(e)],
    }

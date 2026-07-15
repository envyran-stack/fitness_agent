"""Fitness Agent — Streamlit 웹앱 (경량·안정 버전)."""

from __future__ import annotations

import os

# pyarrow의 기본 mimalloc 할당자가 최신 macOS의 스레드 로컬 스토리지 구현과 충돌해
# 세그폴트(Connection error로 나타남)를 유발하는 문제가 있어, pyarrow를 import하기 전에
# 시스템 malloc을 쓰도록 강제한다. (run_web.sh에서도 동일하게 설정하지만, 다른 방식으로
# 앱을 실행하는 경우를 대비해 여기서도 방어적으로 설정)
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import streamlit as st
from dotenv import load_dotenv

from agent import (
    get_executor,
    is_email_request,
    run_agent,
    send_fitness_report_direct,
)
from langchain_core.messages import AIMessage, HumanMessage
from body_metrics import field_label_with_unit, format_metric_change, format_metric_value, summarize_body_entry
from storage import (
    add_body_metric,
    add_body_metric_field,
    delete_body_metric,
    delete_workout,
    get_body_metric_fields,
    get_records_filtered,
    load_data,
    parse_date,
    remove_body_metric_field,
    update_body_metric,
    update_workout,
)
from tools import analyze_fitness_trends, save_workout
from smtp_config import default_report_recipient, smtp_config_status, test_smtp_connection

load_dotenv(Path(__file__).resolve().parent / ".env")

st.set_page_config(page_title="Fitness Agent", page_icon="💪", layout="wide")

# 모바일 브라우저 보정 + 대시보드 카드 스타일
st.markdown(
    """
    <style>
    @media (max-width: 768px) {
      .block-container { padding-top: 0.75rem; padding-bottom: 2rem; }
      [data-testid="stSidebar"] { min-width: 16rem; }
      div[data-testid="column"] .stButton button { width: 100%; }
    }

    .dash-hero {
      background: linear-gradient(135deg, #ff6b6b 0%, #ff9d5c 100%);
      color: #fff;
      padding: 20px 26px;
      border-radius: 16px;
      margin-bottom: 20px;
      box-shadow: 0 4px 14px rgba(255, 107, 107, 0.25);
    }
    .dash-hero h2 { margin: 0 0 4px 0; font-size: 1.4rem; color: #fff; }
    .dash-hero p { margin: 0; opacity: 0.95; font-size: 0.92rem; color: #fff; }

    .dash-section-title {
      font-weight: 700;
      font-size: 1.05rem;
      margin: 22px 0 10px 0;
      color: #fafafa;
    }

    [data-testid="stMetric"] {
      background: #262730;
      border: 1px solid #3b3d47;
      border-radius: 14px;
      padding: 14px 16px 10px 16px;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25);
    }
    [data-testid="stMetricLabel"] { font-weight: 600; color: #c9cdd3; }
    [data-testid="stMetricValue"] { color: #fafafa; }

    div[data-testid="stChatMessage"] {
      border-radius: 14px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

EXERCISE_OPTIONS = [
    "벤치프레스", "스쿼트", "데드리프트", "숄더프레스",
    "랫풀다운", "러닝", "사이클", "기타",
]

MAX_PYTHON = (3, 12)

METRIC_INPUT_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "weight_kg": (30.0, 200.0, 70.0, 0.1),
    "body_fat_pct": (3.0, 60.0, 20.0, 0.1),
    "muscle_mass_kg": (10.0, 80.0, 30.0, 0.1),
}


def metric_input_bounds(field: dict) -> tuple[float, float, float, float]:
    key = str(field["key"])
    return METRIC_INPUT_BOUNDS.get(key, (0.0, 999.0, 0.0, 0.1))


def check_runtime() -> None:
    ver = sys.version_info[:2]
    if ver > MAX_PYTHON:
        st.error(
            f"Python {ver[0]}.{ver[1]} 미지원. `conda activate day15` 후 `./run_web.sh` 실행."
        )
        st.stop()
    if not Path(__file__).resolve().parent.joinpath(".env").exists():
        st.warning("fitness_agent/.env 없음 — API 키·SMTP 설정 필요.")


@st.cache_resource
def cached_executor():
    return get_executor()


def init_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []


def render_body_field_manager() -> None:
    with st.expander("측정 항목 관리", expanded=False):
        st.caption("몸무게·체지방·근육량 외에 허리둘레, 수면 시간 등 원하는 항목을 추가할 수 있습니다.")
        fields = get_body_metric_fields()
        for field in fields:
            col1, col2 = st.columns([4, 1])
            suffix = " · 기본" if field.get("builtin") else ""
            col1.markdown(f"**{field['label']}** ({field.get('unit') or '-'}){suffix}")
            if not field.get("builtin") and col2.button("삭제", key=f"del_field_{field['key']}"):
                try:
                    remove_body_metric_field(str(field["key"]))
                    st.success(f"'{field['label']}' 항목을 삭제했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        st.markdown("##### 새 항목 추가")
        c1, c2, c3 = st.columns([2, 1, 1])
        new_label = c1.text_input("항목 이름", placeholder="예: 허리둘레", key="new_metric_label")
        new_unit = c2.text_input("단위", placeholder="cm", key="new_metric_unit")
        if c3.button("추가", key="add_metric_field", use_container_width=True):
            if not new_label.strip():
                st.error("항목 이름을 입력해 주세요.")
            else:
                try:
                    field = add_body_metric_field(new_label, new_unit)
                    st.success(f"'{field['label']}' 항목을 추가했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def render_body_input() -> None:
    st.subheader("신체 정보 기록")
    render_body_field_manager()
    fields = get_body_metric_fields()

    record_date = st.date_input("측정일", value=date.today())
    metrics: dict[str, float] = {}
    cols = st.columns(2)
    for index, field in enumerate(fields):
        min_v, max_v, default, step = metric_input_bounds(field)
        with cols[index % 2]:
            metrics[str(field["key"])] = st.number_input(
                field_label_with_unit(field),
                min_v,
                max_v,
                default,
                step,
                key=f"body_input_{field['key']}",
            )

    if st.button("신체 정보 저장", type="primary", key="save_body"):
        try:
            entry = add_body_metric(metrics, record_date.isoformat())
            st.success(
                f"신체 정보 저장 완료 ({entry['date']}): {summarize_body_entry(entry, fields)}"
            )
        except Exception as exc:
            st.error(str(exc))


def render_workout_input() -> None:
    st.subheader("운동 기록")
    col1, col2 = st.columns(2)
    with col1:
        record_date = st.date_input("운동일", value=date.today(), key="workout_date")
        exercise_choice = st.selectbox("운동 종목", EXERCISE_OPTIONS)
        custom = st.text_input("기타 종목") if exercise_choice == "기타" else ""
    with col2:
        sets = st.number_input("세트", 1, 20, 4)
        reps = st.number_input("횟수", 1, 100, 10)
        weight = st.number_input("중량 (kg)", 0.0, 300.0, 0.0, 2.5)

    exercise = custom.strip() if exercise_choice == "기타" else exercise_choice

    if st.button("운동 저장", type="primary", key="save_workout"):
        if exercise_choice == "기타" and not exercise:
            st.error("운동 종목을 입력해 주세요.")
            return
        try:
            msg = save_workout.invoke({
                "exercise": exercise,
                "sets": int(sets),
                "reps": int(reps),
                "weight_kg": weight,
                "record_date": record_date.isoformat(),
            })
            st.success(msg)
        except Exception as exc:
            st.error(str(exc))


def body_label(entry: dict) -> str:
    fields = get_body_metric_fields()
    return f"{entry['date']} | {summarize_body_entry(entry, fields)}"


def workout_label(entry: dict) -> str:
    weight = f" {entry['weight_kg']}kg" if entry.get("weight_kg") else ""
    return (
        f"{entry['date']} | {entry['exercise']} | "
        f"{entry['sets']}세트 x {entry['reps']}회{weight}"
    )


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def sort_by_date(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda row: row["date"])


def _format_axis_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%m/%d")
    except ValueError:
        return value


def _render_empty_chart_card(title: str, message: str) -> None:
    st.markdown(
        f"""
        <div style="border:1px solid #3b3d47;border-radius:12px;padding:10px;
                    background:#262730;height:100%;box-sizing:border-box;
                    min-height:150px;display:flex;flex-direction:column;">
          <div style="font-weight:600;margin-bottom:4px;color:#fafafa;">{title}</div>
          <div style="color:#9a9ea8;font-size:0.85rem;flex:1;display:flex;
                      align-items:center;">{message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_svg_line_chart(
    records: list[dict],
    y_field: str,
    title: str,
) -> None:
    data = sort_by_date(
        [row for row in records if y_field in row and row[y_field] is not None]
    )
    if not data:
        _render_empty_chart_card(title, "표시할 기록이 없습니다.")
        return
    if len(data) < 2:
        _render_empty_chart_card(title, f"기록 2건 이상 필요 (현재 {len(data)}건)")
        return

    dates = [_format_axis_date(row["date"]) for row in data]
    values = [float(row[y_field]) for row in data]

    width, height, pad_x, pad_y = 420, 220, 48, 36
    plot_w = width - pad_x * 2
    plot_h = height - pad_y * 2
    min_v, max_v = min(values), max(values)
    if min_v == max_v:
        min_v -= 1.0
        max_v += 1.0
    value_range = max_v - min_v

    points: list[tuple[float, float]] = []
    for i, value in enumerate(values):
        x = pad_x + plot_w * i / max(len(values) - 1, 1)
        y = pad_y + plot_h * (1 - (value - min_v) / value_range)
        points.append((x, y))

    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    circles = "\n".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#ff4b4b" />'
        for x, y in points
    )
    x_labels = "\n".join(
        f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" '
        f'font-size="11" fill="#b8bcc4">{label}</text>'
        for (x, _), label in zip(points, dates, strict=True)
    )
    y_mid = (min_v + max_v) / 2
    y_labels = (
        f'<text x="8" y="{pad_y + 4}" font-size="11" fill="#b8bcc4">{max_v:.1f}</text>'
        f'<text x="8" y="{pad_y + plot_h / 2 + 4}" font-size="11" fill="#b8bcc4">{y_mid:.1f}</text>'
        f'<text x="8" y="{pad_y + plot_h + 4}" font-size="11" fill="#b8bcc4">{min_v:.1f}</text>'
    )

    svg = f"""
    <div style="border:1px solid #3b3d47;border-radius:12px;padding:10px;background:#262730;height:100%;box-sizing:border-box;">
      <div style="font-weight:600;margin-bottom:4px;color:#fafafa;">{title}</div>
      <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">
        <line x1="{pad_x}" y1="{pad_y}" x2="{pad_x}" y2="{pad_y + plot_h}" stroke="#4b4d59"/>
        <line x1="{pad_x}" y1="{pad_y + plot_h}" x2="{pad_x + plot_w}" y2="{pad_y + plot_h}" stroke="#4b4d59"/>
        <polyline fill="none" stroke="#ff4b4b" stroke-width="2.5" points="{polyline}"/>
        {circles}
        {y_labels}
        {x_labels}
      </svg>
    </div>
    """
    st.markdown(svg, unsafe_allow_html=True)


def render_body_charts(body: list[dict]) -> None:
    st.markdown("#### 날짜별 변화")
    if not body:
        st.caption("신체 기록이 없습니다.")
        return

    fields = get_body_metric_fields()
    chart_fields = [
        field
        for field in fields
        if sum(1 for row in body if field["key"] in row and row[field["key"]] is not None) >= 2
    ]
    if not chart_fields:
        st.caption("그래프를 그릴 항목(2건 이상 기록)이 없습니다.")
    else:
        grid_cols = 3
        for chunk_start in range(0, len(chart_fields), grid_cols):
            chunk = chart_fields[chunk_start : chunk_start + grid_cols]
            cols = st.columns(grid_cols)
            for col, field in zip(cols, chunk):
                with col:
                    render_svg_line_chart(body, str(field["key"]), field_label_with_unit(field))

    sorted_body = sort_by_date(body)
    metric_fields = [
        field
        for field in fields
        if any(field["key"] in row and row[field["key"]] is not None for row in sorted_body)
    ]
    if len(sorted_body) >= 2 and metric_fields:
        first, last = sorted_body[0], sorted_body[-1]
        grid_cols = 3
        for chunk_start in range(0, len(metric_fields), grid_cols):
            chunk = metric_fields[chunk_start : chunk_start + grid_cols]
            cols = st.columns(grid_cols)
            for col, field in zip(cols, chunk):
                key = str(field["key"])
                unit = str(field.get("unit") or "")
                if key not in first or key not in last:
                    continue
                with col:
                    st.metric(
                        f"{field['label']} 변화",
                        format_metric_value(float(last[key]), unit),
                        format_metric_change(float(first[key]), float(last[key]), unit),
                    )


def render_workout_charts(workouts: list[dict], key_prefix: str = "") -> None:
    st.markdown("#### 날짜별 변화")
    if not workouts:
        st.caption("운동 기록이 없습니다.")
        return

    state_key = f"{key_prefix}workout_chart_exercise"
    exercises = sorted({w["exercise"] for w in workouts})
    if st.session_state.get(state_key) not in exercises:
        st.session_state[state_key] = exercises[0]

    selected = st.selectbox("종목 선택", exercises, key=state_key)
    filtered = [w for w in workouts if w["exercise"] == selected]

    c1, c2, c3 = st.columns(3)
    with c1:
        render_svg_line_chart(filtered, "weight_kg", "중량 (kg)")
    with c2:
        render_svg_line_chart(filtered, "sets", "세트")
    with c3:
        render_svg_line_chart(filtered, "reps", "횟수")

    sorted_rows = sort_by_date(filtered)
    if len(sorted_rows) >= 2:
        first, last = sorted_rows[0], sorted_rows[-1]
        m1, m2, m3 = st.columns(3)
        m1.metric(
            "중량 변화",
            f"{last['weight_kg']:.1f} kg",
            f"{last['weight_kg'] - first['weight_kg']:+.1f} kg",
        )
        m2.metric(
            "세트 변화",
            f"{last['sets']}세트",
            f"{last['sets'] - first['sets']:+d}세트",
        )
        m3.metric(
            "횟수 변화",
            f"{last['reps']}회",
            f"{last['reps'] - first['reps']:+d}회",
        )


def _cell_text(value) -> str:
    """data_editor TextColumn과 호환되도록 셀 값을 문자열로 변환."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_editor_float(value, label: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label}: 값을 입력해 주세요.")
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"{label}: 숫자 형식이 올바르지 않습니다 ({value})") from exc


def _parse_editor_int(value, label: str) -> int:
    number = _parse_editor_float(value, label)
    if number != int(number):
        raise ValueError(f"{label}: 정수를 입력해 주세요 ({value})")
    return int(number)


def save_body_table_edits(original: list[dict], edited_rows: list[dict]) -> tuple[int, list[str]]:
    fields = get_body_metric_fields()
    field_keys = [str(f["key"]) for f in fields]
    original_by_id = {str(row["id"]): row for row in original}
    updated = 0
    errors: list[str] = []

    for index, row in enumerate(edited_rows, start=1):
        record_id = str(row.get("id", "")).strip()
        if not record_id:
            errors.append(f"{index}행: ID가 없습니다.")
            continue
        if record_id not in original_by_id:
            errors.append(f"{index}행: 알 수 없는 ID ({record_id})")
            continue

        metrics: dict[str, float] = {}
        row_errors = False
        for key in field_keys:
            if key not in row:
                continue
            label = next((str(f["label"]) for f in fields if str(f["key"]) == key), key)
            try:
                metrics[key] = _parse_editor_float(row[key], label)
            except ValueError as exc:
                errors.append(f"{index}행: {exc}")
                row_errors = True
        if row_errors or not metrics:
            if not row_errors and not metrics:
                errors.append(f"{index}행: 저장할 측정값이 없습니다.")
            continue

        try:
            record_date = parse_date(str(row.get("date", "")).strip() or None)
            original = original_by_id[record_id]
            unchanged = str(original.get("date", "")) == record_date
            if unchanged:
                for key, value in metrics.items():
                    if key not in original or float(original[key]) != value:
                        unchanged = False
                        break
            if unchanged:
                continue

            update_body_metric(record_id, metrics, record_date)
            updated += 1
        except Exception as exc:
            errors.append(f"{index}행: {exc}")

    return updated, errors


def save_workout_table_edits(original: list[dict], edited_rows: list[dict]) -> tuple[int, list[str]]:
    original_by_id = {str(row["id"]): row for row in original}
    updated = 0
    errors: list[str] = []

    for index, row in enumerate(edited_rows, start=1):
        record_id = str(row.get("id", "")).strip()
        if not record_id:
            errors.append(f"{index}행: ID가 없습니다.")
            continue
        if record_id not in original_by_id:
            errors.append(f"{index}행: 알 수 없는 ID ({record_id})")
            continue

        exercise = str(row.get("exercise", "")).strip()
        if not exercise:
            errors.append(f"{index}행: 운동 종목을 입력해 주세요.")
            continue

        try:
            sets = _parse_editor_int(row.get("sets"), "세트")
            reps = _parse_editor_int(row.get("reps"), "횟수")
            weight_kg = _parse_editor_float(row.get("weight_kg", 0), "중량 (kg)")
            record_date = parse_date(str(row.get("date", "")).strip() or None)
            original = original_by_id[record_id]
            unchanged = (
                str(original.get("date", "")) == record_date
                and str(original.get("exercise", "")).strip() == exercise
                and int(original.get("sets", 0)) == sets
                and int(original.get("reps", 0)) == reps
                and float(original.get("weight_kg", 0)) == weight_kg
            )
            if unchanged:
                continue

            update_workout(record_id, exercise, sets, reps, weight_kg, record_date)
            updated += 1
        except ValueError as exc:
            errors.append(f"{index}행: {exc}")
        except Exception as exc:
            errors.append(f"{index}행: {exc}")

    return updated, errors


def render_editable_body_table(body: list[dict]) -> None:
    if not body:
        st.caption("기록 없음")
        return

    st.caption("각 칸에 숫자·날짜를 직접 입력한 뒤 **목록 수정 저장**을 누르세요.")
    fields = get_body_metric_fields()
    sorted_entries = sort_by_date(body)
    col_widths = [0.8, 1.2] + [1.0] * len(fields)

    header = st.columns(col_widths)
    header[0].markdown("**ID**")
    header[1].markdown("**날짜**")
    for index, field in enumerate(fields):
        header[index + 2].markdown(f"**{field_label_with_unit(field)}**")

    edited_rows: list[dict] = []
    for entry in sorted_entries:
        record_id = str(entry["id"])
        cols = st.columns(col_widths)
        cols[0].caption(record_id)
        row = {"id": record_id}
        row["date"] = cols[1].text_input(
            "date",
            value=_cell_text(entry.get("date")),
            key=f"body_row_{record_id}_date",
            label_visibility="collapsed",
        )
        for index, field in enumerate(fields):
            key = str(field["key"])
            row[key] = cols[index + 2].text_input(
                key,
                value=_cell_text(entry.get(key)),
                key=f"body_row_{record_id}_{key}",
                label_visibility="collapsed",
            )
        edited_rows.append(row)

    if st.button("목록 수정 저장", type="primary", key="save_body_table_btn"):
        updated, errors = save_body_table_edits(body, edited_rows)
        if errors:
            for message in errors:
                st.error(message)
        if updated:
            st.success(f"신체 기록 {updated}건을 수정했습니다.")
            st.rerun()
        elif not errors:
            st.info("변경된 내용이 없습니다.")

    with st.expander("기록 삭제"):
        sorted_body = sorted(body, key=lambda x: x["date"], reverse=True)
        labels = {body_label(entry): entry for entry in sorted_body}
        selected_label = st.selectbox(
            "삭제할 기록 선택",
            list(labels.keys()),
            key="delete_body_table_select",
        )
        if st.button("선택 기록 삭제", key="delete_body_table_btn"):
            try:
                delete_body_metric(labels[selected_label]["id"])
                st.success("신체 기록을 삭제했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def render_editable_workout_table(workouts: list[dict]) -> None:
    if not workouts:
        st.caption("기록 없음")
        return

    st.caption("각 칸에 숫자·날짜·종목을 직접 입력한 뒤 **목록 수정 저장**을 누르세요.")
    sorted_entries = sort_by_date(workouts)
    col_widths = [0.8, 1.2, 1.4, 0.8, 0.8, 1.0]
    headers = ["ID", "날짜", "운동 종목", "세트", "횟수", "중량 (kg)"]

    header = st.columns(col_widths)
    for col, title in zip(header, headers, strict=True):
        col.markdown(f"**{title}**")

    edited_rows: list[dict] = []
    for entry in sorted_entries:
        record_id = str(entry["id"])
        cols = st.columns(col_widths)
        cols[0].caption(record_id)
        edited_rows.append(
            {
                "id": record_id,
                "date": cols[1].text_input(
                    "date",
                    value=_cell_text(entry.get("date")),
                    key=f"workout_row_{record_id}_date",
                    label_visibility="collapsed",
                ),
                "exercise": cols[2].text_input(
                    "exercise",
                    value=_cell_text(entry.get("exercise")),
                    key=f"workout_row_{record_id}_exercise",
                    label_visibility="collapsed",
                ),
                "sets": cols[3].text_input(
                    "sets",
                    value=_cell_text(entry.get("sets")),
                    key=f"workout_row_{record_id}_sets",
                    label_visibility="collapsed",
                ),
                "reps": cols[4].text_input(
                    "reps",
                    value=_cell_text(entry.get("reps")),
                    key=f"workout_row_{record_id}_reps",
                    label_visibility="collapsed",
                ),
                "weight_kg": cols[5].text_input(
                    "weight_kg",
                    value=_cell_text(entry.get("weight_kg")),
                    key=f"workout_row_{record_id}_weight",
                    label_visibility="collapsed",
                ),
            }
        )

    if st.button("목록 수정 저장", type="primary", key="save_workout_table_btn"):
        updated, errors = save_workout_table_edits(workouts, edited_rows)
        if errors:
            for message in errors:
                st.error(message)
        if updated:
            st.success(f"운동 기록 {updated}건을 수정했습니다.")
            st.rerun()
        elif not errors:
            st.info("변경된 내용이 없습니다.")

    with st.expander("기록 삭제"):
        sorted_workouts = sorted(workouts, key=lambda x: (x["date"], x["exercise"]), reverse=True)
        labels = {workout_label(entry): entry for entry in sorted_workouts}
        selected_label = st.selectbox(
            "삭제할 기록 선택",
            list(labels.keys()),
            key="delete_workout_table_select",
        )
        if st.button("선택 기록 삭제", key="delete_workout_table_btn"):
            try:
                delete_workout(labels[selected_label]["id"])
                st.success("운동 기록을 삭제했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def render_body_history_view(body: list[dict], days: int) -> None:
    st.metric("신체 측정", f"{len(body)}회")
    render_body_charts(body)

    st.markdown("#### 기록 목록")
    render_editable_body_table(body)

    if st.button("신체 기록 분석", key="analyze_body_btn"):
        try:
            st.code(analyze_fitness_trends.invoke({"days": days}))
        except Exception as exc:
            st.error(str(exc))


def render_workout_history_view(workouts: list[dict], days: int) -> None:
    workout_days = len({w["date"] for w in workouts})
    c1, c2 = st.columns(2)
    c1.metric("운동 세션", f"{len(workouts)}회")
    c2.metric("운동한 날", f"{workout_days}일")

    render_workout_charts(workouts)

    st.markdown("#### 기록 목록")
    render_editable_workout_table(workouts)

    if st.button("운동 기록 분석", key="analyze_workout_btn"):
        try:
            st.code(analyze_fitness_trends.invoke({"days": days}))
        except Exception as exc:
            st.error(str(exc))


def render_records_input_tab() -> None:
    tab_body, tab_workout = st.tabs(["신체 기록", "운동 기록"])
    with tab_body:
        render_body_input()
    with tab_workout:
        render_workout_input()


def get_history_filter() -> tuple[dict[str, list], int | None]:
    st.markdown("##### 조회 조건")
    mode = st.radio(
        "조회 방식",
        ["최근 N일", "날짜 범위", "전체 기록"],
        horizontal=True,
        key="history_filter_mode",
    )

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        days = st.number_input(
            "조회 기간 (일)",
            min_value=1,
            value=30,
            step=1,
            key="history_days",
            disabled=(mode != "최근 N일"),
        )
    with filter_col2:
        start = st.date_input(
            "시작일",
            value=date.today().replace(day=1),
            key="history_start",
            disabled=(mode != "날짜 범위"),
        )
    with filter_col3:
        end = st.date_input(
            "종료일",
            value=date.today(),
            key="history_end",
            disabled=(mode != "날짜 범위"),
        )

    if mode == "최근 N일":
        records = get_records_filtered(days=int(days))
        return records, int(days)

    if mode == "날짜 범위":
        records = get_records_filtered(
            days=None,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        span_days = (end - start).days + 1
        return records, max(span_days, 1)

    records = get_records_filtered(days=None)
    data = load_data()
    all_dates = [
        entry["date"]
        for entry in data["body_metrics"] + data["workouts"]
        if entry.get("date")
    ]
    if all_dates:
        earliest = min(all_dates)
        latest = max(all_dates)
        span_days = (date.fromisoformat(latest) - date.fromisoformat(earliest)).days + 1
    else:
        span_days = 30
    return records, span_days


def render_history_tab() -> None:
    st.subheader("기록 조회")
    try:
        records, analysis_days = get_history_filter()
    except Exception as exc:
        st.error(str(exc))
        return

    body = records["body_metrics"]
    workouts = records["workouts"]

    tab_body, tab_workout = st.tabs(["신체기록 조회", "운동기록 조회"])
    with tab_body:
        render_body_history_view(body, analysis_days)
    with tab_workout:
        render_workout_history_view(workouts, analysis_days)


def handle_chat_prompt(prompt: str) -> None:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("실행 중..."):
            try:
                if is_email_request(prompt):
                    reply = send_fitness_report_direct(
                        user_request=prompt,
                        chat_history=st.session_state.chat_history,
                        ui_messages=st.session_state.messages,
                    )
                else:
                    reply = run_agent(
                        prompt,
                        chat_history=st.session_state.chat_history,
                        executor=cached_executor(),
                    )
            except Exception as exc:
                reply = f"오류: {exc}"
        st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.session_state.chat_history.append(HumanMessage(content=prompt))
        st.session_state.chat_history.append(AIMessage(content=reply))


def render_ai_chat_section(*, key_prefix: str, max_message_area_height: int | None = None) -> None:
    """AI 코치 대화 UI. 대시보드·AI 코치 탭에서 같은 세션(messages/chat_history)을 공유.

    대화가 없을 때는 입력창 위에 빈 박스를 만들지 않고, 대화가 쌓였을 때만
    (그리고 max_message_area_height 지정 시) 스크롤 가능한 영역으로 감쌉니다.
    """
    ex1, ex2 = st.columns(2)
    with ex1:
        quick_analyze = st.button(
            "이번 주 분석해줘", key=f"{key_prefix}_btn_analyze", use_container_width=True
        )
    with ex2:
        quick_email = st.button(
            "메일로 보내줘", key=f"{key_prefix}_btn_email", use_container_width=True
        )

    messages = st.session_state.messages
    if messages:
        use_scroll = bool(max_message_area_height) and len(messages) > 4
        message_area = st.container(height=max_message_area_height) if use_scroll else st.container()
        with message_area:
            for msg in messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

    prompt = st.session_state.pop("pending_prompt", None)
    if quick_analyze:
        prompt = "이번 주 분석해줘"
    elif quick_email:
        has_prior_chat = bool(st.session_state.chat_history) or len(st.session_state.messages) > 0
        if has_prior_chat:
            prompt = "방금 대화한 내용을 메일로 보내줘"
        else:
            prompt = "이번 주 분석해서 메일로 보내줘"

    typed_prompt = st.chat_input(
        "메시지를 입력하고 Enter (예: 벤치프레스 5세트 8회 60kg 기록해줘)",
        key=f"{key_prefix}_chat_input",
    )
    if typed_prompt:
        prompt = typed_prompt

    if prompt:
        handle_chat_prompt(prompt)


def render_chat_tab() -> None:
    st.subheader("AI 피트니스 코치")
    st.caption("운동·신체 기록 저장, 특정 날짜 조회, 분석, 메일 발송 등을 자유롭게 물어보세요. (예: \"7월 5일 몸무게 알려줘\", \"어제 운동 뭐 했지?\")")
    st.divider()
    render_ai_chat_section(key_prefix="coach")


def render_dashboard_kpis(body: list[dict], workouts: list[dict], fields: list[dict]) -> None:
    sorted_body = sort_by_date(body)
    latest = sorted_body[-1] if sorted_body else None
    previous = sorted_body[-2] if len(sorted_body) >= 2 else None

    week_start = date.today() - timedelta(days=date.today().weekday())
    week_workouts = [w for w in workouts if parse_iso_date(w["date"]) >= week_start]
    workout_days = len({w["date"] for w in week_workouts})

    cards: list[tuple[str, str, str | None]] = []
    if latest:
        for field in fields[:3]:
            key = str(field["key"])
            if key not in latest or latest[key] is None:
                continue
            unit = str(field.get("unit") or "")
            value_text = format_metric_value(float(latest[key]), unit)
            delta_text = None
            if previous and key in previous and previous[key] is not None:
                delta_text = format_metric_change(float(previous[key]), float(latest[key]), unit)
            cards.append((str(field["label"]), value_text, delta_text))

    cards.append(("이번 주 운동", f"{workout_days}/4일", None))
    cards.append(("전체 기록", f"{len(body) + len(workouts)}건", None))

    cols = st.columns(len(cards))
    for col, (label, value, delta) in zip(cols, cards, strict=True):
        with col:
            st.metric(label, value, delta)


def render_metric_detail_chart(body: list[dict], field: dict) -> None:
    """선택한 측정 항목 하나의 추세를 차트로 표시. 점 위에 마우스를 올리거나
    (모바일에서는 탭) 하면 날짜·값이 담긴 툴팁이 표시됩니다.
    """
    key = str(field["key"])
    unit = str(field.get("unit") or "")
    label = str(field["label"])

    sorted_body = sort_by_date(body)
    rows: list[dict[str, str | float]] = []
    for entry in sorted_body:
        if key not in entry or entry[key] is None:
            continue
        value = float(entry[key])
        d = datetime.strptime(str(entry["date"]), "%Y-%m-%d")
        rows.append(
            {
                "date": str(entry["date"]),
                "value": value,
                "date_label": d.strftime("%y.%m.%d"),
            }
        )

    if len(rows) < 2:
        st.info(f"{label} 그래프를 그리려면 기록이 2건 이상 필요합니다 (현재 {len(rows)}건).")
        return

    x_enc = alt.X(
        "date:T",
        title=None,
        axis=alt.Axis(
            format="%m/%d",
            labelColor="#b8bcc4",
            tickColor="#4b4d59",
            domainColor="#4b4d59",
            grid=False,
        ),
    )
    y_enc = alt.Y(
        "value:Q",
        title=None,
        scale=alt.Scale(zero=False, padding=12),
        axis=alt.Axis(
            labelColor="#b8bcc4",
            tickColor="#4b4d59",
            domainColor="#4b4d59",
            gridColor="#333640",
        ),
    )
    tooltip = [
        alt.Tooltip("date_label:N", title="날짜"),
        alt.Tooltip("value:Q", title=label, format=".1f"),
    ]

    chart_data = alt.Data(values=rows)
    line = alt.Chart(chart_data).mark_line(color="#ff4b4b", strokeWidth=2.5).encode(x=x_enc, y=y_enc)
    points = (
        alt.Chart(chart_data)
        .mark_point(size=70, filled=True, color="#ff4b4b")
        .encode(x=x_enc, y=y_enc, tooltip=tooltip)
    )

    chart = (
        alt.layer(line, points)
        .properties(height=280, background="transparent")
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption("💡 그래프의 점 위에 마우스를 올리면(모바일은 탭) 날짜와 값이 표시됩니다.")


def render_dashboard_metric_explorer(body: list[dict], fields: list[dict]) -> None:
    """InBody 스타일: 측정 항목을 선택하면 해당 그래프만 보여주는 위젯."""
    sorted_body = sort_by_date(body)
    if not sorted_body:
        st.info("아직 신체 기록이 없습니다. **기록** 탭에서 입력해 보세요.")
        return
    latest = sorted_body[-1]

    selectable_fields = [
        field
        for field in fields
        if sum(1 for row in body if field["key"] in row and row[field["key"]] is not None) >= 2
    ]
    if not selectable_fields:
        st.info("그래프를 그릴 항목(2건 이상 기록)이 아직 없습니다.")
        return

    option_labels: list[str] = []
    label_to_field: dict[str, dict] = {}
    for field in selectable_fields:
        key = str(field["key"])
        unit = str(field.get("unit") or "")
        value = latest.get(key)
        value_text = format_metric_value(float(value), unit) if value is not None else "-"
        option_label = f"{field['label']}  {value_text}"
        option_labels.append(option_label)
        label_to_field[option_label] = field

    state_key = "dash_metric_select"
    if st.session_state.get(state_key) not in option_labels:
        st.session_state[state_key] = option_labels[0]

    selected = st.segmented_control(
        "측정 항목 선택",
        option_labels,
        key=state_key,
        label_visibility="collapsed",
    )
    selected = selected or option_labels[0]

    render_metric_detail_chart(body, label_to_field[selected])


def render_dashboard_tab() -> None:
    today = date.today()
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    st.markdown(
        f"""
        <div class="dash-hero">
          <h2>👋 오늘도 힘내봐요!</h2>
          <p>{today.isoformat()} ({weekday_kr}요일) 기준 피트니스 현황을 한눈에 확인하세요.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    data = load_data()
    body = data["body_metrics"]
    workouts = data["workouts"]
    fields = get_body_metric_fields()

    render_dashboard_kpis(body, workouts, fields)

    st.markdown('<div class="dash-section-title">📈 최근 변화</div>', unsafe_allow_html=True)
    if not body and not workouts:
        st.info("아직 기록이 없습니다. **기록** 탭에서 신체·운동 정보를 입력해 보세요.")
    else:
        render_dashboard_metric_explorer(body, fields)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        if workouts:
            render_workout_charts(workouts, key_prefix="dash_")
        else:
            st.caption("운동 기록이 없습니다. 운동을 기록하면 그래프가 표시됩니다.")

    st.divider()
    st.markdown('<div class="dash-section-title">💬 AI 코치에게 물어보기</div>', unsafe_allow_html=True)
    render_ai_chat_section(key_prefix="dash", max_message_area_height=320)


def main() -> None:
    check_runtime()
    init_session_state()

    st.title("💪 Fitness Agent")
    t0, t1, t2, t3 = st.tabs(["🏠 대시보드", "기록", "기록 조회", "AI 코치"])
    with t0:
        render_dashboard_tab()
    with t1:
        render_records_input_tab()
    with t2:
        render_history_tab()
    with t3:
        render_chat_tab()

    with st.sidebar:
        st.caption(f"Python {sys.version_info.major}.{sys.version_info.minor}")
        data = load_data()
        st.write(f"신체 {len(data['body_metrics'])}건 / 운동 {len(data['workouts'])}건")

        st.markdown("##### 메일 설정")
        mail_status = smtp_config_status()
        if mail_status["ok"]:
            st.success(str(mail_status["summary"]))
            st.caption(f"리포트 수신: {default_report_recipient()}")
        else:
            st.error(str(mail_status["summary"]))
        with st.expander("Gmail 앱 비밀번호 설정"):
            st.markdown(
                "1. [Google 보안](https://myaccount.google.com/security) → **2단계 인증** 켜기\n"
                "2. [앱 비밀번호](https://myaccount.google.com/apppasswords) → 메일용 생성\n"
                "3. `fitness_agent/.env` 의 `SMTP_PASSWORD` 를 **16자리 앱 비밀번호**로 교체\n"
                "4. `./run_web.sh` 재시작"
            )
        if st.button("SMTP 연결 테스트", key="smtp_test_btn", use_container_width=True):
            st.info(test_smtp_connection())

        st.code("conda activate day15\n./run_web.sh", language="bash")


if __name__ == "__main__":
    main()

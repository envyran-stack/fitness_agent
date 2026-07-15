"""Fitness Agent — Streamlit 웹앱 (경량·안정 버전)."""

from __future__ import annotations

import os

# pyarrow의 기본 mimalloc 할당자가 최신 macOS의 스레드 로컬 스토리지 구현과 충돌해
# 세그폴트(Connection error로 나타남)를 유발하는 문제가 있어, pyarrow를 import하기 전에
# 시스템 malloc을 쓰도록 강제한다. (run_web.sh에서도 동일하게 설정하지만, 다른 방식으로
# 앱을 실행하는 경우를 대비해 여기서도 방어적으로 설정)
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import streamlit as st
import streamlit.components.v1 as components
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
    add_event,
    delete_body_metric,
    delete_event,
    delete_workout,
    get_body_metric_fields,
    create_profile,
    delete_profile,
    get_events,
    get_records_filtered,
    list_profiles,
    load_data,
    parse_date,
    profile_has_pin,
    remove_body_metric_field,
    sanitize_username,
    set_current_user,
    set_profile_pin,
    update_body_metric,
    update_event,
    update_workout,
    verify_profile_pin,
)
from tools import analyze_fitness_trends, save_workout
from smtp_config import default_report_recipient, smtp_config_status, test_smtp_connection

load_dotenv(Path(__file__).resolve().parent / ".env")


def _load_cloud_secrets() -> None:
    """Streamlit Cloud의 st.secrets 값을 os.environ에 복사한다.

    로컬에서는 .env를, 배포 환경(Streamlit Community Cloud)에서는 Settings → Secrets를
    쓰도록 하되, 기존 os.getenv() 기반 코드는 그대로 두기 위한 다리 역할만 한다.
    secrets.toml이 없는 로컬 환경에서는 조용히 넘어간다.
    """
    try:
        # secrets.toml이 없는 로컬 환경에서는 st.error()가 그려지지 않도록,
        # 먼저 조용히 존재 여부만 확인한다(있으면 이미 파싱까지 끝난 상태가 된다).
        if not st.secrets.load_if_toml_exists():
            return
        secrets = st.secrets
        for key in (
            "OPENAI_API_KEY",
            "SMTP_USER",
            "SMTP_PASSWORD",
            "SMTP_HOST",
            "SMTP_PORT",
            "REPORT_EMAIL",
        ):
            if key in secrets and not os.getenv(key):
                os.environ[key] = str(secrets[key])
    except Exception:
        return


_load_cloud_secrets()

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
    # 로컬은 .env, 배포(Streamlit Cloud)는 Secrets를 쓰므로, 파일 존재 여부 대신
    # 실제로 키가 로드됐는지(둘 중 하나라도 설정됐는지)로 판단한다.
    if not os.getenv("OPENAI_API_KEY"):
        st.warning("OPENAI_API_KEY 미설정 — 로컬은 `.env`, 배포 환경은 Streamlit Cloud **Secrets**에 설정해 주세요.")


@st.cache_resource
def cached_executor():
    return get_executor()


def init_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []


def _profile_login(name: str) -> None:
    """PIN 확인이 끝난 프로필로 로그인 처리하고 메인 화면으로 이동한다."""
    st.session_state.current_profile = name
    st.session_state.pop("pending_profile", None)
    st.session_state.pending_new_profile = False
    st.rerun()


def _render_pin_entry(name: str) -> None:
    st.subheader(f"👤 {name}")
    if profile_has_pin(name):
        st.caption("4자리 PIN을 입력해 주세요.")
        with st.form(f"pin_form_{name}"):
            pin = st.text_input("PIN", max_chars=4, type="password", key=f"pin_input_{name}")
            col_ok, col_back = st.columns(2)
            ok = col_ok.form_submit_button("확인", type="primary", use_container_width=True)
            back = col_back.form_submit_button("뒤로", use_container_width=True)
        if back:
            st.session_state.pop("pending_profile", None)
            st.rerun()
        if ok:
            if verify_profile_pin(name, pin):
                _profile_login(name)
            else:
                st.error("PIN이 올바르지 않습니다.")
    else:
        st.caption("이 프로필은 아직 PIN이 없습니다. 사용할 4자리 PIN을 새로 설정해 주세요.")
        with st.form(f"setpin_form_{name}"):
            pin1 = st.text_input("새 PIN (숫자 4자리)", max_chars=4, type="password", key=f"newpin1_{name}")
            pin2 = st.text_input("PIN 확인", max_chars=4, type="password", key=f"newpin2_{name}")
            col_ok, col_back = st.columns(2)
            ok = col_ok.form_submit_button("설정하고 시작하기", type="primary", use_container_width=True)
            back = col_back.form_submit_button("뒤로", use_container_width=True)
        if back:
            st.session_state.pop("pending_profile", None)
            st.rerun()
        if ok:
            if pin1 != pin2:
                st.error("PIN이 서로 다릅니다.")
            elif not pin1.isdigit() or len(pin1) != 4:
                st.error("PIN은 숫자 4자리로 입력해 주세요.")
            else:
                set_profile_pin(name, pin1)
                _profile_login(name)


def _render_delete_confirm(name: str) -> None:
    st.subheader(f"🗑 '{name}' 프로필 삭제")
    st.warning("삭제하면 이 프로필의 신체·운동·이벤트 기록이 전부 사라지고 되돌릴 수 없습니다.")

    if profile_has_pin(name):
        with st.form(f"delete_form_{name}"):
            pin = st.text_input("PIN 확인", max_chars=4, type="password", key=f"del_pin_{name}")
            col_ok, col_back = st.columns(2)
            confirm = col_ok.form_submit_button("삭제", type="primary", use_container_width=True)
            cancel = col_back.form_submit_button("취소", use_container_width=True)
        if cancel:
            st.session_state.pop("pending_delete_profile", None)
            st.rerun()
        if confirm:
            if verify_profile_pin(name, pin):
                delete_profile(name)
                st.session_state.pop("pending_delete_profile", None)
                st.rerun()
            else:
                st.error("PIN이 올바르지 않습니다.")
    else:
        st.caption("이 프로필에는 PIN이 없어서, 확인을 위해 닉네임을 다시 입력해 주세요.")
        with st.form(f"delete_form_nopin_{name}"):
            typed = st.text_input("닉네임 다시 입력", key=f"del_typed_{name}")
            col_ok, col_back = st.columns(2)
            confirm = col_ok.form_submit_button("삭제", type="primary", use_container_width=True)
            cancel = col_back.form_submit_button("취소", use_container_width=True)
        if cancel:
            st.session_state.pop("pending_delete_profile", None)
            st.rerun()
        if confirm:
            if sanitize_username(typed) == name:
                delete_profile(name)
                st.session_state.pop("pending_delete_profile", None)
                st.rerun()
            else:
                st.error("닉네임이 일치하지 않습니다.")


def _render_new_profile_form() -> None:
    st.subheader("➕ 새 프로필 추가")
    with st.form("new_profile_form"):
        name_input = st.text_input("닉네임 (예: 철수)", max_chars=30)
        pin1 = st.text_input("PIN (숫자 4자리)", max_chars=4, type="password")
        pin2 = st.text_input("PIN 확인", max_chars=4, type="password")
        col_ok, col_back = st.columns(2)
        ok = col_ok.form_submit_button("만들기", type="primary", use_container_width=True)
        back = col_back.form_submit_button("뒤로", use_container_width=True)
    if back:
        st.session_state.pending_new_profile = False
        st.rerun()
    if ok:
        clean = sanitize_username(name_input)
        if not clean:
            st.error("한글·영문·숫자로 닉네임을 입력해 주세요.")
        elif pin1 != pin2:
            st.error("PIN이 서로 다릅니다.")
        elif not pin1.isdigit() or len(pin1) != 4:
            st.error("PIN은 숫자 4자리로 입력해 주세요.")
        else:
            try:
                create_profile(clean, pin1)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _profile_login(clean)


def _render_profile_picker() -> None:
    st.title("💪 Fitness Agent")

    pending_delete = st.session_state.get("pending_delete_profile")
    if pending_delete:
        _render_delete_confirm(pending_delete)
        return
    pending = st.session_state.get("pending_profile")
    if pending:
        _render_pin_entry(pending)
        return
    if st.session_state.get("pending_new_profile"):
        _render_new_profile_form()
        return

    st.subheader("누구신가요?")
    st.caption(
        "여러 명이 같은 앱을 함께 쓰기 때문에, 프로필을 선택하고 PIN을 입력해 주세요. "
        "(🗑 로 필요 없는 프로필을 지울 수 있어요.)"
    )

    profiles = list_profiles()
    if profiles:
        cols_per_row = 4
        for row_start in range(0, len(profiles), cols_per_row):
            row = profiles[row_start : row_start + cols_per_row]
            cols = st.columns(cols_per_row)
            for name, col in zip(row, cols, strict=False):
                with col:
                    sub_main, sub_del = st.columns([4, 1])
                    with sub_main:
                        if st.button(f"👤 {name}", key=f"profile_btn_{name}", use_container_width=True):
                            st.session_state.pending_profile = name
                            st.rerun()
                    with sub_del:
                        if st.button("🗑", key=f"del_btn_{name}", use_container_width=True, help=f"'{name}' 프로필 삭제"):
                            st.session_state.pending_delete_profile = name
                            st.rerun()
    else:
        st.caption("아직 등록된 프로필이 없습니다. 아래에서 새로 만들어 주세요.")

    st.divider()
    if st.button("➕ 새 프로필 추가", use_container_width=True):
        st.session_state.pending_new_profile = True
        st.rerun()


def ensure_current_user() -> str:
    """여러 사람이 같은 배포본을 함께 쓸 때 기록이 섞이지 않도록, 넷플릭스 프로필처럼
    닉네임을 고르고 PIN으로 확인한다. 프로필 선택은 세션(브라우저 탭)마다 유지된다."""
    current = st.session_state.get("current_profile")
    if current:
        set_current_user(current)
        return current

    _render_profile_picker()
    st.stop()


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


def event_period_text(entry: dict) -> str:
    start = entry.get("start_date", "")
    end = entry.get("end_date", start)
    return start if start == end else f"{start} ~ {end}"


def event_label(entry: dict) -> str:
    return f"{event_period_text(entry)} | {entry.get('title', '')}"


def render_event_input() -> None:
    st.subheader("이벤트")
    st.caption(
        "교육·여행·부상 등 신체 상태에 영향을 줄 수 있는 기간을 기록해 두면, "
        "그래프에 해당 기간이 함께 표시됩니다."
    )

    title = st.text_input(
        "이벤트 제목", key="event_title", placeholder="예: Autonomous R&D 교육"
    )
    date_range = st.date_input(
        "기간 (하루짜리 이벤트는 같은 날짜를 두 번 선택)",
        value=(date.today(), date.today()),
        key="event_date_range",
    )
    note = st.text_input(
        "메모 (선택)", key="event_note", placeholder="예: 야근이 많아 운동량이 줄었음"
    )

    if st.button("이벤트 저장", type="primary", key="save_event_btn"):
        if isinstance(date_range, (tuple, list)):
            if len(date_range) == 2:
                start, end = date_range
            elif len(date_range) == 1:
                start = end = date_range[0]
            else:
                start = end = date.today()
        else:
            start = end = date_range

        if not title.strip():
            st.error("이벤트 제목을 입력해 주세요.")
        else:
            try:
                entry = add_event(title, start.isoformat(), end.isoformat(), note)
                st.success(f"이벤트 저장 완료: {entry['title']} ({event_period_text(entry)})")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.markdown("##### 등록된 이벤트")
    render_editable_event_table(get_events())


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def sort_by_date(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda row: row["date"])


def _format_axis_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%m/%d")
    except ValueError:
        return value


def _render_html_card(body_html: str, height: int) -> None:
    """카드형 HTML(+SVG)을 iframe으로 렌더링한다.

    st.markdown(unsafe_allow_html=True)로 <svg>가 포함된 마크업을 렌더링하면
    브라우저에서 "First argument must be a String, HTMLElement, HTMLCollection,
    or NodeList" TypeError가 발생하는 경우가 있어(Streamlit 마크다운 새니타이저/
    앵커링크 처리 파이프라인 이슈), 별도 iframe에서 독립적으로 렌더링해 이를 피한다.
    """
    components.html(
        f"""
        <html>
          <head>
            <style>
              html, body {{ margin: 0; padding: 0; background: transparent; }}
            </style>
          </head>
          <body>{body_html}</body>
        </html>
        """,
        height=height,
    )


def _render_empty_chart_card(title: str, message: str) -> None:
    _render_html_card(
        f"""
        <div style="border:1px solid #3b3d47;border-radius:12px;padding:10px;
                    background:#262730;height:100%;box-sizing:border-box;
                    min-height:150px;display:flex;flex-direction:column;">
          <div style="font-weight:600;margin-bottom:4px;color:#fafafa;">{title}</div>
          <div style="color:#9a9ea8;font-size:0.85rem;flex:1;display:flex;
                      align-items:center;">{message}</div>
        </div>
        """,
        height=170,
    )


_EVENT_BAND_COLORS = ["#ffb300", "#7c4dff", "#00bcd4", "#ff7043"]


def _events_overlapping(events: list[dict] | None, start: date, end: date) -> list[dict]:
    if not events:
        return []
    overlapping = []
    for event in events:
        if not isinstance(event, dict) or not event.get("start_date"):
            continue
        try:
            ev_start = date.fromisoformat(str(event["start_date"]))
            ev_end = date.fromisoformat(str(event.get("end_date") or event["start_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        try:
            if ev_end < start or ev_start > end:
                continue
        except TypeError:
            continue
        overlapping.append(event)
    return overlapping


def render_svg_line_chart(
    records: list[dict],
    y_field: str,
    title: str,
    events: list[dict] | None = None,
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

    row_dates = [date.fromisoformat(row["date"]) for row in data]
    values = [float(row[y_field]) for row in data]

    width, height, pad_x, pad_y = 420, 220, 48, 36
    plot_w = width - pad_x * 2
    plot_h = height - pad_y * 2
    min_v, max_v = min(values), max(values)
    if min_v == max_v:
        min_v -= 1.0
        max_v += 1.0
    value_range = max_v - min_v

    # 실제 날짜(캘린더 기준) 비례로 x좌표를 계산한다. 데이터가 뜨문뜨문 있어도
    # 간격이 실제 날짜 차이만큼 반영되고, 이벤트 기간도 같은 축에 정확히 얹을 수 있다.
    min_ord, max_ord = row_dates[0].toordinal(), row_dates[-1].toordinal()
    date_span = max(max_ord - min_ord, 1)

    def x_of(d: date) -> float:
        ratio = (d.toordinal() - min_ord) / date_span
        ratio = min(max(ratio, 0.0), 1.0)
        return pad_x + plot_w * ratio

    points: list[tuple[float, float]] = []
    for d, value in zip(row_dates, values, strict=True):
        x = x_of(d)
        y = pad_y + plot_h * (1 - (value - min_v) / value_range)
        points.append((x, y))

    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    circles = "\n".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#ff4b4b" />'
        for x, y in points
    )

    # 이벤트 기간을 반투명한 띠로 표시 (범례는 그래프 아래 캡션으로 별도 표시).
    # 이벤트 데이터가 예상과 다른 형태여도 그래프 자체는 항상 정상적으로 그려지도록
    # 이벤트 처리 전체를 보호한다.
    visible_events: list[dict] = []
    event_bands = ""
    try:
        visible_events = _events_overlapping(events, row_dates[0], row_dates[-1])
        for i, event in enumerate(visible_events):
            ev_start = date.fromisoformat(str(event["start_date"]))
            ev_end = date.fromisoformat(str(event.get("end_date") or event["start_date"]))
            clipped_start = max(ev_start, row_dates[0])
            clipped_end = min(ev_end, row_dates[-1])
            x1, x2 = x_of(clipped_start), x_of(clipped_end)
            if x2 - x1 < 2:
                x1, x2 = x1 - 1, x1 + 1
            color = _EVENT_BAND_COLORS[i % len(_EVENT_BAND_COLORS)]
            event_bands += (
                f'<rect x="{x1:.1f}" y="{pad_y}" width="{x2 - x1:.1f}" height="{plot_h}" '
                f'fill="{color}" opacity="0.18" />'
            )
    except (KeyError, TypeError, ValueError):
        visible_events = []
        event_bands = ""

    # 데이터가 많을수록(예: 전체 기간) 라벨이 겹치므로, 표시할 x축 라벨 개수를
    # 가로 폭에 맞춰 자동으로 줄이고 실제 날짜 간격에 맞춰 균등하게 고른다.
    max_labels = max(2, plot_w // 45)
    n_ticks = min(max_labels, date_span + 1)
    n_ticks = max(n_ticks, 2) if date_span > 0 else 1
    if n_ticks > 1:
        tick_ords = sorted({round(min_ord + date_span * i / (n_ticks - 1)) for i in range(n_ticks)})
    else:
        tick_ords = [min_ord]

    x_labels = "\n".join(
        f'<text x="{x_of(date.fromordinal(ordv)):.1f}" y="{height - 8}" text-anchor="middle" '
        f'font-size="11" fill="#b8bcc4">{_format_axis_date(date.fromordinal(ordv).isoformat())}</text>'
        for ordv in tick_ords
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
        {event_bands}
        <line x1="{pad_x}" y1="{pad_y}" x2="{pad_x}" y2="{pad_y + plot_h}" stroke="#4b4d59"/>
        <line x1="{pad_x}" y1="{pad_y + plot_h}" x2="{pad_x + plot_w}" y2="{pad_y + plot_h}" stroke="#4b4d59"/>
        <polyline fill="none" stroke="#ff4b4b" stroke-width="2.5" points="{polyline}"/>
        {circles}
        {y_labels}
        {x_labels}
      </svg>
    </div>
    """
    _render_html_card(svg, height=height + 44)

    if visible_events:
        try:
            legend = " · ".join(
                f'<span style="color:{_EVENT_BAND_COLORS[i % len(_EVENT_BAND_COLORS)]};">■</span> '
                f'{event.get("title", "")} ({event.get("start_date", "")}~{event.get("end_date", "")})'
                for i, event in enumerate(visible_events)
            )
            st.markdown(f'<div style="font-size:0.8rem;color:#b8bcc4;">{legend}</div>', unsafe_allow_html=True)
        except (KeyError, TypeError):
            pass


def render_body_charts(body: list[dict], events: list[dict] | None = None) -> None:
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
                    render_svg_line_chart(
                        body, str(field["key"]), field_label_with_unit(field), events=events
                    )

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


def render_workout_charts(
    workouts: list[dict], key_prefix: str = "", events: list[dict] | None = None
) -> None:
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
        render_svg_line_chart(filtered, "weight_kg", "중량 (kg)", events=events)
    with c2:
        render_svg_line_chart(filtered, "sets", "세트", events=events)
    with c3:
        render_svg_line_chart(filtered, "reps", "횟수", events=events)

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


def save_event_table_edits(original: list[dict], edited_rows: list[dict]) -> tuple[int, list[str]]:
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

        title = str(row.get("title", "")).strip()
        if not title:
            errors.append(f"{index}행: 이벤트 제목을 입력해 주세요.")
            continue

        try:
            start_date = parse_date(str(row.get("start_date", "")).strip() or None)
            end_date = parse_date(str(row.get("end_date", "")).strip() or None)
            note = str(row.get("note", "")).strip()
            norm_start, norm_end = (
                (start_date, end_date) if start_date <= end_date else (end_date, start_date)
            )
            original = original_by_id[record_id]
            unchanged = (
                str(original.get("start_date", "")) == norm_start
                and str(original.get("end_date", "")) == norm_end
                and str(original.get("title", "")).strip() == title
                and str(original.get("note", "")).strip() == note
            )
            if unchanged:
                continue

            update_event(record_id, title, start_date, end_date, note)
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


_WORKOUT_CARDS_PER_ROW = 3


def _group_workouts_by_date(sorted_entries: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for entry in sorted_entries:
        grouped.setdefault(str(entry.get("date", "")), []).append(entry)
    return grouped


def _chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def render_workout_quick_add() -> None:
    with st.expander("➕ 새 운동 기록 추가", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            record_date = st.date_input("운동일", value=date.today(), key="quick_add_workout_date")
            exercise_choice = st.selectbox("운동 종목", EXERCISE_OPTIONS, key="quick_add_workout_exercise")
            custom = (
                st.text_input("기타 종목", key="quick_add_workout_custom")
                if exercise_choice == "기타"
                else ""
            )
        with col2:
            sets = st.number_input("세트", 1, 20, 4, key="quick_add_workout_sets")
            reps = st.number_input("횟수", 1, 100, 10, key="quick_add_workout_reps")
            weight = st.number_input("중량 (kg)", 0.0, 300.0, 0.0, 2.5, key="quick_add_workout_weight")

        exercise = custom.strip() if exercise_choice == "기타" else exercise_choice

        if st.button("추가", type="primary", key="quick_add_workout_btn"):
            if exercise_choice == "기타" and not exercise:
                st.error("운동 종목을 입력해 주세요.")
            else:
                try:
                    msg = save_workout.invoke(
                        {
                            "exercise": exercise,
                            "sets": int(sets),
                            "reps": int(reps),
                            "weight_kg": weight,
                            "record_date": record_date.isoformat(),
                        }
                    )
                    st.success(msg)
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def render_editable_workout_table(workouts: list[dict]) -> None:
    render_workout_quick_add()

    if not workouts:
        st.caption("기록 없음")
        return

    st.caption(
        "같은 날짜의 운동은 한 줄에 모아서 표시됩니다 "
        f"(한 줄에 최대 {_WORKOUT_CARDS_PER_ROW}개, 더 있으면 아래 줄로 이어집니다). "
        "칸을 수정한 뒤 **목록 수정 저장**을 누르세요. (날짜 변경은 아래 '기록 삭제'로 지우고 다시 등록해 주세요.)"
    )

    sorted_entries = sort_by_date(workouts)
    grouped = _group_workouts_by_date(sorted_entries)

    edited_rows: list[dict] = []
    for record_date, entries in grouped.items():
        chunks = _chunk(entries, _WORKOUT_CARDS_PER_ROW)

        for chunk_index, chunk in enumerate(chunks):
            row_cols = st.columns([0.7] + [1.0] * _WORKOUT_CARDS_PER_ROW)

            with row_cols[0]:
                if chunk_index == 0:
                    st.markdown(f"**{record_date}**")
                    st.caption(f"{len(entries)}개 종목")
                else:
                    st.caption("⤷ 이어서")

            for entry, col in zip(chunk, row_cols[1 : 1 + len(chunk)], strict=True):
                record_id = str(entry["id"])
                with col:
                    with st.container(border=True):
                        exercise = st.text_input(
                            "종목",
                            value=_cell_text(entry.get("exercise")),
                            key=f"workout_row_{record_id}_exercise",
                        )
                        field_cols = st.columns(3)
                        sets = field_cols[0].text_input(
                            "세트",
                            value=_cell_text(entry.get("sets")),
                            key=f"workout_row_{record_id}_sets",
                        )
                        reps = field_cols[1].text_input(
                            "횟수",
                            value=_cell_text(entry.get("reps")),
                            key=f"workout_row_{record_id}_reps",
                        )
                        weight = field_cols[2].text_input(
                            "중량(kg)",
                            value=_cell_text(entry.get("weight_kg")),
                            key=f"workout_row_{record_id}_weight",
                        )
                edited_rows.append(
                    {
                        "id": record_id,
                        "date": record_date,
                        "exercise": exercise,
                        "sets": sets,
                        "reps": reps,
                        "weight_kg": weight,
                    }
                )

        st.divider()

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


def render_editable_event_table(events: list[dict]) -> None:
    if not events:
        st.caption("등록된 이벤트가 없습니다.")
        return

    st.caption("각 칸을 수정한 뒤 **이벤트 목록 저장**을 누르세요.")
    sorted_events = sorted(events, key=lambda x: x["start_date"])
    col_widths = [1.1, 1.1, 1.6, 1.6]
    headers = ["시작일", "종료일", "제목", "메모"]

    header = st.columns(col_widths)
    for col, title_h in zip(header, headers, strict=True):
        col.markdown(f"**{title_h}**")

    edited_rows: list[dict] = []
    for entry in sorted_events:
        record_id = str(entry["id"])
        cols = st.columns(col_widths)
        edited_rows.append(
            {
                "id": record_id,
                "start_date": cols[0].text_input(
                    "start_date",
                    value=_cell_text(entry.get("start_date")),
                    key=f"event_row_{record_id}_start",
                    label_visibility="collapsed",
                ),
                "end_date": cols[1].text_input(
                    "end_date",
                    value=_cell_text(entry.get("end_date")),
                    key=f"event_row_{record_id}_end",
                    label_visibility="collapsed",
                ),
                "title": cols[2].text_input(
                    "title",
                    value=_cell_text(entry.get("title")),
                    key=f"event_row_{record_id}_title",
                    label_visibility="collapsed",
                ),
                "note": cols[3].text_input(
                    "note",
                    value=_cell_text(entry.get("note")),
                    key=f"event_row_{record_id}_note",
                    label_visibility="collapsed",
                ),
            }
        )

    if st.button("이벤트 목록 저장", type="primary", key="save_event_table_btn"):
        updated, errors = save_event_table_edits(events, edited_rows)
        if errors:
            for message in errors:
                st.error(message)
        if updated:
            st.success(f"이벤트 {updated}건을 수정했습니다.")
            st.rerun()
        elif not errors:
            st.info("변경된 내용이 없습니다.")

    with st.expander("이벤트 삭제"):
        sorted_desc = sorted(events, key=lambda x: x["start_date"], reverse=True)
        labels = {event_label(entry): entry for entry in sorted_desc}
        selected_label = st.selectbox(
            "삭제할 이벤트 선택",
            list(labels.keys()),
            key="delete_event_select",
        )
        if st.button("선택 이벤트 삭제", key="delete_event_btn"):
            try:
                delete_event(labels[selected_label]["id"])
                st.success("이벤트를 삭제했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def render_body_history_view(body: list[dict], days: int, events: list[dict] | None = None) -> None:
    st.metric("신체 측정", f"{len(body)}회")
    render_body_charts(body, events=events)

    st.markdown("#### 기록 목록")
    render_editable_body_table(body)

    if st.button("신체 기록 분석", key="analyze_body_btn"):
        try:
            st.code(analyze_fitness_trends.invoke({"days": days}))
        except Exception as exc:
            st.error(str(exc))


def render_workout_history_view(
    workouts: list[dict], days: int, events: list[dict] | None = None
) -> None:
    workout_days = len({w["date"] for w in workouts})
    c1, c2 = st.columns(2)
    c1.metric("운동 세션", f"{len(workouts)}회")
    c2.metric("운동한 날", f"{workout_days}일")

    render_workout_charts(workouts, events=events)

    st.markdown("#### 기록 목록")
    render_editable_workout_table(workouts)

    if st.button("운동 기록 분석", key="analyze_workout_btn"):
        try:
            st.code(analyze_fitness_trends.invoke({"days": days}))
        except Exception as exc:
            st.error(str(exc))


def render_records_input_tab() -> None:
    tab_body, tab_workout, tab_event = st.tabs(["신체 기록", "운동 기록", "이벤트"])
    with tab_body:
        render_body_input()
    with tab_workout:
        render_workout_input()
    with tab_event:
        render_event_input()


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
    events = records.get("events", [])

    tab_body, tab_workout = st.tabs(["신체기록 조회", "운동기록 조회"])
    with tab_body:
        render_body_history_view(body, analysis_days, events=events)
    with tab_workout:
        render_workout_history_view(workouts, analysis_days, events=events)


def handle_chat_prompt(prompt: str) -> None:
    """대화를 세션에 기록하고 AI 응답을 받아온 뒤 다시 그린다.

    메시지를 직접 여기서 그리지 않고 st.rerun()으로 넘기는 이유는, 입력창이
    탭 바깥(화면 하단 고정)에 있어서 이 함수가 호출되는 위치와 대화 내역이
    실제로 표시되는 위치(각 탭의 메시지 영역)가 다르기 때문이다.
    """
    st.session_state.messages.append({"role": "user", "content": prompt})
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
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.chat_history.append(HumanMessage(content=prompt))
    st.session_state.chat_history.append(AIMessage(content=reply))
    st.rerun()


def render_ai_chat_section(*, key_prefix: str, max_message_area_height: int | None = None) -> None:
    """AI 코치 대화 내역 UI. 대시보드·AI 코치 탭에서 같은 세션(messages/chat_history)을 공유.

    입력창은 여기서 그리지 않는다 — 탭 안에 두면 화면 하단에 고정되지 않으므로,
    화면 아래에 항상 고정된 입력창 하나를 main()에서 탭 바깥에 그리고, 대화 내역만
    (질문/답변) 이 함수에서 그 위쪽에 표시한다.
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
    else:
        st.caption("아직 대화가 없습니다. 화면 아래 입력창에 메시지를 입력해 보세요.")

    if quick_analyze:
        handle_chat_prompt("이번 주 분석해줘")
    elif quick_email:
        has_prior_chat = bool(st.session_state.chat_history) or len(st.session_state.messages) > 0
        if has_prior_chat:
            handle_chat_prompt("방금 대화한 내용을 메일로 보내줘")
        else:
            handle_chat_prompt("이번 주 분석해서 메일로 보내줘")


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


def render_metric_detail_chart(
    body: list[dict], field: dict, events: list[dict] | None = None
) -> None:
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

    visible_range_start = date.fromisoformat(rows[0]["date"])
    visible_range_end = date.fromisoformat(rows[-1]["date"])
    visible_events: list[dict] = []
    layers = []
    try:
        visible_events = _events_overlapping(events, visible_range_start, visible_range_end)
        for i, event in enumerate(visible_events):
            event_data = alt.Data(
                values=[
                    {
                        "start": event["start_date"],
                        "end": event.get("end_date") or event["start_date"],
                    }
                ]
            )
            color = _EVENT_BAND_COLORS[i % len(_EVENT_BAND_COLORS)]
            layers.append(
                alt.Chart(event_data)
                .mark_rect(color=color, opacity=0.18)
                .encode(x="start:T", x2="end:T")
            )
    except (KeyError, TypeError, ValueError):
        visible_events = []
        layers = []
    layers.extend([line, points])

    chart = (
        alt.layer(*layers)
        .properties(height=280, background="transparent")
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption("💡 그래프의 점 위에 마우스를 올리면(모바일은 탭) 날짜와 값이 표시됩니다.")

    if visible_events:
        try:
            legend = " · ".join(
                f'<span style="color:{_EVENT_BAND_COLORS[i % len(_EVENT_BAND_COLORS)]};">■</span> '
                f'{event.get("title", "")} ({event.get("start_date", "")}~{event.get("end_date", "")})'
                for i, event in enumerate(visible_events)
            )
            st.markdown(f'<div style="font-size:0.8rem;color:#b8bcc4;">{legend}</div>', unsafe_allow_html=True)
        except (KeyError, TypeError):
            pass


def render_dashboard_metric_explorer(
    body: list[dict], fields: list[dict], events: list[dict] | None = None
) -> None:
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

    render_metric_detail_chart(body, label_to_field[selected], events=events)


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
    events = data.get("events", [])
    fields = get_body_metric_fields()

    render_dashboard_kpis(body, workouts, fields)

    st.markdown('<div class="dash-section-title">📈 최근 변화</div>', unsafe_allow_html=True)
    if not body and not workouts:
        st.info("아직 기록이 없습니다. **기록** 탭에서 신체·운동 정보를 입력해 보세요.")
    else:
        render_dashboard_metric_explorer(body, fields, events=events)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        if workouts:
            render_workout_charts(workouts, key_prefix="dash_", events=events)
        else:
            st.caption("운동 기록이 없습니다. 운동을 기록하면 그래프가 표시됩니다.")

    st.divider()
    st.markdown('<div class="dash-section-title">💬 AI 코치에게 물어보기</div>', unsafe_allow_html=True)
    render_ai_chat_section(key_prefix="dash", max_message_area_height=320)


def main() -> None:
    check_runtime()
    init_session_state()
    current_user = ensure_current_user()

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

    # 탭 바깥(메인 흐름)에 둬야 Streamlit이 이 입력창을 화면 하단에 고정해 준다.
    # 탭 안에 있으면 그냥 그 위치에 인라인으로 그려져서 위치가 애매해진다.
    prompt = st.chat_input(
        "메시지를 입력하고 Enter (예: 벤치프레스 5세트 8회 60kg 기록해줘)",
        key="global_chat_input",
    )
    if prompt:
        handle_chat_prompt(prompt)

    with st.sidebar:
        st.markdown(f"👤 **{current_user}** 님")
        if st.button("다른 사람으로 전환", key="switch_user_btn", use_container_width=True):
            st.session_state.pop("current_profile", None)
            st.rerun()
        st.divider()

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

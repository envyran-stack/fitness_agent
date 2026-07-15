"""LangChain tools for the fitness agent."""

from __future__ import annotations

import json
import os
import smtplib
from collections import Counter
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from langchain_core.tools import tool

from smtp_config import (
    default_report_recipient,
    resolve_report_recipient,
    smtp_config_status,
    smtp_settings,
    validate_smtp_password,
)
from body_metrics import (
    field_label_with_unit,
    format_metric_change,
    format_metric_value,
    summarize_body_entry,
)
from storage import (
    add_body_metric,
    add_body_metric_field,
    add_workout,
    delete_body_metric,
    delete_workout,
    get_body_metric_fields,
    get_records_filtered,
    get_records_within_days,
    remove_body_metric_field,
    update_body_metric,
    update_workout,
)


@tool
def save_body_metrics(
    weight_kg: float = -1,
    body_fat_pct: float = -1,
    muscle_mass_kg: float = -1,
    record_date: str = "",
    extra_metrics_json: str = "",
) -> str:
    """몸무게·체지방·근육량 등 신체 정보를 저장합니다. extra_metrics_json에 추가 항목 JSON(예: {\"허리둘레\": 78})을 넣을 수 있습니다."""
    metrics: dict[str, float] = {}
    if weight_kg >= 0:
        metrics["weight_kg"] = weight_kg
    if body_fat_pct >= 0:
        metrics["body_fat_pct"] = body_fat_pct
    if muscle_mass_kg >= 0:
        metrics["muscle_mass_kg"] = muscle_mass_kg

    if extra_metrics_json.strip():
        try:
            extra = json.loads(extra_metrics_json)
        except json.JSONDecodeError as exc:
            return f"신체 정보 저장 실패: extra_metrics_json JSON 오류 ({exc})"
        if not isinstance(extra, dict):
            return "신체 정보 저장 실패: extra_metrics_json은 객체(JSON)여야 합니다."

        fields = get_body_metric_fields()
        label_to_key = {str(f["label"]): str(f["key"]) for f in fields}
        key_set = {str(f["key"]) for f in fields}
        for raw_key, value in extra.items():
            if value is None:
                continue
            key = label_to_key.get(str(raw_key), str(raw_key))
            if key in key_set:
                metrics[key] = float(value)

    if not metrics:
        return "신체 정보 저장 실패: 저장할 측정값이 없습니다."

    entry = add_body_metric(metrics, record_date or None)
    fields = get_body_metric_fields()
    return f"신체 정보 저장 완료 ({entry['date']}): {summarize_body_entry(entry, fields)}"


@tool
def save_workout(
    exercise: str,
    sets: int,
    reps: int,
    weight_kg: float = 0.0,
    record_date: str = "",
) -> str:
    """운동 종목, 세트 수, 반복 횟수, 중량(kg)을 저장합니다. 운동할 때마다 사용하세요."""
    entry = add_workout(exercise, sets, reps, weight_kg, record_date or None)
    weight_text = f", {entry['weight_kg']}kg" if entry["weight_kg"] else ""
    return (
        f"운동 기록 저장 완료 ({entry['date']}): "
        f"{entry['exercise']} {entry['sets']}세트 x {entry['reps']}회{weight_text}"
    )


@tool
def update_body_metrics(
    record_id: str,
    weight_kg: float = -1,
    body_fat_pct: float = -1,
    muscle_mass_kg: float = -1,
    record_date: str = "",
    extra_metrics_json: str = "",
) -> str:
    """기존 신체 기록을 수정합니다. record_id는 get_fitness_history 조회 결과의 id 필드를 사용하세요."""
    metrics: dict[str, float] = {}
    if weight_kg >= 0:
        metrics["weight_kg"] = weight_kg
    if body_fat_pct >= 0:
        metrics["body_fat_pct"] = body_fat_pct
    if muscle_mass_kg >= 0:
        metrics["muscle_mass_kg"] = muscle_mass_kg

    if extra_metrics_json.strip():
        try:
            extra = json.loads(extra_metrics_json)
        except json.JSONDecodeError as exc:
            return f"신체 기록 수정 실패: extra_metrics_json JSON 오류 ({exc})"
        if isinstance(extra, dict):
            fields = get_body_metric_fields()
            label_to_key = {str(f["label"]): str(f["key"]) for f in fields}
            key_set = {str(f["key"]) for f in fields}
            for raw_key, value in extra.items():
                if value is None:
                    continue
                key = label_to_key.get(str(raw_key), str(raw_key))
                if key in key_set:
                    metrics[key] = float(value)

    if not metrics:
        return "신체 기록 수정 실패: 수정할 측정값이 없습니다."

    entry = update_body_metric(record_id, metrics, record_date or None)
    if entry is None:
        return f"신체 기록 수정 실패: id '{record_id}' 를 찾을 수 없습니다."
    fields = get_body_metric_fields()
    return f"신체 정보 수정 완료 ({entry['date']}, id={entry['id']}): {summarize_body_entry(entry, fields)}"


@tool
def manage_body_metric_fields(action: str, label: str = "", unit: str = "", key: str = "") -> str:
    """신체 측정 항목을 추가·조회·삭제합니다. action: list | add | remove"""
    action = action.strip().lower()
    if action == "list":
        fields = get_body_metric_fields()
        lines = [
            f"- {field['label']} ({field.get('unit') or '-'}) [key={field['key']}]"
            + (" (기본)" if field.get("builtin") else "")
            for field in fields
        ]
        return "신체 측정 항목:\n" + "\n".join(lines)

    if action == "add":
        if not label.strip():
            return "항목 추가 실패: label(항목 이름)이 필요합니다."
        field = add_body_metric_field(label, unit)
        return f"측정 항목 추가: {field['label']} ({field.get('unit') or '-'}) [key={field['key']}]"

    if action == "remove":
        if not key.strip():
            return "항목 삭제 실패: key가 필요합니다. manage_body_metric_fields(action='list')로 key를 확인하세요."
        try:
            if remove_body_metric_field(key.strip()):
                return f"측정 항목 '{key}' 을(를) 삭제했습니다."
            return f"측정 항목 삭제 실패: key '{key}' 를 찾을 수 없습니다."
        except ValueError as exc:
            return str(exc)

    return "action은 list, add, remove 중 하나여야 합니다."


@tool
def update_workout_record(
    record_id: str,
    exercise: str,
    sets: int,
    reps: int,
    weight_kg: float = 0.0,
    record_date: str = "",
) -> str:
    """기존 운동 기록을 수정합니다. record_id는 get_fitness_history 조회 결과의 id 필드를 사용하세요."""
    entry = update_workout(
        record_id,
        exercise,
        sets,
        reps,
        weight_kg,
        record_date or None,
    )
    if entry is None:
        return f"운동 기록 수정 실패: id '{record_id}' 를 찾을 수 없습니다."
    weight_text = f", {entry['weight_kg']}kg" if entry["weight_kg"] else ""
    return (
        f"운동 기록 수정 완료 ({entry['date']}, id={entry['id']}): "
        f"{entry['exercise']} {entry['sets']}세트 x {entry['reps']}회{weight_text}"
    )


@tool
def delete_fitness_record(record_type: str, record_id: str) -> str:
    """신체 또는 운동 기록을 삭제합니다. record_type은 'body' 또는 'workout' 입니다."""
    kind = record_type.strip().lower()
    if kind in {"body", "body_metric", "body_metrics"}:
        if delete_body_metric(record_id):
            return f"신체 기록(id={record_id})을 삭제했습니다."
        return f"신체 기록 삭제 실패: id '{record_id}' 를 찾을 수 없습니다."
    if kind in {"workout", "workouts"}:
        if delete_workout(record_id):
            return f"운동 기록(id={record_id})을 삭제했습니다."
        return f"운동 기록 삭제 실패: id '{record_id}' 를 찾을 수 없습니다."
    return "record_type은 'body' 또는 'workout' 이어야 합니다."


@tool
def get_fitness_history(days: int = 30, start_date: str = "", end_date: str = "") -> str:
    """신체·운동 기록을 조회합니다.
    - 특정 날짜 하나를 물으면 start_date와 end_date를 같은 값(YYYY-MM-DD)으로 지정하세요.
    - 특정 기간을 물으면 start_date~end_date를 지정하세요. (지정 시 days는 무시됩니다)
    - 기간 지정 없이 "최근 N일"이면 days만 사용하세요. days=0 이면 전체 기간.
    """
    start = start_date.strip()
    end = end_date.strip()
    if start or end:
        records = get_records_filtered(
            days=None,
            start_date=start or end,
            end_date=end or start,
        )
    elif days <= 0:
        records = get_records_filtered(days=None)
    else:
        records = get_records_within_days(days)
    return json.dumps(records, ensure_ascii=False, indent=2)


@tool
def analyze_fitness_trends(days: int = 7, start_date: str = "", end_date: str = "") -> str:
    """체중·체지방·근육량 변화와 운동 빈도를 분석합니다.
    - "6월 20일부터", "지난주부터" 등 특정 시작일 기준 분석이면 start_date(YYYY-MM-DD)를 지정하세요. end_date를 비워두면 오늘까지로 계산합니다.
    - 특정 기간(예: 6/1~6/30) 분석이면 start_date~end_date를 지정하세요. (start_date 또는 end_date 지정 시 days는 무시됩니다)
    - 기간 지정 없이 "최근 N일"/"이번 주"/"한 달" 이면 days만 사용하세요. days=0 이면 전체 기간.
    """
    start = start_date.strip()
    end = end_date.strip()
    if start or end:
        records = get_records_filtered(
            days=None,
            start_date=start or None,
            end_date=end or None,
        )
        if start and end:
            period_label = f"{start} ~ {end}"
        elif start:
            period_label = f"{start} ~ 오늘"
        else:
            period_label = f"~ {end}"
    elif days <= 0:
        records = get_records_filtered(days=None)
        period_label = "전체 기간"
    else:
        records = get_records_within_days(days)
        period_label = f"최근 {days}일"

    body = records["body_metrics"]
    workouts = records["workouts"]
    fields = get_body_metric_fields()

    lines: list[str] = [f"=== {period_label} 피트니스 분석 ==="]

    if body:
        first, last = body[0], body[-1]
        metric_parts: list[str] = []
        for field in fields:
            key = field["key"]
            unit = str(field.get("unit") or "")
            if key in first and key in last:
                first_val = float(first[key])
                last_val = float(last[key])
                metric_parts.append(
                    f"{field['label']} {format_metric_value(first_val, unit)}→"
                    f"{format_metric_value(last_val, unit)} "
                    f"({format_metric_change(first_val, last_val, unit)})"
                )
        if metric_parts:
            lines.append(f"신체 ({first['date']} → {last['date']}): " + ", ".join(metric_parts))
        else:
            lines.append(f"신체 ({first['date']} → {last['date']}): 측정값 없음")
        lines.append(f"신체 측정 횟수: {len(body)}회 (목표: 주 1회)")
    else:
        lines.append("신체 기록 없음 — 몸무게·체지방·근육량 등을 입력해 주세요.")

    if workouts:
        workout_days = len({w["date"] for w in workouts})
        exercise_counts = Counter(w["exercise"] for w in workouts)
        top_exercises = ", ".join(f"{name} {cnt}회" for name, cnt in exercise_counts.most_common(5))
        lines.append(f"운동 세션: {len(workouts)}회 / 운동한 날: {workout_days}일 (목표: 주 4일)")
        lines.append(f"종목별: {top_exercises}")
    else:
        lines.append("운동 기록 없음 — 운동 종목·세트·횟수를 입력해 주세요.")

    week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    this_week_workouts = [w for w in workouts if w["date"] >= week_start]
    this_week_days = len({w["date"] for w in this_week_workouts})
    lines.append(f"이번 주(월~오늘) 운동: {this_week_days}/4일, {len(this_week_workouts)}세션")

    return "\n".join(lines)


def _smtp_settings() -> tuple[str, int, str, str]:
    return smtp_settings()


@tool
def send_fitness_report(
    to_email: str = "",
    report_text: str = "",
    report_period: str = "",
) -> str:
    """피트니스 분석 리포트를 이메일로 발송합니다. to_email은 비워 두세요(.env REPORT_EMAIL 사용). report_text에 LLM이 작성한 리포트 본문을 넣으세요."""
    config = smtp_config_status()
    if not config["ok"]:
        return f"메일 발송 실패: {config['summary']}\n\n{config['detail']}"

    smtp_host, smtp_port, smtp_user, smtp_password = _smtp_settings()
    raw_password = os.getenv("SMTP_PASSWORD", "")
    password_issue = validate_smtp_password(raw_password)
    if password_issue:
        return f"메일 발송 실패: {password_issue}"

    requested = to_email.strip() if to_email else ""
    recipient = resolve_report_recipient(to_email)

    if not report_text.strip():
        return "메일 발송 실패: report_text가 비어 있습니다. analyze_fitness_trends 결과를 넣어 주세요."

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg["Subject"] = (
        f"[Fitness Agent] {report_period.strip() or '주간'} 리포트 ({date.today().isoformat()})"
    )
    msg.attach(MIMEText(report_text, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [recipient], msg.as_string())
    except smtplib.SMTPAuthenticationError:
        return (
            "메일 발송 실패: Gmail 로그인 거부.\n"
            "fitness_agent/.env 의 SMTP_PASSWORD 를 Gmail '앱 비밀번호' 16자리로 교체하세요.\n"
            "발급: https://myaccount.google.com/apppasswords (2단계 인증 필요)"
        )
    except OSError as exc:
        return (
            f"메일 발송 실패: SMTP 서버 연결 오류 ({exc}). "
            f"SMTP_HOST가 smtp.gmail.com 인지 확인하세요. (현재: {smtp_host})"
        )
    except smtplib.SMTPException as exc:
        return f"메일 발송 실패: {exc}"

    if requested and requested.lower() != recipient.lower():
        return (
            f"리포트를 {recipient} 로 발송했습니다. "
            f"(입력 주소 '{requested}' 는 placeholder로 무시됨)"
        )
    return f"리포트를 {recipient} 로 발송했습니다."


AGENT_TOOLS = [
    save_body_metrics,
    save_workout,
    update_body_metrics,
    update_workout_record,
    delete_fitness_record,
    manage_body_metric_fields,
    get_fitness_history,
    analyze_fitness_trends,
    send_fitness_report,
]

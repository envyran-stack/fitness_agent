"""Fitness Agent — AgentExecutor (14.1 패턴)."""

from __future__ import annotations

import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from smtp_config import default_report_recipient
from tools import AGENT_TOOLS, analyze_fitness_trends, get_fitness_history, send_fitness_report

load_dotenv(Path(__file__).resolve().parent / ".env")


def build_system_prompt() -> str:
    recipient = default_report_recipient()
    today = date.today()
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    return f"""너는 사용자의 피트니스 코치 AI입니다. 오늘 날짜는 {today.isoformat()} ({weekday_kr}요일) 입니다. 질문에 맞는 도구를 골라 사용하세요.

- 몸무게·체지방·근육량 저장 → save_body_metrics (추가 항목은 extra_metrics_json)
- 측정 항목 추가/조회/삭제 → manage_body_metric_fields (action: list | add | remove)
- 운동(종목, 세트, 횟수, 중량) 저장 → save_workout
- 신체 기록 수정 → update_body_metrics (record_id 필요, get_fitness_history로 id 확인)
- 운동 기록 수정 → update_workout_record (record_id 필요)
- 기록 삭제 → delete_fitness_record (record_type: body 또는 workout)
- 기록 조회 → get_fitness_history (특정 날짜·기간을 물으면 start_date·end_date를 YYYY-MM-DD로 지정)
- 추세·기간 분석 → analyze_fitness_trends (days=0 이면 전체 기간. "6월 20일부터", "지난주부터" 등 특정 시작일 기준 분석이면 반드시 start_date를 지정하세요 — days로 대충 계산하지 마세요. 특정 기간이면 start_date~end_date를 지정하세요)
- 분석 결과 이메일 발송 → send_fitness_report (먼저 analyze_fitness_trends로 수치 확인 후, LLM이 작성한 상세 코칭 리포트를 report_text에 넣으세요)

규칙:
1. 숫자 계산·통계는 도구 결과를 그대로 활용하고, 추측하지 마세요.
2. 데이터가 없으면 솔직히 알리고 기록을 권하세요.
3. 메일 발송 요청 시 analyze_fitness_trends와 get_fitness_history로 데이터를 확인한 뒤, 수치·추세·코칭 조언을 포함한 **상세 리포트 본문**을 직접 작성하고 send_fitness_report를 반드시 호출하세요. analyze_fitness_trends 출력만 그대로 보내지 마세요.
4. send_fitness_report의 to_email은 항상 ""(빈 문자열)로 두세요. 수신 주소는 시스템이 {recipient} 로 자동 설정합니다.
5. user@example.com 등 예시·임의 이메일을 절대 사용하지 마세요.
6. "전체 기간"·"전체 운동" 요청은 analyze_fitness_trends(days=0)을 사용하세요.
7. **날짜가 언급된 질문**("7월 5일", "어제", "그제", "이번 주 월요일", "지난주 화요일", "6월 20일부터", "6/10~6/15" 등)은 오늘 날짜({today.isoformat()})를 기준으로 실제 날짜(YYYY-MM-DD)를 직접 계산한 뒤, 조회는 get_fitness_history(start_date=..., end_date=...), 분석은 analyze_fitness_trends(start_date=..., end_date=...)를 사용해 그 날짜(들)만 반영해서 답하세요. days 파라미터만으로는 특정 날짜/시작일을 정확히 반영할 수 없으니, 날짜가 하나라도 지정되면 반드시 start_date/end_date를 우선 사용하세요. 연도가 빠져 있으면 오늘과 가장 가까운 연도로 추정하세요.
8. 조회 결과에 해당 날짜 기록이 없으면 추측하지 말고 "해당 날짜에는 기록이 없습니다"라고 솔직히 답하세요.
9. 한국어로 친절하고 동기부여가 되게 답하세요."""


def is_email_request(text: str) -> bool:
    """메일 발송 의도가 포함된 사용자 메시지인지 판별."""
    lowered = text.lower()
    mail_keywords = ("메일", "이메일", "e-mail", "email", "mail")
    send_keywords = ("보내", "발송", "전송", "송신", "send")
    has_mail = any(k in lowered or k in text for k in mail_keywords)
    has_send = any(k in text or k in lowered for k in send_keywords)
    return has_mail and has_send


def infer_analysis_days(text: str) -> int | None:
    """사용자 메시지에서 분석 기간(일) 추론. None = 기간 명시 없음."""
    if any(k in text for k in ("전체", "전 기간", "전체 운동", "모든 기록", "전체 기간")):
        return 0
    if "all time" in text.lower() or "entire" in text.lower():
        return 0
    if any(k in text for k in ("이번 주", "주간", "일주일", "7일")):
        return 7
    if any(k in text for k in ("한 달", "1개월", "30일")):
        return 30

    match = re.search(r"(\d+)\s*일", text)
    if match:
        return int(match.group(1))

    return None


def _is_email_send_confirmation(text: str) -> bool:
    """이메일 발송 완료 안내 메시지인지 판별 (분석 답변과 구분)."""
    head = str(text).strip()[:160]
    return head.startswith("'") and "로 발송했습니다" in head


def _last_assistant_content(chat_history: list | None) -> str:
    if not chat_history:
        return ""
    for msg in reversed(chat_history):
        if isinstance(msg, AIMessage):
            content = str(msg.content).strip()
            if content and not _is_email_send_confirmation(content):
                return content
    return ""


def _is_referential_email_request(text: str) -> bool:
    """이미 나눈 대화·답변을 그대로 메일로 옮기려는 요청."""
    referential_phrases = (
        "방금",
        "위 내용",
        "말한 내용",
        "말해준",
        "말한",
        "얘기한",
        "대화 내용",
        "대화한",
        "분석한 내용",
        "분석한 거",
        "방금 분석",
        "설명한",
        "앞에서",
        "이 내용",
        "그 내용",
        "요약한",
        "적어준",
        "알려준",
    )
    return any(p in text for p in referential_phrases)


def _is_new_analysis_email_request(text: str) -> bool:
    """DB에서 새로 분석한 리포트를 메일로 받으려는 요청."""
    if _is_referential_email_request(text):
        return False

    if infer_analysis_days(text) is not None:
        return True

    new_analysis_phrases = (
        "분석해서",
        "분석해줘",
        "분석해 ",
        "분석 후",
        "분석한 뒤",
        "추세",
        "통계",
        "리포트",
        "weekly",
        "report",
        "전체",
        "전 기간",
        "이번 주",
        "한 달",
        "피트니스",
        "운동 기록",
        "신체",
    )
    return any(p in text for p in new_analysis_phrases)


def classify_email_intent(text: str, chat_history: list | None) -> str:
    """
    메일 본문 유형 분류.
    - context: 방금 대화·설명한 내용을 그대로 메일로
    - analysis: 기간별 피트니스 데이터 분석 리포트
    - mixed: 대화 맥락 + 데이터 분석 모두 (명시적 이중 요청)
    """
    if _is_referential_email_request(text):
        wants_extra_stats = (
            infer_analysis_days(text) is not None
            and any(p in text for p in ("추가", "함께", "같이", "포함", "더해"))
        )
        return "mixed" if wants_extra_stats else "context"

    if _is_new_analysis_email_request(text):
        return "analysis"

    if chat_history and _last_assistant_content(chat_history):
        return "context"

    return "analysis"


def chat_history_from_ui_messages(
    messages: list[dict] | None,
    chat_history: list | None = None,
) -> list:
    """Streamlit messages와 chat_history를 합쳐 메일용 대화 맥락 구성."""
    merged: list = []

    if messages and len(messages) > 1:
        for msg in messages[:-1]:
            role = msg.get("role")
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            if role == "user":
                merged.append(HumanMessage(content=content))
            elif role == "assistant":
                if _is_email_send_confirmation(content):
                    continue
                merged.append(AIMessage(content=content))

    if chat_history:
        if len(chat_history) > len(merged):
            return list(chat_history)
        if len(chat_history) == len(merged) and _last_assistant_content(chat_history):
            if len(str(_last_assistant_content(chat_history))) > len(str(_last_assistant_content(merged))):
                return list(chat_history)

    return merged


def _analysis_period_label(days: int) -> str:
    return "전체 기간" if days <= 0 else f"최근 {days}일"


def _format_chat_context(chat_history: list | None, *, limit: int = 12) -> str:
    if not chat_history:
        return ""
    snippets: list[str] = []
    for msg in chat_history[-limit:]:
        if isinstance(msg, HumanMessage):
            snippets.append(f"사용자: {msg.content}")
        elif isinstance(msg, AIMessage):
            snippets.append(f"코치: {msg.content[:4000]}")
    return "\n\n".join(snippets)


def compose_context_email(
    *,
    user_request: str = "",
    chat_history: list | None = None,
) -> str:
    """대화 맥락(방금 나눈 내용)을 이메일 본문으로 변환. 통계 Tool·LLM 재분석 없음."""
    last_reply = _last_assistant_content(chat_history)
    if not last_reply:
        chat_context = _format_chat_context(chat_history)
        if not chat_context.strip():
            raise ValueError(
                "이메일로 보낼 이전 대화가 없습니다. "
                "먼저 AI 코치와 분석·질문을 나눈 뒤 '방금 말한 내용 메일로 보내줘'라고 요청해 주세요."
            )
        last_reply = chat_context

    return f"## 피트니스 코치 메모\n\n{last_reply}"


def compose_email_report(
    *,
    days: int = 7,
    user_request: str = "",
    chat_history: list | None = None,
    include_stats: bool = True,
) -> str:
    """도구 통계 + LLM 코칭 코멘트로 이메일 본문 생성."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.")

    period = _analysis_period_label(days)
    trend_summary = analyze_fitness_trends.invoke({"days": days}) if include_stats else ""
    history_json = get_fitness_history.invoke({"days": days}) if include_stats else ""
    chat_context = _format_chat_context(chat_history)

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """너는 사용자의 피트니스 코치입니다. 이메일로 보낼 본문을 작성하세요.

규칙:
- **사용자 요청**을 최우선으로 반영하세요.
- 제공된 수치·기록만 사용하고 추측하지 마세요.
- 통계 요약이 제공된 경우에만 신체/운동 수치 분석을 포함하세요.
- 이전 대화 맥락이 있으면 반드시 반영하세요.
- 고정된 7일 리포트 형식에 억지로 맞추지 마세요.
- 한국어, plain text (## 소제목 사용 가능).
- 이메일 본문만 출력하세요.""",
            ),
            (
                "human",
                """사용자 요청: {user_request}
분석 기간: {period}

[통계 요약]
{trend_summary}

[상세 기록]
{history_json}

[이전 대화 맥락]
{chat_context}

위 정보를 바탕으로 사용자 요청에 맞는 이메일 본문을 작성하세요.""",
            ),
        ]
    )

    response = (prompt | llm).invoke(
        {
            "period": period if include_stats else "대화/요청 기반",
            "user_request": user_request or "피트니스 관련 내용을 이메일로 보내달라",
            "trend_summary": trend_summary or "(통계 분석 없음 — 대화/요청 내용 중심)",
            "history_json": (history_json[:12000] if history_json else "(상세 기록 없음)"),
            "chat_context": chat_context or "(없음)",
        }
    )
    body = str(response.content).strip()
    if include_stats and trend_summary:
        return f"{body}\n\n---\n[참고 통계]\n{trend_summary}"
    return body


def send_fitness_report_direct(
    *,
    days: int | None = None,
    to_email: str = "",
    user_request: str = "",
    chat_history: list | None = None,
    ui_messages: list[dict] | None = None,
) -> str:
    """요청 유형에 맞는 이메일 본문 작성 후 .env 수신 주소로 발송."""
    effective_history = chat_history_from_ui_messages(ui_messages, chat_history)
    intent = classify_email_intent(user_request, effective_history)
    analysis_days = days if days is not None else infer_analysis_days(user_request)

    if intent == "context":
        report_text = compose_context_email(
            user_request=user_request,
            chat_history=effective_history,
        )
        period = "코치 대화 요약"
    elif intent == "mixed":
        report_days = analysis_days if analysis_days is not None else 7
        report_text = compose_email_report(
            days=report_days,
            user_request=user_request,
            chat_history=effective_history,
            include_stats=True,
        )
        period = _analysis_period_label(report_days)
    else:
        report_days = analysis_days if analysis_days is not None else 7
        report_text = compose_email_report(
            days=report_days,
            user_request=user_request,
            chat_history=effective_history,
            include_stats=True,
        )
        period = _analysis_period_label(report_days)

    send_result = send_fitness_report.invoke(
        {
            "to_email": to_email,
            "report_text": report_text,
            "report_period": period,
        }
    )

    recipient = default_report_recipient()
    return (
        f"'{period}' 내용을 {recipient} 로 발송했습니다.\n\n"
        f"{report_text}\n\n---\n{send_result}"
    )


def build_executor(*, verbose: bool = False) -> AgentExecutor:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.")

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", build_system_prompt()),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, AGENT_TOOLS, prompt)
    return AgentExecutor(
        agent=agent,
        tools=AGENT_TOOLS,
        verbose=verbose,
        max_iterations=8,
    )


@lru_cache(maxsize=1)
def get_executor() -> AgentExecutor:
    """AgentExecutor 싱글톤 — Streamlit rerun 시 매번 재생성하지 않음."""
    return build_executor(verbose=False)


def run_agent(
    user_input: str,
    chat_history: list | None = None,
    *,
    verbose: bool = False,
    executor: AgentExecutor | None = None,
) -> str:
    agent_executor = executor or get_executor()
    if verbose:
        agent_executor.verbose = True

    result = agent_executor.invoke(
        {
            "input": user_input,
            "chat_history": chat_history or [],
        }
    )
    return result["output"]


def generate_weekly_report_email(verbose: bool = False) -> str:
    """스케줄러용: 분석 후 메일 발송."""
    _ = verbose
    return send_fitness_report_direct(
        user_request="이번 주 분석해서 메일로 보내줘",
    )

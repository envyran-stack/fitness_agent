"""Gmail SMTP 설정 검증 및 연결 테스트."""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

GMAIL_APP_PASSWORD_LEN = 16
GMAIL_APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"

# LLM이 자주 쓰는 placeholder — .env 기본 수신 주소로 대체
BLOCKED_RECIPIENT_EMAILS = frozenset(
    {
        "user@example.com",
        "test@test.com",
        "your@gmail.com",
        "email@example.com",
        "me@example.com",
        "user@gmail.com",
    }
)


def normalize_smtp_password(password: str) -> str:
    """Gmail 앱 비밀번호는 'abcd efgh ijkl mnop' 형태로 주어지므로 공백 제거."""
    return password.replace(" ", "").strip()


def smtp_settings() -> tuple[str, int, str, str]:
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = normalize_smtp_password(os.getenv("SMTP_PASSWORD", ""))
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if "@" in smtp_host and not smtp_host.startswith("smtp."):
        smtp_host = "smtp.gmail.com"

    return smtp_host, smtp_port, smtp_user, smtp_password


def default_report_recipient() -> str:
    """REPORT_EMAIL → SMTP_USER 순으로 기본 수신 주소 반환."""
    _, _, smtp_user, _ = smtp_settings()
    return os.getenv("REPORT_EMAIL", smtp_user).strip() or smtp_user


def resolve_report_recipient(to_email: str | None) -> str:
    """
    수신 이메일 결정. 빈 값·placeholder·example 도메인은 .env 기본 주소 사용.
    사용자가 채팅에서 명시한 실제 주소만 허용.
    """
    default = default_report_recipient()
    if not to_email or not str(to_email).strip():
        return default

    candidate = str(to_email).strip()
    lowered = candidate.lower()

    if lowered in BLOCKED_RECIPIENT_EMAILS:
        return default
    if "@example." in lowered or lowered.endswith("example.com"):
        return default
    if lowered.startswith("your@") or "placeholder" in lowered:
        return default

    return candidate


def validate_smtp_password(password: str) -> str | None:
    """앱 비밀번호 형식이 아니면 설정 안내 메시지 반환."""
    if not password:
        return "SMTP_PASSWORD가 비어 있습니다."

    cleaned = normalize_smtp_password(password)
    if len(cleaned) != GMAIL_APP_PASSWORD_LEN:
        return (
            f"SMTP_PASSWORD 길이가 {len(cleaned)}자입니다. "
            f"Gmail 앱 비밀번호는 {GMAIL_APP_PASSWORD_LEN}자(공백 제외)여야 합니다. "
            "일반 Gmail 비밀번호는 사용할 수 없습니다."
        )
    if not cleaned.isalnum():
        return (
            "SMTP_PASSWORD에 특수문자(@ 등)가 포함되어 있습니다. "
            "Gmail '앱 비밀번호' 16자리(영문/숫자만)를 입력하세요."
        )
    return None


def smtp_config_status() -> dict[str, str | bool]:
    smtp_host, smtp_port, smtp_user, smtp_password = smtp_settings()
    recipient = default_report_recipient()

    if not smtp_user:
        return {
            "ok": False,
            "summary": "SMTP_USER 미설정",
            "detail": "fitness_agent/.env 에 SMTP_USER=your@gmail.com 을 추가하세요.",
        }

    password_issue = validate_smtp_password(smtp_password)
    if password_issue:
        return {
            "ok": False,
            "summary": "앱 비밀번호 형식 오류",
            "detail": (
                f"{password_issue}\n\n"
                "설정 방법:\n"
                "1. Google 계정 → 보안 → 2단계 인증 켜기\n"
                f"2. 앱 비밀번호 생성: {GMAIL_APP_PASSWORD_URL}\n"
                "3. fitness_agent/.env 의 SMTP_PASSWORD 를 16자리 앱 비밀번호로 교체\n"
                "4. 웹앱 재시작 (./run_web.sh)"
            ),
        }

    return {
        "ok": True,
        "summary": f"설정됨 ({smtp_user})",
        "detail": f"수신: {recipient} | SMTP: {smtp_host}:{smtp_port}",
    }


def test_smtp_connection() -> str:
    status = smtp_config_status()
    if not status["ok"]:
        return f"SMTP 설정 오류\n\n{status['detail']}"

    smtp_host, smtp_port, smtp_user, smtp_password = smtp_settings()
    recipient = default_report_recipient()

    msg = MIMEText(
        "Fitness Agent SMTP 연결 테스트 메일입니다.\n설정이 정상입니다.",
        "plain",
        "utf-8",
    )
    msg["Subject"] = "[Fitness Agent] SMTP 테스트"
    msg["From"] = smtp_user
    msg["To"] = recipient

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [recipient], msg.as_string())
    except smtplib.SMTPAuthenticationError:
        return (
            "SMTP 로그인 실패: 앱 비밀번호가 거부되었습니다.\n\n"
            "다시 확인:\n"
            "1. 2단계 인증이 켜져 있는지\n"
            f"2. 새 앱 비밀번호 발급: {GMAIL_APP_PASSWORD_URL}\n"
            "3. .env 의 SMTP_PASSWORD 교체 후 웹앱 재시작"
        )
    except OSError as exc:
        return f"SMTP 서버 연결 실패 ({exc}). SMTP_HOST=smtp.gmail.com 인지 확인하세요."
    except smtplib.SMTPException as exc:
        return f"SMTP 오류: {exc}"

    return f"테스트 메일 발송 성공 → {recipient}"

"""Fitness Agent — 터미널 대화형 CLI."""

from __future__ import annotations

import argparse
import sys

from langchain_core.messages import AIMessage, HumanMessage

from agent import (
    generate_weekly_report_email,
    is_email_request,
    run_agent,
    send_fitness_report_direct,
)
from smtp_config import test_smtp_connection


def chat_loop(*, verbose: bool = False) -> None:
    print("Fitness Agent (터미널)")
    print("종료: quit / exit / q")
    print("예: 몸무게 72kg 체지방 18% 근육량 32kg 저장해줘")
    print("예: 벤치프레스 5세트 8회 60kg 기록해줘")
    print("예: 이번 주 분석해서 메일 보내줘\n")

    chat_history: list = []

    while True:
        try:
            user_input = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("종료합니다.")
            break

        try:
            if is_email_request(user_input):
                response = send_fitness_report_direct(
                    user_request=user_input,
                    chat_history=chat_history,
                )
            else:
                response = run_agent(user_input, chat_history=chat_history, verbose=verbose)
        except RuntimeError as exc:
            print(f"오류: {exc}")
            continue
        except Exception as exc:
            print(f"Agent 실행 오류: {exc}")
            continue

        chat_history.append(HumanMessage(content=user_input))
        chat_history.append(AIMessage(content=response))
        print(f"\nAgent> {response}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fitness Agent 터미널 CLI")
    parser.add_argument(
        "message",
        nargs="?",
        help="한 번만 실행할 메시지 (없으면 대화 모드)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="주간 분석 후 이메일 발송",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Agent Tool 호출 로그 출력",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Gmail SMTP 연결 및 테스트 메일 발송",
    )
    args = parser.parse_args()

    if args.test_email:
        print(test_smtp_connection())
        return

    if args.report:
        print(generate_weekly_report_email(verbose=args.verbose))
        return

    if args.message:
        if is_email_request(args.message):
            print(
                send_fitness_report_direct(
                    user_request=args.message,
                )
            )
        else:
            print(run_agent(args.message, verbose=args.verbose))
        return

    chat_loop(verbose=args.verbose)


if __name__ == "__main__":
    main()

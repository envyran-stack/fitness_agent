"""주간 자동 리포트 스케줄러 (선택).

사용 예:
  python scheduler.py              # 즉시 1회 실행
  python scheduler.py --daemon     # 매주 일요일 09:00 자동 실행
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


def run_once() -> None:
    from agent import generate_weekly_report_email

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 주간 리포트 생성 중...")
    result = generate_weekly_report_email(verbose=True)
    print(result)


def run_daemon() -> None:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("APScheduler 미설치: pip install apscheduler")
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_once,
        CronTrigger(day_of_week="sun", hour=9, minute=0),
        id="weekly_fitness_report",
    )
    print("스케줄러 시작 — 매주 일요일 09:00 리포트 발송 (Ctrl+C 종료)")
    scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fitness Agent 주간 리포트")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="매주 일요일 09:00 자동 실행",
    )
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
    else:
        run_once()


if __name__ == "__main__":
    main()

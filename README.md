# Fitness Agent

몸무게·체지방·근육량과 운동 기록을 **Streamlit 웹앱**에서 입력하고, **LangChain Agent**가 분석·이메일 발송을 수행합니다.

## 구조

```
fitness_agent/
├── app.py          # Streamlit 웹앱 (폼 입력 + AI 채팅)
├── agent.py        # AgentExecutor (14.1 패턴)
├── tools.py        # @tool 5개
├── storage.py      # JSON 저장
├── scheduler.py    # 주간 자동 메일 (선택)
└── data/fitness.json
```

## 설치

```bash
cd fitness_agent
pip install -r requirements.txt
cp .env.example .env
# .env 에 OPENAI_API_KEY, SMTP 설정 입력
```

## 실행

### 웹앱 (Streamlit)

**Connection Error 발생 시 — 아래 순서 그대로:**

```bash
conda activate day15
cd "/Users/user/Desktop/cursor 연습/fitness_agent"
pip install -r requirements.txt          # streamlit 안정 버전 설치
./run_web.sh --install                   # 최초 1회 또는 패키지 꼬였을 때
./run_web.sh                             # 실행
```

브라우저: **http://localhost:8501**

### 📱 폰 브라우저 접속 (같은 Wi-Fi)

PC에서 서버를 켠 뒤, **휴대폰 Safari/Chrome**에서 PC의 IP로 접속합니다.

```bash
conda activate day15
cd "/Users/user/Desktop/cursor 연습/fitness_agent"
./run_web_mobile.sh
# 또는
./run_web.sh --mobile
```

터미널에 표시되는 주소 예: **http://192.168.0.12:8501** → 폰 브라우저 주소창에 입력

**체크리스트**
1. PC와 폰이 **같은 Wi-Fi**에 연결
2. Mac **방화벽**에서 Python 연결 허용 (시스템 설정 → 네트워크 → 방화벽)
3. PC가 **절전/화면 꺼짐**이면 접속이 끊길 수 있음 — 사용 중 PC 켜 두기
4. **외부(4G/5G)에서 접속**하려면 공유기 포트포워딩 또는 클라우드 배포 필요 (별도 작업)

> segfault 원인: Python 3.13(base), pandas/pyarrow 차트, 파일 감시(watchdog).  
> 현재 앱은 pandas·차트 제거 + fileWatcher 비활성화로 안정화됨.

### 터미널 (CLI)

```bash
# 대화형 모드
python cli.py

# 한 번만 실행
python cli.py "이번 주 운동 분석해줘"

# 분석 + 이메일 (한 번에)
python cli.py --report
```

## 탭 구성

| 탭 | 기능 |
|----|------|
| 신체 기록 | 몸무게·체지방·근육량 폼 → Tool 직접 저장 |
| 운동 기록 | 종목·세트·횟수·중량 폼 → Tool 직접 저장 |
| 기록 조회 | 차트·테이블 + 주간 분석 |
| AI 코치 | Agent 채팅 — 분석·메일 발송 |

## 주간 자동 메일 (선택)

```bash
python scheduler.py           # 즉시 1회
python scheduler.py --daemon  # 매주 일요일 09:00
```

## Gmail 설정

1. Google 계정 → 2단계 인증 ON
2. [앱 비밀번호](https://myaccount.google.com/apppasswords) 생성
3. `.env`의 `SMTP_PASSWORD`에 앱 비밀번호 입력

## Agent Tool 목록

- `save_body_metrics` — 신체 정보 저장
- `save_workout` — 운동 기록 저장
- `get_fitness_history` — 기록 조회
- `analyze_fitness_trends` — 추세 분석
- `send_fitness_report` — 이메일 발송

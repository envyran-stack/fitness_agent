# Fitness Agent

몸무게·체지방·근육량과 운동 기록을 **Streamlit 웹앱**에서 입력하고, **LangChain Agent**가 분석·이메일 발송을 수행합니다.

## 구조

```
fitness_agent/
├── app.py          # Streamlit 웹앱 (폼 입력 + AI 채팅)
├── agent.py        # AgentExecutor (14.1 패턴)
├── tools.py        # @tool 5개
├── storage.py      # JSON 저장 (사용자별 파일 분리)
├── scheduler.py    # 주간 자동 메일 (선택)
├── docs/           # PWA 껍데기 (GitHub Pages로 배포, "앱처럼" 홈 화면에 추가)
│   ├── index.html
│   ├── manifest.json
│   ├── sw.js
│   └── icons/
└── data/
    ├── fitness.json        # 닉네임 없이(단독) 실행할 때 쓰는 기본 파일
    └── users/<닉네임>.json  # 사용자별 기록
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

## 📱 지인들과 "앱처럼" 함께 쓰기 (PWA 배포)

내 컴퓨터를 켜두지 않아도 지인들이 휴대폰에서 "다운로드해서 쓰는 앱"처럼 쓸 수 있게 하려면,
**① Streamlit Community Cloud에 배포 → ② GitHub Pages로 PWA 껍데기 배포 → ③ 링크 공유** 순서로 진행합니다.
비용은 0원(둘 다 무료 플랜)이며, 앱스토어 등록 없이 "홈 화면에 추가"로 아이콘이 생깁니다.

> ⚠️ **여러 명이 함께 쓰므로 알아둘 점**
> - AI 채팅(OpenAI API)은 **내 API 키로 과금**됩니다. 친한 사람 몇 명 기준으로는 보통 월 몇백~몇천 원 수준이지만, 사용량이 걱정되면 [OpenAI 사용량 한도](https://platform.openai.com/settings/organization/limits)를 설정해 두세요.
> - 각자 처음 접속 시 **닉네임**을 입력하면 이후 기록이 자동으로 분리 저장됩니다(비밀번호는 없음 — 신뢰할 수 있는 지인끼리 사용 전제).

### ① Streamlit Community Cloud에 앱 배포

1. 이 프로젝트를 GitHub에 푸시해 둡니다 (이미 되어 있다면 스킵).
2. [share.streamlit.io](https://share.streamlit.io) 접속 → GitHub 계정으로 로그인.
3. **New app** → 이 저장소 선택 → Main file path: `app.py` (저장소 안에 `fitness_agent/`가 하위 폴더라면 `fitness_agent/app.py`).
4. **Advanced settings**에서 Python 버전을 **3.12**로 선택.
5. **Secrets**에 `.streamlit/secrets.toml.example` 내용을 복사해서 실제 값(OpenAI 키, Gmail 앱 비밀번호 등)으로 채워 붙여넣기.
6. **Deploy** 클릭 → 몇 분 후 `https://xxxx.streamlit.app` 주소가 생성됩니다.

### ② GitHub Pages로 "앱 아이콘" 껍데기 배포

저장소의 `docs/` 폴더에 PWA 껍데기(아이콘·매니페스트·서비스워커)가 이미 준비되어 있습니다.

1. `docs/index.html`을 열어 `STREAMLIT_APP_URL` 값을 ①에서 받은 `https://xxxx.streamlit.app` 주소로 바꿔서 커밋·푸시합니다.
2. GitHub 저장소 → **Settings → Pages** → Source: **Deploy from a branch**, Branch: `main` / 폴더: `/docs` 선택 → Save.
3. 잠시 후 `https://<github-아이디>.github.io/<저장소명>/` 주소가 생성됩니다. 이게 지인들에게 공유할 링크입니다.

### ③ 지인들에게 공유하기

- 위 GitHub Pages 링크를 카톡 등으로 전달합니다.
- **iPhone (Safari)**: 링크 열기 → 공유 버튼 → **홈 화면에 추가**
- **Android (Chrome)**: 링크 열기 → 우측 상단 메뉴(⋮) → **앱 설치** 또는 **홈 화면에 추가**
- 처음 열면 닉네임을 물어보는데, 입력하고 나면 그 사람의 기록만 따로 저장됩니다.

### 로컬 개발과의 차이

| | 로컬 실행 (`./run_web.sh`) | 클라우드 배포 |
|---|---|---|
| API 키·SMTP | `.env` | Streamlit Cloud **Secrets** |
| 데이터 저장 | `data/users/<닉네임>.json` (내 컴퓨터) | 마찬가지지만 Streamlit Cloud 서버에 저장 (앱 재배포 시 초기화될 수 있음 — 중요 기록은 주기적으로 백업 권장) |
| 접속 | localhost / 같은 Wi-Fi | 어디서나 (인터넷만 있으면) |

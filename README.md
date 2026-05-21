# Lion Story Learning Backend

동화 기반 영어 학습 서비스를 위한 AI 콘텐츠 생성 파이프라인과 FastAPI 백엔드입니다.

이 프로젝트는 한국 어린이 영어 학습자를 대상으로 다음 콘텐츠를 자동 생성합니다.

- 영어 동화
- 동화 품질 judge 결과
- 페이지별 이미지 프롬프트 및 이미지 경로
- 장면 묘사 퀴즈
- 롤플레잉 미션
- 발음 평가 / 묘사 평가 / 롤플레잉 API

## 핵심 로직

전체 콘텐츠 생성 흐름은 `레벨별 긴 동화 기획 → 강별 Part 생성 → judge 평가 → 통과분 저장 → 학습 콘텐츠 생성` 구조입니다.

중요한 개념:

- `Level 1`, `Level 2`, `Level 3`은 서로 다른 난이도의 커리큘럼입니다.
- 각 Level 안의 `1강`, `2강`, `3강`은 서로 다른 동화가 아닙니다.
- 한 Level 안에서 하나의 긴 동화를 만들고, 각 강은 그 동화를 조금씩 나누어 보여주는 `Part`입니다.
- `plans/curriculum_plan.example.json`의 `episode_beats`는 각 Part에서 진행할 사건/감정 흐름입니다.

```txt
Level 1 long story
  -> Part 1 / 1강
    -> Llama 3.1로 첫 구간 생성
    -> Qwen judge로 품질 평가
    -> 80점 이상 + 개별 기준 통과 시 KEEP
    -> 이미지 프롬프트 생성
    -> 묘사 퀴즈 생성
    -> 롤플레잉 미션 생성
    -> DB/JSON 저장
  -> Part 2 / 2강
    -> Part 1 내용을 continuity context로 넘겨 이어서 생성
  -> Part 3 / 3강
    -> 앞 구간을 이어받아 같은 동화를 계속 생성
```

동화 judge는 20개 기준을 각각 1-5점으로 평가합니다. 총점은 100점이며, 아래 조건을 모두 만족해야 통과합니다. 어린이 대상 콘텐츠이므로 안전성과 학습 적합성 기준은 엄격하게 유지합니다.

- 총점 `80점 이상`
- 모든 개별 기준 `3점 이상`
- 중요 기준 `4점 이상`
  - emotional safety
  - readability
  - visual scene clarity
  - roleplay compatibility
- emotional safety, readability, visual scene clarity가 `2점 이하`면 자동 탈락

문장 수는 학습 설계상 목표값을 두되, 생성 안정성을 위해 `목표 ±2문장`까지 허용합니다.

- Level 1: 목표 5문장, 허용 3-7문장
- Level 2: 목표 7문장, 허용 5-9문장
- Level 3: 목표 10문장, 허용 8-12문장

## 프로젝트 구조

```txt
ai/
  prompts.py             # 동화 생성, judge, 묘사 퀴즈, 롤플레잉 프롬프트
  story_generator.py     # 콘텐츠 생성 메인 파이프라인
  image_generator.py     # ComfyUI 이미지 생성 연동
  llm_client.py          # Ollama / Claude 호출 어댑터
  pronunciation.py       # STT + 발음 평가
  description.py         # 장면 묘사 평가
  roleplay.py            # 롤플레잉 처리

backend/
  main.py                # FastAPI 엔드포인트
  database.py            # DB 연결
  db_models.py           # SQLAlchemy 모델
  repositories.py        # 저장/조회 로직
  serializers.py         # Lesson 응답 변환

shared/
  settings.py            # 환경변수, 모델, 레벨 설정
  models.py              # 공통 dataclass 모델

scripts/
  generate_lessons.py    # 테마 목록 기반 로컬 실행 스크립트

docs/
  API_REQUESTS.md        # API 요청 예시
  GITHUB_STRUCTURE.md    # 저장소 구조 설명

plans/
  content_plan.example.json    # 단일 레벨 동화 생성 요청 목록 예시
  curriculum_plan.example.json # 전체 커리큘럼 생성 요청 목록 예시
docker-compose.yml        # PostgreSQL 실행용
requirements.txt          # Python 패키지 목록
```

## 설치해야 하는 것

### 1. Python 패키지

```bash
cd /Users/daeun/Desktop/lion
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

주요 패키지:

- `fastapi`, `uvicorn`: 백엔드 API 서버
- `sqlalchemy`, `asyncpg`: PostgreSQL 연동
- `requests`: Ollama / ComfyUI HTTP 호출
- `python-dotenv`: `.env` 환경변수 로드
- `faster-whisper`: 발음 평가용 STT
- `edge-tts`: 로컬/대체 TTS
- `anthropic`: Claude API를 사용할 경우 필요

### 2. Ollama 모델

동화 생성은 로컬 Llama 계열 모델을 사용합니다.

```bash
ollama pull llama3.1:8b
```

Judge는 기본적으로 `qwen2.5:7b` 하나만 사용합니다. JSON 출력 안정성이 좋아서 현재 파이프라인 judge에 가장 적합합니다.

```bash
ollama pull qwen2.5:7b
```

`.env` 예시:

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
STORY_MODEL=llama3.1:8b
STORY_JUDGE_MODELS=qwen2.5:7b
```

### 3. PostgreSQL

DB 저장을 사용하려면 Docker로 PostgreSQL을 실행합니다.

```bash
docker compose up -d
```

`.env`:

```bash
DATABASE_URL=postgresql+asyncpg://lion:lion_password@localhost:5432/lion
```

`DATABASE_URL`이 비어 있으면 DB 저장 없이 메모리/JSON 결과 위주로 동작합니다.

### 4. ComfyUI + SDXL

이미지 생성까지 하려면 ComfyUI가 필요합니다. 이 저장소에서는 `tools/ComfyUI`를 로컬 도구로 사용합니다.

SDXL checkpoint 위치:

```txt
tools/ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors
```

ComfyUI 실행:

```bash
cd /Users/daeun/Desktop/lion/tools/ComfyUI
source .venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188
```

`.env`:

```bash
IMAGE_PROVIDER=comfyui
IMAGE_OUTPUT_DIR=images
COMFYUI_BASE_URL=http://127.0.0.1:8188
COMFYUI_CHECKPOINT=sd_xl_base_1.0.safetensors
COMFYUI_WIDTH=832
COMFYUI_HEIGHT=1216
COMFYUI_STEPS=28
COMFYUI_CFG=7.0
```

## 실행 방법

### 방법 1. 테마 목록 파일로 동화 생성

긴 `curl` 없이 `plans/content_plan.example.json`을 수정한 뒤 실행합니다.

```bash
source .venv/bin/activate
python -m scripts.generate_lessons --plan plans/content_plan.example.json
```

또는 `story_generator.py`로 직접 실행할 수 있습니다.

```bash
python -m ai.story_generator --plan plans/content_plan.example.json
```

결과는 기본적으로 아래 파일에 저장됩니다.

```txt
outputs/generated_lessons.json
outputs/generated_lessons_stories.md
```

실제 개인 설정 파일을 따로 만들고 싶으면 `plans/content_plan.json`을 사용하면 됩니다. 이 파일은 `.gitignore`에 포함되어 저장소에 올라가지 않습니다.

```bash
cp plans/content_plan.example.json plans/content_plan.json
python -m scripts.generate_lessons --plan plans/content_plan.json
```

커리큘럼 전체를 한 번에 생성하려면 `plans/curriculum_plan.example.json`을 사용합니다.

```bash
python -m scripts.generate_lessons --plan plans/curriculum_plan.example.json
```

기본 커리큘럼 구성:

- Level 1: 5문장 x 10강 = 50문장
- Level 2: 7문장 x 7강 = 49문장
- Level 3: 10문장 x 5강 = 50문장

결과는 기본적으로 아래 파일에 저장됩니다.

```txt
outputs/curriculum_lessons.json
outputs/curriculum_lessons_stories.md
```

`*.json` 파일은 API/DB 저장용 전체 데이터이고, `*_stories.md` 파일은 사람이 읽기 쉬운 동화 전문 모음입니다.

## 생성 결과 저장 방식

동화 생성 파이프라인은 통과한 콘텐츠와 탈락 기록을 모두 파일로 남깁니다. 기본 저장 위치는 `outputs/`입니다.

### 파일명으로 구분하는 법

| 파일/경로 | 언제 생김 | 의미 |
| --- | --- | --- |
| `outputs/generated_lessons.json` | `plans/content_plan.example.json` 실행 시 | 단일 레벨 테스트/소량 생성 결과 전체 JSON |
| `outputs/generated_lessons_stories.md` | 위 실행 후 자동 생성 | 단일 레벨 통과 동화 전문만 모은 읽기용 파일 |
| `outputs/curriculum_lessons.json` | `plans/curriculum_plan.example.json` 실행 시 | Level 1/2/3 전체 커리큘럼 생성 결과 JSON |
| `outputs/curriculum_lessons_stories.md` | 위 실행 후 자동 생성 | 전체 커리큘럼 통과 동화 전문만 모은 읽기용 파일 |
| `outputs/test_generated_lessons.json` | `--output outputs/test_generated_lessons.json`처럼 직접 지정했을 때 | 개발 중 테스트 실행 결과 |
| `images/{book_id}_ep{episode}_p{page}.png` | `generate_images=true`일 때 | 특정 동화의 특정 페이지 이미지 |
| `audio/{lesson_id}_p{page}.mp3` | `generate_tts=true`일 때 | 특정 동화의 특정 페이지 음성 |

`generated_*`는 보통 짧은 테스트/단일 레벨 생성 결과이고, `curriculum_*`는 전체 커리큘럼 결과입니다.

`*_stories.md`가 붙은 파일이 **동화 전문**입니다. 아이에게 읽힐 문장만 확인하고 싶으면 JSON이 아니라 이 파일을 보면 됩니다.

기존에 생성된 JSON 파일은 `*_stories.md` 기능을 추가하기 전에 만들어졌을 수 있습니다. 그런 경우 같은 명령을 다시 실행하면 Markdown 전문 파일이 함께 생성됩니다.

### Lesson ID 읽는 법

lesson ID는 아래 형식입니다.

```txt
{book_id}_ep{episode}_lv{level}
```

예시:

```txt
lion_ep1_lv1
```

의미:

- `lion`: book ID
- `ep1`: 1강
- `lv1`: Level 1

이미지 파일명은 lesson의 page 번호까지 포함합니다.

```txt
images/lion_ep1_p3.png
```

의미:

- `lion`: book ID
- `ep1`: 1강
- `p3`: 3페이지 이미지

### 1. 전체 결과 JSON

```txt
outputs/generated_lessons.json
outputs/curriculum_lessons.json
```

JSON 파일에는 API와 DB 저장에 필요한 전체 데이터가 들어갑니다.

포함 내용:

- `accepted_count`: judge를 통과한 lesson 수
- `rejected_count`: reject된 생성 시도 수
- `accepted`: 통과한 lesson 목록
- `rejected`: 탈락한 생성 시도와 탈락 사유
- 각 lesson의 `pages`
- 각 page의 `text`, `image_prompt`, `image_path`, `audio_path`
- 묘사 퀴즈 `description_scenes`
- 롤플레잉 미션 `roleplay_scenarios`
- judge 점수와 평가 결과 `evaluation`

`plans/content_plan.example.json`을 실행하면 기본적으로 `outputs/generated_lessons.json`에 저장됩니다.

`plans/curriculum_plan.example.json`을 실행하면 기본적으로 `outputs/curriculum_lessons.json`에 저장됩니다.

### 2. 동화 전문 Markdown

```txt
outputs/generated_lessons_stories.md
outputs/curriculum_lessons_stories.md
```

Markdown 파일은 사람이 바로 읽기 위한 동화 전문입니다. JSON에서 통과한 구간만 뽑아서 Level, Lesson/Part, Episode beat, Score, 문장 순서대로 정리합니다.

예시 구조:

```txt
## Level 1

### Level 1 - Lesson 1
- Lesson ID: lion_ep1_lv1
- Episode beat: friendship
- Score: 87

1. ...
2. ...
3. ...
```

`--no-story-text` 옵션을 붙이면 Markdown 전문 파일은 만들지 않습니다.

```bash
python -m scripts.generate_lessons --plan plans/curriculum_plan.example.json --no-story-text
```

### 3. 이미지 파일

이미지 경로는 항상 lesson 데이터에 먼저 저장됩니다.

```txt
images/{book_id}_ep{episode}_p{page_number}.png
```

예시:

```txt
images/lion_ep1_p1.png
images/lion_ep1_p2.png
```

`generate_images=false`이면 실제 PNG 파일은 만들지 않고, `image_path`와 `image_prompt`만 JSON에 저장합니다.

`generate_images=true`이고 `IMAGE_PROVIDER=comfyui`이면 ComfyUI로 실제 이미지를 생성해서 `images/`에 저장합니다.

### 4. 음성 파일

`generate_tts=true`이면 각 페이지 문장마다 TTS 음성을 생성합니다.

```txt
audio/{lesson_id}_p{page_number}.mp3
```

예시:

```txt
audio/lion_ep1_lv1_p1.mp3
```

생성된 경로는 각 page의 `audio_path`에 저장됩니다.

### 5. PostgreSQL 저장

`.env`에 `DATABASE_URL`이 있고 plan에서 `save_to_database=true`이면, judge를 통과한 lesson만 PostgreSQL에도 저장합니다.

DB 저장 대상:

- lesson 전체 JSON 데이터
- roleplay scenario 데이터

reject된 결과는 DB에 저장하지 않고 JSON 결과 파일의 `rejected`에만 남깁니다.

### 방법 2. FastAPI 서버 실행

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

기본 주소:

```txt
http://127.0.0.1:8000
```

포트가 사용 중이면 다른 포트를 지정합니다.

```bash
uvicorn backend.main:app --reload --port 8005
```

### 방법 3. API로 배치 생성

```bash
curl -X POST http://127.0.0.1:8000/api/content/lessons/batch \
  -H "Content-Type: application/json" \
  -d '{
    "book_id": "book1",
    "start_episode": 1,
    "level": 1,
    "age": 7,
    "themes": ["friendship", "courage", "helping others"],
    "protagonist": "a cute yellow lion cub",
    "min_score": 80,
    "generate_images": false,
    "generate_tts": false
  }'
```

## 주요 API

- `POST /api/content/lesson`: 단일 동화 lesson 생성
- `POST /api/content/lessons/batch`: 테마 목록 기반 순차 생성 + judge 필터
- `GET /api/content/lesson/{lesson_id}`: 저장된 lesson 조회
- `POST /api/learning/pronunciation`: 발음 평가
- `POST /api/learning/description/{lesson_id}/scene/{scene_number}`: 장면 묘사 평가
- `GET /api/learning/roleplay/{scenario_id}/start`: 롤플레잉 시작
- `WS /ws/roleplay/{scenario_id}`: 롤플레잉 WebSocket

더 자세한 요청 예시는 [docs/API_REQUESTS.md](docs/API_REQUESTS.md)를 참고하세요.

## 저장소에 포함하지 않는 것

`.gitignore`에서 다음 항목은 제외합니다.

- `.env`, `.env.*`: API 키, DB URL 등 비밀값
- `.venv/`, `venv/`: 로컬 가상환경
- `tools/`: ComfyUI 같은 외부 도구 및 대용량 모델
- `images/`, `audio/`, `outputs/`: 생성 결과물
- `models/`, `checkpoints/`: 대용량 모델 파일
- `postgres_data/`, `*.sqlite`, `*.db`: 로컬 DB 데이터
- `__pycache__/`, `.pytest_cache/`: Python 캐시
- `.DS_Store`, `.idea/`, `.vscode/`: 개인 OS/IDE 파일

## 현재 파이프라인 상태

- 동화 생성 프롬프트 적용 완료
- Judge 프롬프트 적용 완료
- 80점 이상 + 개별 기준 필터 적용 완료
- 3점 미만 자동 탈락 조건 적용 완료
- 묘사 퀴즈 프롬프트 적용 완료
- 롤플레잉 미션 프롬프트 적용 완료
- Llama 3.1 생성 / Qwen judge 구조 적용 완료
- ComfyUI + SDXL 이미지 생성 연동 구조 적용 완료
- PostgreSQL 저장 구조 적용 완료

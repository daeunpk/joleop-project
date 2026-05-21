# API Request Examples

서버 기본 실행:

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload
```

서버 주소:

```txt
http://127.0.0.1:8000
```

## 1. 단일 동화 생성

테마 하나로 lesson 하나를 생성합니다.

```bash
curl -X POST http://127.0.0.1:8000/api/content/lesson \
  -H "Content-Type: application/json" \
  -d '{
    "book_id": "book1",
    "episode": 1,
    "level": 1,
    "age": 7,
    "theme": "friendship",
    "protagonist": "a cute yellow lion cub",
    "generate_images": false,
    "generate_tts": false
  }'
```

## 2. 테마 목록 기반 생성 + Judge 필터

원하는 운영 파이프라인입니다.

```txt
theme 1 -> Llama 생성 -> Qwen judge -> PASS면 저장/출력
theme 2 -> Llama 생성 -> Qwen judge -> FAIL이면 rejected
theme 3 -> 반복
```

긴 요청문을 매번 치지 않으려면 `plans/content_plan.example.json`을 수정하고 아래처럼 실행하면 됩니다.

```bash
source .venv/bin/activate
python -m scripts.generate_lessons --plan plans/content_plan.example.json
```

`ai/story_generator.py` 쪽으로 실행하고 싶으면 아래 명령도 같습니다.

```bash
source .venv/bin/activate
python -m ai.story_generator --plan plans/content_plan.example.json
```

API로 호출해야 할 때만 아래 `curl`을 사용합니다.

```bash
curl -X POST http://127.0.0.1:8000/api/content/lessons/batch \
  -H "Content-Type: application/json" \
  -d '{
    "book_id": "book1",
    "start_episode": 1,
    "level": 1,
    "age": 7,
    "themes": [
      "friendship",
      "courage",
      "helping others",
      "curiosity",
      "teamwork"
    ],
    "protagonist": "a cute yellow lion cub",
    "min_score": 80,
    "generate_images": false,
    "generate_tts": false
  }'
```

## 3. 이미지까지 생성

ComfyUI가 켜져 있어야 합니다.

```bash
cd /Users/daeun/Desktop/lion/tools/ComfyUI
source .venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188
```

요청에서 `generate_images`를 `true`로 바꿉니다.

```json
"generate_images": true
```

## 4. 저장된 lesson 조회

```bash
curl http://127.0.0.1:8000/api/content/lesson/book1_ep1_lv1
```

## 5. 발음 평가

```bash
curl -X POST http://127.0.0.1:8000/api/learning/pronunciation \
  -F "audio=@/path/to/audio.wav" \
  -F "target_sentence=A cute lion waves." \
  -F "level=1"
```

## 6. 묘사 퀴즈 평가

```bash
curl -X POST http://127.0.0.1:8000/api/learning/description/book1_ep1_lv1/scene/1 \
  -F "audio=@/path/to/audio.wav"
```

## 7. 롤플레잉 시작

```bash
curl http://127.0.0.1:8000/api/learning/roleplay/rp_1_1/start
```

응답의 `websocket_url`로 오디오 바이트를 전송합니다.

"""
=== 현재 서비스 구조 ===

[AI 텍스트 파이프라인]
- 기본 권장: Ollama + llama3.1:8b
- 역할: 동화 품질 평가, 이미지 프롬프트 생성, 묘사 채점, 롤플레잉 대화/판단
- 대체: Anthropic Claude API (LLM_PROVIDER=anthropic)
- 동화 초안 생성은 STORY_MODEL로 지정한 로컬 Ollama 모델을 사용

[이미지 생성]
- 기본 권장: 로컬 ComfyUI + SDXL Base 1.0
- 역할: 동화 한 문장당 이미지 1장 생성
- checkpoint 위치:
  tools/ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors
- 그림체 통일: IMAGE_STYLE_GUIDE + 동일 checkpoint + 추후 IMAGE_REFERENCE/IP-Adapter 연결

[STT - 따라말하기]
- faster-whisper 로컬 실행
- 기본 모델: openai/whisper-large-v3

[TTS]
- ElevenLabs API 사용 가능
- 테스트/대안: edge-tts

[Backend]
- FastAPI: backend/main.py
- PostgreSQL: DATABASE_URL 설정 시 사용
- DATABASE_URL이 없으면 메모리 저장소 사용
"""

from dataclasses import dataclass, field
from typing import Optional
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── 외부 API Key / DB URL ────────────────────────────────────
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
HF_TOKEN            = os.getenv("HF_TOKEN", "")          # STT HuggingFace fallback용
DATABASE_URL        = os.getenv("DATABASE_URL", "")      # 예: postgresql+asyncpg://user:pass@localhost:5432/lion

# ─── LLM Provider ────────────────────────────────────────────
# - anthropic: Claude API 사용
# - ollama: 로컬 Ollama 사용 (권장: llama3.1:8b)
LLM_PROVIDER        = os.getenv("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL     = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL        = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
STORY_MODEL         = os.getenv("STORY_MODEL", OLLAMA_MODEL)
STORY_JUDGE_MODELS  = [
    m.strip()
    for m in os.getenv("STORY_JUDGE_MODELS", "qwen2.5:7b").split(",")
    if m.strip()
]

# ─── Image Generation Provider ───────────────────────────────
# - none: 이미지 파일은 만들지 않고 경로/프롬프트만 저장
# - comfyui: 로컬 ComfyUI API 사용
IMAGE_PROVIDER      = os.getenv("IMAGE_PROVIDER", "comfyui")
IMAGE_OUTPUT_DIR    = os.getenv("IMAGE_OUTPUT_DIR", "images")
IMAGE_REFERENCE     = os.getenv("IMAGE_REFERENCE", "")  # 통일 그림체 기준 이미지 경로
COMFYUI_BASE_URL    = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
COMFYUI_CHECKPOINT  = os.getenv("COMFYUI_CHECKPOINT", "sd_xl_base_1.0.safetensors")
COMFYUI_WIDTH       = int(os.getenv("COMFYUI_WIDTH", "832"))
COMFYUI_HEIGHT      = int(os.getenv("COMFYUI_HEIGHT", "1216"))
COMFYUI_STEPS       = int(os.getenv("COMFYUI_STEPS", "28"))
COMFYUI_CFG         = float(os.getenv("COMFYUI_CFG", "7.0"))
COMFYUI_SAMPLER     = os.getenv("COMFYUI_SAMPLER", "euler")
COMFYUI_SCHEDULER   = os.getenv("COMFYUI_SCHEDULER", "normal")

# ─── 모델 설정 ────────────────────────────────────────────────
@dataclass
class ModelConfig:
    # Claude API를 사용할 때의 품질 평가 / 채점 / 롤플레잉 모델
    judge_model: str = "claude-haiku-4-5-20251001"

    # Ollama를 사용할 때의 로컬 텍스트 모델
    local_judge_model: str = OLLAMA_MODEL
    story_model: str = STORY_MODEL
    story_judge_models: list = field(default_factory=lambda: STORY_JUDGE_MODELS)

    # STT 모델명 (faster-whisper 로컬 또는 HF fallback에서 사용)
    whisper_model: str = "openai/whisper-large-v3"

    # TTS voice id (ElevenLabs)
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"  # Rachel (영어 동화)


# ─── 난이도별 학습 설정 ───────────────────────────────────────
@dataclass
class LevelConfig:
    level: int
    pages: int              # 페이지(=문장) 수
    repeat_sentences: int   # 따라말하기 문장 수
    pronunciation_cutoff: int  # 발음 정확도 커트라인 (%)
    description_scenes: int    # 묘사 장면 수
    roleplay_count: int        # 롤플레잉 횟수

LEVEL_CONFIGS = {
    1: LevelConfig(level=1, pages=5,  repeat_sentences=5,  pronunciation_cutoff=50, description_scenes=2, roleplay_count=1),
    2: LevelConfig(level=2, pages=7,  repeat_sentences=7,  pronunciation_cutoff=70, description_scenes=3, roleplay_count=1),
    3: LevelConfig(level=3, pages=10, repeat_sentences=10, pronunciation_cutoff=80, description_scenes=3, roleplay_count=2),
}

MODELS = ModelConfig()


IMAGE_STYLE_GUIDE = """
Consistent mobile children's storybook app illustration style:
- bright 2D digital illustration, cute educational mobile game look
- rounded friendly characters with big expressive eyes and soft simple shapes
- clean vector-like edges, smooth cel shading, warm highlights
- saturated cheerful colors, forest/fairy-tale palette, high readability
- simple background depth with layered hills, trees, sky, or room elements
- portrait mobile composition, clear center subject, no clutter
- same character design, same proportions, same line weight, same lighting
- no photorealism, no watercolor texture, no sketch lines, no 3D render
"""

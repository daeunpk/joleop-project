"""
Story image generation boundary.

The story pipeline always creates stable image paths and prompts. Actual image
file generation is intentionally provider-based so the backend can run before
the final image model is chosen.
"""

import os
import random
import time

import requests

from shared.settings import (
    COMFYUI_BASE_URL,
    COMFYUI_CFG,
    COMFYUI_CHECKPOINT,
    COMFYUI_HEIGHT,
    COMFYUI_SAMPLER,
    COMFYUI_SCHEDULER,
    COMFYUI_STEPS,
    COMFYUI_WIDTH,
    IMAGE_OUTPUT_DIR,
    IMAGE_PROVIDER,
    IMAGE_REFERENCE,
    IMAGE_STYLE_GUIDE,
)
from shared.models import StoryPage


def build_image_path(book_id: str, episode: int, page_number: int) -> str:
    return f"{IMAGE_OUTPUT_DIR}/{book_id}_ep{episode}_p{page_number}.png"


def attach_planned_image_paths(
    pages: list[StoryPage],
    *,
    book_id: str,
    episode: int,
) -> list[str]:
    paths = []
    for page in pages:
        page.image_path = build_image_path(book_id, episode, page.page_number)
        paths.append(page.image_path)
    return paths


def generate_story_images(
    pages: list[StoryPage],
    *,
    book_id: str,
    episode: int,
) -> list[str]:
    """
    Generate one image per story page.

    IMAGE_PROVIDER=none keeps the paths/prompts only. This is useful while the
    final image backend is undecided. Once a provider is chosen, implement it
    here without touching the rest of the lesson pipeline.
    """
    paths = attach_planned_image_paths(pages, book_id=book_id, episode=episode)

    if IMAGE_PROVIDER == "none":
        return paths

    if IMAGE_PROVIDER == "comfyui":
        ensure_image_output_dir()
        for page in pages:
            _generate_page_with_comfyui(page)
        return paths

    raise ValueError(f"Unsupported IMAGE_PROVIDER: {IMAGE_PROVIDER}")


def build_final_image_prompt(page: StoryPage) -> str:
    reference_note = (
        f"Use this reference image for style consistency: {IMAGE_REFERENCE}"
        if IMAGE_REFERENCE
        else "Use the shared style guide exactly for consistency."
    )
    return f"""{page.image_prompt}

{IMAGE_STYLE_GUIDE}

Consistency requirement:
- The full lesson must look like one illustrated book by the same artist.
- Keep the protagonist identical across all pages.
- Same line weight, color palette, lighting, shading, and mobile portrait framing.
- {reference_note}

Output:
- One portrait mobile story illustration.
- No UI chrome, buttons, captions, watermarks, or text inside the image."""


def ensure_image_output_dir() -> None:
    os.makedirs(IMAGE_OUTPUT_DIR, exist_ok=True)


def _generate_page_with_comfyui(page: StoryPage) -> None:
    if not page.image_path:
        raise ValueError("StoryPage.image_path must be set before image generation.")

    prompt = build_final_image_prompt(page)
    workflow = _build_sdxl_workflow(prompt)
    prompt_id = _queue_comfyui_prompt(workflow)
    output = _wait_for_comfyui_image(prompt_id)
    image_bytes = _download_comfyui_image(output)

    with open(page.image_path, "wb") as f:
        f.write(image_bytes)


def _queue_comfyui_prompt(workflow: dict) -> str:
    resp = requests.post(
        f"{COMFYUI_BASE_URL.rstrip('/')}/prompt",
        json={"prompt": workflow},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["prompt_id"]


def _wait_for_comfyui_image(prompt_id: str, timeout_seconds: int = 300) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = requests.get(
            f"{COMFYUI_BASE_URL.rstrip('/')}/history/{prompt_id}",
            timeout=30,
        )
        resp.raise_for_status()
        history = resp.json()
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            for node_output in outputs.values():
                images = node_output.get("images", [])
                if images:
                    return images[0]
        time.sleep(1)

    raise TimeoutError(f"ComfyUI image generation timed out: {prompt_id}")


def _download_comfyui_image(image_info: dict) -> bytes:
    resp = requests.get(
        f"{COMFYUI_BASE_URL.rstrip('/')}/view",
        params={
            "filename": image_info["filename"],
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def _build_sdxl_workflow(prompt: str) -> dict:
    negative_prompt = (
        "photorealistic, realistic, 3d render, watercolor, sketch, messy lines, "
        "different character design, inconsistent style, text, caption, watermark, "
        "logo, UI, button, blurry, low quality, extra limbs, scary, violent"
    )
    seed = random.randint(1, 2**31 - 1)
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": COMFYUI_CHECKPOINT},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": COMFYUI_WIDTH,
                "height": COMFYUI_HEIGHT,
                "batch_size": 1,
            },
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": COMFYUI_STEPS,
                "cfg": COMFYUI_CFG,
                "sampler_name": COMFYUI_SAMPLER,
                "scheduler": COMFYUI_SCHEDULER,
                "denoise": 1,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "lion_story", "images": ["6", 0]},
        },
    }

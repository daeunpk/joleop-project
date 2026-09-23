from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import AudioValidationException


ALLOWED_AUDIO_MIME_TYPES = {
    "audio/aac",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/x-m4a",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
}


class AudioValidationService:
    async def read_validated_audio(self, audio: UploadFile) -> bytes:
        content_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in ALLOWED_AUDIO_MIME_TYPES:
            raise AudioValidationException(
                code="INVALID_AUDIO_MIME_TYPE",
                detail="지원하지 않는 오디오 형식입니다.",
            )

        data = await audio.read()
        if not data:
            raise AudioValidationException(
                code="EMPTY_AUDIO",
                detail="빈 오디오 파일은 업로드할 수 없습니다.",
            )
        if len(data) > settings.MAX_AUDIO_UPLOAD_BYTES:
            raise AudioValidationException(
                code="AUDIO_TOO_LARGE",
                detail="오디오 파일 크기가 너무 큽니다.",
            )
        return data

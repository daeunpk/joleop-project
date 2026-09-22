FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/backend
ENV OLLAMA_EMBEDDED=false

ARG INSTALL_OLLAMA=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl zstd \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$INSTALL_OLLAMA" = "true" ]; then \
      curl -fsSL https://ollama.com/install.sh | sh; \
    fi

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY ai /app/ai
COPY shared /app/shared
RUN mkdir -p /app/outputs/run_20260903_114105_819434
COPY outputs/run_20260903_114105_819434/qwen_judged_lessons_accepted_text_roleplays.json /app/outputs/run_20260903_114105_819434/
COPY outputs/run_20260903_114105_819434/qwen_judged_lessons_level2_accepted_text_roleplays.json /app/outputs/run_20260903_114105_819434/
COPY outputs/run_20260903_114105_819434/qwen_judged_lessons_level3_accepted_text_roleplays.json /app/outputs/run_20260903_114105_819434/
RUN chmod +x /app/backend/start.sh

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=900s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/app/backend/start.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

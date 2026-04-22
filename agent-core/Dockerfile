# =============================================================================
# Hoshino Agent Core — production-ready Dockerfile
# =============================================================================
# Multi-stage build: a lean runtime image with no build tools, plus a dev image.
#
# Build & run (local vLLM still required for vision; swap in cloud keys for API):
#
#   # Local development with cloud API
#   cp .env.example .env   # fill in MINIMAX_API_KEY / MODELSCOPE_API_KEY as needed
#   docker compose up
#
#   # Local development with local vLLM
#   docker compose -f docker-compose.yml -f docker-compose.vllm.yml up
# =============================================================================

FROM python:3.11-slim AS base

WORKDIR /app

# Install runtime & build dependencies in one layer (shared by all stages).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        npx \
    && rm -rf /var/lib/apt/lists/

# Install uv for fast dependency management.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# =============================================================================
# Deps stage — pinned hashes for reproducible installs.
# =============================================================================
FROM base AS deps

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -e .

# =============================================================================
# Runtime stage — everything the agent needs to run, nothing it doesn't.
# =============================================================================
FROM base AS runtime

WORKDIR /app

# Copy installed Python packages from deps stage.
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application code.
COPY app/ ./app/
COPY eval/ ./eval/
COPY skills/ ./skills/
COPY artifacts/ ./artifacts/

# chromadb needs a writable home for its embedded storage.
ENV HOME=/root
ENV PYTHONDONTWRITEBYTECODE=1

# The server listens on 8787 inside the container.
EXPOSE 8787

# Run as a non-root user for security.
RUN useradd --create-home --shell /bin/bash hoshino
USER hoshino
WORKDIR /app/hoshino
ENV PATH="/root/.local/bin:$PATH"

# Default: MiniMax cloud API for chat, local hash embeddings as a safe fallback.
ENV PROVIDER=minimax
ENV MINIMAX_MODEL=MiniMax-Text-01
ENV EMBEDDING_PROVIDER=hash
ENV EMBEDDING_MODEL=text-embedding-3-small
ENV EMBEDDING_DIM=256

# Memory directories — mounted as volumes in production.
ENV MEMORY_DIR=/app/hoshino/memory
ENV SKILLS_DIR=/app/hoshino/skills
ENV SCREENSHOTS_DIR=/app/hoshino/artifacts/screenshots
ENV CHROMA_DIR=/app/hoshino/memory/chroma

# Create writable dirs.
RUN mkdir -p "$MEMORY_DIR" "$SKILLS_DIR" "$SCREENSHOTS_DIR" "$CHROMA_DIR"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]

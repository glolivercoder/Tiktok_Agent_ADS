FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Skills ficam fora do wheel (dados editáveis). A env aponta para elas.
COPY data/skills ./data/skills

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app

USER appuser

ENV ECHOTIK_AGENT_DATA_DIR=/data \
    ECHOTIK_SKILLS_PATH=/app/data/skills/strategic_skills.json

EXPOSE 5077

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5077/health || exit 1

CMD ["uvicorn", "echotik_agent.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "5077"]

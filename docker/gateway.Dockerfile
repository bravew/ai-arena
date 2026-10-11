FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY docs/DEV_PLAN.md ./docs/DEV_PLAN.md
COPY src ./src
RUN python -m pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
EXPOSE 8765
ENTRYPOINT ["python", "-m", "arena.gateway.run"]

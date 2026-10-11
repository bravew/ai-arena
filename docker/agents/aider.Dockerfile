FROM python:3.12.11-slim-bookworm

ARG AIDER_VERSION=0.86.2
RUN python -m pip install --no-cache-dir "aider-chat==${AIDER_VERSION}" \
    && useradd --create-home --home-dir /home/agent --shell /bin/sh agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

ENV HOME=/home/agent
WORKDIR /workspace
USER agent

CMD ["aider", "--version"]

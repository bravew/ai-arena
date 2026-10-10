FROM node:20-bookworm-slim
ARG CODEX_VERSION=0.162.1
RUN npm install --global "@openai/codex@${CODEX_VERSION}" \
    && codex --version | grep -F "${CODEX_VERSION}" \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /home/agent/.codex /workspace \
    && chown -R agent:agent /home/agent /workspace
ENV HOME=/home/agent CODEX_HOME=/home/agent/.codex ARENA_GATEWAY_TOKEN=
USER agent
WORKDIR /workspace
ENTRYPOINT ["codex"]

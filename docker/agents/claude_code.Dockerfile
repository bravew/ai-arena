FROM node:20-bookworm-slim

ARG CLAUDE_CODE_VERSION=2.4.1
RUN npm install --global "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && useradd --create-home --shell /bin/bash agent \
    && mkdir -p /home/agent/.claude \
    && chown -R agent:agent /home/agent

ENV HOME=/home/agent
USER agent
WORKDIR /workspace
ENTRYPOINT ["claude"]

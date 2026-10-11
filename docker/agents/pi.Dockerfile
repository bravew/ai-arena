FROM node:20-bookworm-slim

ARG PI_VERSION=0.99.2
RUN npm install --global "@mariozechner/pi-coding-agent@${PI_VERSION}" \
    && pi --version \
    && mkdir -p /home/agent/.pi/agent \
    && chown -R 1000:1000 /home/agent

ENV HOME=/home/agent \
    PI_CODING_AGENT_DIR=/home/agent/.pi/agent
WORKDIR /workspace
USER 1000:1000

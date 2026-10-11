# Image for execution scorers that run `python -m pytest`. Built by `docker build` as
# arena-scorer-python:1 and run with no network, so pytest is baked in.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.3.4

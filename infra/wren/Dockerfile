FROM python:3.12-slim

ARG WREN_VERSION=0.13.2

RUN python -m pip install --no-cache-dir \
    "wrenai[mcp]==${WREN_VERSION}" \
    "mcp>=1.19,<2"

WORKDIR /project

COPY infra/wren/serve_no_connect.py /opt/wren/serve_no_connect.py

EXPOSE 8080

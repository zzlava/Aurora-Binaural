FROM node:22-bullseye-slim AS node
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential pkg-config ffmpeg git && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/ /usr/local/
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH=/root/.local/bin:/root/.cargo/bin:$PATH
WORKDIR /workspace
CMD ["bash"]

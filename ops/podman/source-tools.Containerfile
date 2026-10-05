# 仅准备公开词库的临时工具镜像，不是产品运行镜像。
FROM docker.io/library/python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git 7zip && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/bin/sh"]

FROM python:3.12-slim

# 国内 pip 源可按需覆盖：docker build --build-arg PIP_INDEX_URL=...
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    TZ=Asia/Shanghai

# 采样时间戳用本地时间，容器内必须有 tzdata 否则 TZ 不生效
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY static/ static/

RUN useradd -m appuser
USER appuser

# 容器内固定监听 8000，宿主机端口由 compose 映射
EXPOSE 8821

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8821/api/vendors', timeout=4)"

CMD ["python", "-m", "app.main"]

FROM python:3.10-slim-bullseye

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /Bangumi_Auto_Rename

COPY requirements_docker.txt ./

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git \
        python-is-python3 \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libxkbcommon0 libasound2 libpango-1.0-0 libcairo2 && \
    python -m pip install --upgrade pip && \
    pip install -r requirements_docker.txt && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY . /Bangumi_Auto_Rename

EXPOSE 5999

CMD ["sh", "-c", "pwd && ls /Bangumi_Auto_Rename && python3 -m src.start"]

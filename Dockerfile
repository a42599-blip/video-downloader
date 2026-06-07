FROM python:3.12-slim

# 安裝系統依賴（ffmpeg + Playwright Chromium + Deno）
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# 安裝 Deno（JS runtime，yt-dlp 需要它解 YouTube 機器人驗證）
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app

# 安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 安裝 Playwright Chromium
RUN playwright install chromium --with-deps

# 複製應用程式
COPY server.py .
COPY index.html .
COPY crawlers/ ./crawlers/
RUN mkdir -p 下載影片

EXPOSE 7790

CMD ["python", "server.py"]

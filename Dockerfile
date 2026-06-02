FROM python:3.12-slim

# 安裝系統依賴（ffmpeg + Playwright Chromium）
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安裝 Python 依賴（yt-dlp 不鎖版本，保持最新）
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 不安裝 Playwright Chromium（雲端 512MB 跑不動，節省空間和建置時間）
# 如需 Playwright 支援，請升級到 Developer 方案（2GB RAM）後取消註解：
# RUN playwright install chromium --with-deps

# 複製應用程式
COPY server.py .
COPY index.html .
RUN mkdir -p 下載影片

EXPOSE 7790

CMD ["python", "server.py"]

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

# 不安裝 Chromium（Railway 512MB 跑不動，節省建置時間）
# 若升級到 2GB 方案，可取消下一行註解啟用 Playwright 支援
# RUN playwright install chromium --with-deps

# 複製應用程式
COPY server.py .
COPY index.html .
RUN mkdir -p 下載影片

EXPOSE 7790

CMD ["python", "server.py"]

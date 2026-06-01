FROM python:3.12-slim

# 安裝系統依賴（ffmpeg + Playwright Chromium 所需）
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安裝 Playwright Chromium
RUN playwright install chromium --with-deps

# 複製應用程式
COPY server.py .
COPY index.html .
RUN mkdir -p 下載影片

EXPOSE 7790

CMD ["python", "server.py"]

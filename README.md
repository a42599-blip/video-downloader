# 🎬 去水印下載工具

> 貼上連結 → 伺服器解析並下載影片 → 一鍵取得無水印影片

🌐 **立即使用：** [v8i8.com](https://v8i8.com)

## 功能特色

- ✅ **抖音** — 無水印影片下載
- ✅ **YouTube** — 360p / 720p / 1080p 多畫質選擇
- ✅ **TikTok** — 影片下載
- ✅ **Bilibili** — 直接 API 解析
- ✅ **小紅書 Xiaohongshu** — 筆記圖片/影片下載
- ✅ **蝦皮短影音 Shopee** — 短影音下載
- ✅ **Lux 工具** — 額外支援愛奇藝、優酷、騰訊視頻、微博
- ✅ **伺服器端下載** — 影片下載到伺服器後，用戶再取回
- ✅ **下載紀錄管理** — 記錄每台裝置的下載檔案
- ✅ **手機/電腦通用** — 響應式設計，任何裝置都能用

## 架構

```
用戶貼入連結
      ↓
伺服器解析影片來源
      ↓
伺服器下載影片（流量經過伺服器）
      ↓
用戶從伺服器取回已下載的影片檔案
      ↓
用戶取得無水印影片 ✅
```

## API 使用

本專案提供開放的 API，可供其他開發者串接。

### 解析影片

```
GET /api/video-info?url=<影片連結>
```

### 下載影片

```
GET /api/dl?url=<CDN網址>&filename=video.mp4
```

### 健康檢查

```
GET /api/health
```

完整 API 文件請參考專案原始碼。

## 本地開發

```bash
pip install -r requirements.txt
playwright install chromium
python server.py
# → http://localhost:7798
```

## 部署

本專案可部署至任何支援 Python 的平台，推薦使用 Railway。

## 專案結構

```
video-downloader/
├── server.py              # FastAPI 主伺服器
├── index.html             # 前端頁面
├── crawlers/              # 爬蟲模組（抖音/蝦皮/B站）
├── requirements.txt       # Python 依賴
├── Dockerfile             # Docker 容器設定
└── README.md              # 本說明文件
```

## 免責聲明

- 本工具僅供學習與研究使用
- 使用者應遵守目標平台的使用條款
- 請勿用於任何侵權或非法用途

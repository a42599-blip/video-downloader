# 🎬 去水印下載工具

> 貼上連結 → 伺服器解析並下載影片 → 一鍵取得無水印影片

🌐 **立即使用：** [v8i8.com](https://v8i8.com)

---

## 功能特色

- ✅ **抖音** — 無水印影片下載
- ✅ **YouTube** — 360p / 720p / 1080p 多畫質選擇
- ✅ **TikTok** — 影片下載
- ✅ **Bilibili** — 直接 API 解析
- ✅ **小紅書 Xiaohongshu** — 圖片/影片下載
- ✅ **蝦皮短影音 Shopee** — 短影音下載
- ✅ **伺服器端下載** — 下載完成後用戶取回
- ✅ **下載紀錄管理** — 每台裝置的下載紀錄
- ✅ **手機/電腦通用** — 響應式設計

## 架構

```
用戶貼入連結 → 伺服器解析 → 伺服器下載 → 用戶取回檔案
```

## API 文件

本專案提供完整的 REST API，可供第三方開發者串接使用。

### 解析影片

```
GET /api/video-info?url=<影片連結>
```

| 參數 | 說明 | 範例 |
|:----|:----|:----|
| `url` | 影片連結（必填） | `https://www.youtube.com/watch?v=xxx` |

**回應範例：**
```json
{
  "title": "影片標題",
  "thumbnail": "https://...縮圖網址",
  "duration": 180,
  "uploader": "作者名稱",
  "platform": "YouTube",
  "has_video": true,
  "cdn_url": "https://...CDN直鏈",
  "formats": [
    {"id": "18", "label": "360p", "height": 360, "cdn_url": "...", "single": true}
  ]
}
```

### 下載影片

```
GET /api/dl?url=<CDN網址>&filename=video.mp4
```

代理下載 CDN 影片，加上強制下載標頭。

### 取得已下載列表

```
GET /api/files?device_id=<裝置ID>
```

### 健康檢查

```
GET /api/health
```

---

## 快速開始（本地開發）

```bash
# 安裝依賴
pip install -r requirements.txt

# 安裝瀏覽器（抖音解析需要）
playwright install chromium

# 啟動伺服器
python server.py
# → http://localhost:7798
```

---

## 自行部署

本專案可部署到任何支援 Python 的雲端平台。

### 方式一：Railway 自動部署

1. Fork 此倉庫
2. 在 Railway 建立新專案 → 選擇 Fork 的倉庫
3. Railway 會自動偵測 Dockerfile 並部署

### 方式二：Docker 手動部署

```bash
docker build -t video-downloader .
docker run -p 7798:7798 video-downloader
```

---

## 技術棧

| 層 | 技術 |
|:----|:------|
| 前端 | HTML + CSS + JavaScript |
| API | Python FastAPI |
| 解析引擎 | yt-dlp + Playwright |
| 爬蟲 | 自訂爬蟲模組 |
| 部署 | Docker / Railway |

---

## 專案結構

```
video-downloader/
├── server.py              # FastAPI 主程式
├── index.html             # 前端頁面
├── crawlers/              # 爬蟲模組（抖音、蝦皮、B站）
├── requirements.txt       # Python 依賴
├── Dockerfile             # Docker 容器設定
└── README.md              # 本說明文件
```

---

## 免責聲明

- 本工具僅供學習與研究使用
- 使用者應遵守目標平台的使用條款
- 請勿用於任何侵權或非法用途

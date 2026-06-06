# 🎬 去水印下載工具 — 迭代開發版

> ⚠️ **此為迭代開發版，非 production**
> 
> **Production 網址：** https://v8i8.com（🔒 保底，不可動）
> **迭代版網址：** https://aa.v8i8.com（🟡 開發測試用）

🌐 **立即使用（迭代版）：** [aa.v8i8.com](https://aa.v8i8.com)
🌐 **正式版：** [v8i8.com](https://v8i8.com)

---

## 功能特色

- ✅ **6 大平台解析** — 抖音、YouTube、TikTok、B站、小紅書、蝦皮
- ✅ **解析紀錄** — 自動保存最近 50 筆（localStorage）
- ✅ **三國語言** — 繁體中文、简体中文、English
- ✅ **並行處理** — 抖音三種方式同時跑
- ✅ **剪貼簿自動貼上**

## 與 production 的關係

```
diedai-ban（迭代版 🟡）    →     video-downloader（production 🟢）
    │                                      │
    ├── 所有修改先在這裡測                   ├── 保底版本，不可直接修改
    ├── 測試完成後合併回 production           ├── 最終穩定版：6847de2
    └── GitHub: a42599-blip/diedai-ban       └── GitHub: a42599-blip/video-downloader
```

## 當前版本

| 項目 | 內容 |
|:----|:------|
| 目前版本 | commit `6847de2`（與 production 相同） |
| 建立日期 | 2026-06-06 |
| 狀態 | ✅ 線上，可用於開發測試 |
| 部署方式 | push master → Railway 自動部署 |

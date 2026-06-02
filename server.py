# -*- coding: utf-8 -*-
import asyncio, re, json, threading, webbrowser, time, httpx, subprocess, os, shutil, sys as _sys_top
try:
    from playwright_stealth import Stealth as _StealthCls
    _stealth_inst = _StealthCls()
    async def _stealth(page):
        await _stealth_inst.apply_stealth_async(page)
except Exception:
    async def _stealth(page): pass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import uvicorn

BASE_DIR          = Path(__file__).parent
DOWNLOAD_DIR      = BASE_DIR / "下載影片"
COOKIES_FILE      = BASE_DIR / "platform_cookies.json"
DOWNLOAD_REGISTRY = DOWNLOAD_DIR / ".download_registry.json"
_reg_lock         = threading.Lock()

def _registry_add(filename: str, device_id: str):
    if not filename or not device_id:
        return
    with _reg_lock:
        try:
            data = json.loads(DOWNLOAD_REGISTRY.read_text(encoding="utf-8")) if DOWNLOAD_REGISTRY.exists() else {}
            data[filename] = device_id
            DOWNLOAD_REGISTRY.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

def _registry_get_files(device_id: str) -> set:
    if not DOWNLOAD_REGISTRY.exists():
        return set()
    try:
        data = json.loads(DOWNLOAD_REGISTRY.read_text(encoding="utf-8"))
        return {k for k, v in data.items() if v == device_id}
    except Exception:
        return set()

def _load_platform_cookies() -> dict:
    # 優先：從檔案讀（管理員在UI設定的）
    try:
        if COOKIES_FILE.exists():
            data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
            if data:
                return data
    except Exception:
        pass
    # 備用：從 Railway 環境變數讀（管理員在 Railway Dashboard 設定的）
    result = {}
    for plat in ["douyin", "kuaishou", "tiktok"]:
        env_val = os.environ.get(f"{plat.upper()}_COOKIES", "")
        if env_val:
            try:
                result[plat] = json.loads(env_val)
            except Exception:
                pass
    return result

def _get_cookies_for_url(url: str) -> list[dict]:
    data = _load_platform_cookies()
    for plat, domain in [
        ("douyin", "douyin.com"), ("kuaishou", "kuaishou.com"), ("tiktok", "tiktok.com"),
    ]:
        if domain in url:
            return data.get(plat, [])
    return []

async def _apply_cookies(ctx, url: str):
    cookies = _get_cookies_for_url(url)
    if cookies:
        try:
            await ctx.add_cookies(cookies)
        except Exception:
            pass

LUX_PATH = Path(os.environ.get("LUX_PATH", r"D:\tools\lux\lux.exe"))
# ffmpeg：Windows 預設路徑 → 環境變數 → 系統 PATH 自動找
_WIN_FFMPEG = r"C:\Users\USER\AppData\Local\Microsoft\WinGet\Links"
if os.path.isdir(_WIN_FFMPEG) and _WIN_FFMPEG not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _WIN_FFMPEG + ";" + os.environ.get("PATH", "")
FFMPEG_BIN = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"

LUX_DOMAINS = ("bilibili.com", "b23.tv", "iqiyi.com", "youku.com",
               "v.qq.com", "weibo.com", "miaopai.com", "pearvideo.com")

def _is_lux_platform(url: str) -> bool:
    return LUX_PATH.exists() and any(d in url for d in LUX_DOMAINS)

# ── Invidious 公開實例（YouTube 替代前端，雲端 IP 不被封）──────
INVIDIOUS_INSTANCES = [
    "https://inv.tux.pizza",
    "https://invidious.io.lol",
    "https://yewtu.be",
    "https://invidious.privacyredirect.com",
    "https://iv.datura.network",
    "https://invidious.nerdvpn.de",
    "https://invidious.slipfox.xyz",
    "https://youtube.owacon.dev",
]

def _extract_youtube_id(url: str) -> str:
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else ""

async def _get_youtube_via_invidious(url: str) -> dict:
    """透過 Invidious API 取得 YouTube 影片直連 URL（繞過雲端 IP 封鎖）"""
    vid_id = _extract_youtube_id(url)
    if not vid_id:
        return {}
    for instance in INVIDIOUS_INSTANCES:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                resp = await client.get(f"{instance}/api/v1/videos/{vid_id}",
                                        params={"fields": "title,author,lengthSeconds,videoThumbnails,formatStreams,adaptiveFormats"})
                if resp.status_code != 200:
                    continue
                data = resp.json()
                title     = data.get("title", "")
                author    = data.get("author", "")
                duration  = data.get("lengthSeconds", 0)
                thumbs    = data.get("videoThumbnails", [])
                thumbnail = next((t["url"] for t in thumbs if t.get("quality") in ("high","medium","default")), "")
                # formatStreams = 已合併音視頻 (mp4)
                fmts = data.get("formatStreams", [])
                if not fmts:
                    continue
                # 按解析度排序，取最高
                def _res(f): return int(re.search(r'(\d+)p',f.get("resolution","0p")).group(1)) if re.search(r'(\d+)p',f.get("resolution","0p")) else 0
                fmts_sorted = sorted(fmts, key=_res, reverse=True)
                best = fmts_sorted[0]
                cdn_url = best.get("url","")
                if not cdn_url:
                    continue
                formats = [{"id": f.get("itag",""), "label": f.get("resolution",""), "height": _res(f)} for f in fmts_sorted]
                print(f"[invidious] {instance} OK vid={vid_id}")
                return {"title": title, "uploader": author, "duration": duration,
                        "thumbnail": thumbnail, "cdn_url": cdn_url, "formats": formats,
                        "platform": "YouTube", "_source": "invidious"}
        except Exception as ex:
            print(f"[invidious] {instance} 失敗: {ex}")
            continue
    return {}

DOWNLOAD_DIR.mkdir(exist_ok=True)

# ── B站直連 API（繞過 yt-dlp 地區限制）────────────────────────────
_BILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_BILI_COOKIE = "buvid3=local-12345678; b_nut=1700000000; b_lsid=ABC123;"

import uuid as _uuid
_BILI_HEADERS_WITH_COOKIE = {**_BILI_HEADERS, "Cookie": _BILI_COOKIE + f" buvid4={_uuid.uuid4().hex[:16]};"}

async def _get_bilibili_direct(url: str) -> dict:
    """直接打 Bilibili API 取得影片資訊和 CDN URL，不走 yt-dlp"""
    bvid_m = re.search(r'BV[A-Za-z0-9]+', url)
    aid_m  = re.search(r'av(\d+)', url, re.I)
    if not bvid_m and not aid_m:
        return {}
    params = {"bvid": bvid_m.group()} if bvid_m else {"aid": aid_m.group(1)}
    try:
        async with httpx.AsyncClient(timeout=20, headers=_BILI_HEADERS_WITH_COOKIE) as client:
            # Step 1: 取元數據（嘗試多個 API 端點）
            meta = None
            for api_url in ["https://api.bilibili.com/x/web-interface/view", "https://api.bilibili.com/x/web-interface/view/detail"]:
                try:
                    resp = await client.get(api_url, params=params)
                    if resp.status_code == 200:
                        meta = resp.json()
                        if meta.get("code") == 0:
                            break
                        meta = None
                except Exception:
                    continue
            if not meta or meta.get("code") != 0:
                return {}
            d = meta["data"]
            bvid  = d.get("bvid", "")
            cid   = d.get("cid", 0)
            title = d.get("title", "")
            thumb = d.get("pic", "")
            dur   = d.get("duration", 0)
            author = (d.get("owner") or {}).get("name", "")
            embed_url = f"https://player.bilibili.com/player.html?bvid={bvid}&cid={cid}&high_quality=1&danmaku=0"

            # Step 2: 取播放 URL（qn=80=1080P，qn=64=720P，不登入最高通常 480P）
            for qn in [80, 64, 32, 16]:
                pu = (await client.get(
                    "https://api.bilibili.com/x/player/playurl",
                    params={"bvid": bvid, "cid": cid, "qn": qn, "fnval": 1, "platform": "pc"}
                )).json()
                if pu.get("code") != 0:
                    continue
                durls = (pu.get("data") or {}).get("durl", [])
                if durls:
                    cdn_url = durls[0].get("url", "")
                    if cdn_url:
                        label = {80:"1080P", 64:"720P HD", 32:"480P", 16:"360P"}.get(qn, f"{qn}P")
                        return {
                            "title": title, "thumbnail": thumb, "duration": dur,
                            "uploader": author, "platform": "Bilibili",
                            "cdn_url": cdn_url, "cdn_audio_url": "",
                            "embed_url": embed_url,
                            "formats": [{"id": str(qn), "label": label, "height": 0}],
                        }
            # 拿不到直連，至少返回 embed
            return {"title": title, "thumbnail": thumb, "duration": dur,
                    "uploader": author, "platform": "Bilibili",
                    "cdn_url": "", "embed_url": embed_url,
                    "formats": [{"id": "embed", "label": "嵌入播放", "height": 0}]}
    except Exception as ex:
        print(f"[bilibili_direct] {ex}")
        return {}

# ── 小紅書直連（繞過 yt-dlp 問題）────────────────────────────────
async def _get_xhs_direct(url: str) -> dict:
    """解析小紅書影片，先追蹤短鏈後取 OG meta 裡的影片 URL"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://www.xiaohongshu.com/",
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            r = await client.get(url)
            final_url = str(r.url)
            html = r.text
        # 從 meta 取影片 URL
        video_m = re.search(r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"', html)
        if not video_m:
            video_m = re.search(r'<video[^>]+src="([^"]+)"', html)
        if not video_m:
            video_m = re.search(r'"url"\s*:\s*"(https://[^"]+\.mp4[^"]*)"', html)
        title_m  = re.search(r'<meta[^>]+og:title[^>]+content="([^"]+)"', html)
        thumb_m  = re.search(r'<meta[^>]+og:image[^>]+content="([^"]+)"', html)
        if video_m:
            return {
                "title":     (title_m.group(1) if title_m else "小紅書影片"),
                "thumbnail": (thumb_m.group(1) if thumb_m else ""),
                "cdn_url":   video_m.group(1),
                "cdn_audio_url": "",
                "duration":  0, "uploader": "",
            }
    except Exception as ex:
        print(f"[xhs_direct] {ex}")
    return {}

def extract_url_from_text(text: str) -> str:
    m = re.search(r'https?://[^\s一-鿿＀-￯　-〿⺀-⻿]+', text)
    if m:
        return m.group(0).rstrip(',.，。！？、')
    return text.strip()

async def resolve_short_url(url: str) -> str:
    text_url = extract_url_from_text(url)
    SHORT_DOMAINS = ("v.douyin.com", "v.kuaishou.com", "kuaishou.app.link",
                     "xhslink.com", "t.co", "vm.tiktok.com", "vt.tiktok.com")
    if any(d in text_url for d in SHORT_DOMAINS):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=12,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as c:
                r = await c.head(text_url)
                return str(r.url)
        except Exception:
            pass
    return text_url

EDGE_PATH = os.environ.get("EDGE_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/下載影片", StaticFiles(directory=str(DOWNLOAD_DIR)), name="downloads")

executor = ThreadPoolExecutor(max_workers=6)

DOUYIN_LIB = r"D:\tools\Douyin_TikTok_Download_API"

def _is_douyin(url: str) -> bool:
    return "douyin.com" in url or "douyinvod" in url

def _is_kuaishou(url: str) -> bool:
    return "kuaishou.com" in url

def _is_shopee_url(url: str) -> bool:
    return any(d in url for d in ("shopee.tw", "shopee.sg", "shopee.vn", "shopee.ph",
                                   "shopee.my", "shopee.co.id", "shp.ee", "sv.shopee"))

async def _get_shopee_video_info(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Referer": "https://shopee.tw/",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=headers) as c:
        r0 = await c.get(url)
        final_url = str(r0.url)

    video_page_url = final_url
    if "universal-link" in final_url:
        from urllib.parse import parse_qs, urlparse as _up
        qs = parse_qs(_up(final_url).query)
        redir = qs.get("redir", [""])[0]
        if redir:
            video_page_url = redir

    if "sv.shopee" not in video_page_url and "share-video" not in video_page_url:
        return {}

    async with httpx.AsyncClient(timeout=20, headers=headers) as c:
        r = await c.get(video_page_url)
        html = r.text

    mp4_urls = re.findall(r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*", html)
    if not mp4_urls:
        return {}

    video_url = mp4_urls[0]
    title = "蝦皮短影音"
    thumbnail = ""

    tm = re.search(r'<meta[^>]+(?:og:title)[^>]+content="([^"]+)"', html)
    if tm:
        title = tm.group(1)
    thm = re.search(r'<meta[^>]+og:image[^>]+content="([^"]+)"', html)
    if thm:
        thumbnail = thm.group(1)

    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if nd:
        try:
            nd_data = json.loads(nd.group(1))
            props = nd_data.get("props", {}).get("pageProps", {})
            title = props.get("title") or props.get("videoTitle") or title
            thumbnail = props.get("thumbnail") or props.get("coverUrl") or thumbnail
        except Exception:
            pass

    return {"title": title, "thumbnail": thumbnail, "video_url": video_url,
            "platform": "Shopee", "duration": 0, "uploader": ""}

def _parse_aweme_id(url: str) -> str:
    for pat in (r'/video/(\d+)', r'modal_id=(\d+)', r'[?&]vid=(\d+)', r'/note/(\d+)'):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return ""

async def _resolve_aweme_id(url: str) -> str:
    aid = _parse_aweme_id(url)
    if aid:
        return aid
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            aid = _parse_aweme_id(str(r.url))
            if aid:
                return aid
    except Exception:
        pass
    return ""

def _cookies_to_str(cookie_list: list) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookie_list if c.get("name") and c.get("value"))

def _cookies_to_netscape(cookie_list: list, path: str):
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookie_list:
        domain = c.get("domain", ".douyin.com")
        if not domain.startswith("."):
            domain = "." + domain.lstrip(".")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = str(int(time.time()) + 86400 * 30)
        lines.append(f"{domain}\t{flag}\t{c.get('path','/')}\t{secure}\t{expiry}\t{c['name']}\t{c['value']}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

async def _get_douyin_fast(url: str) -> dict:
    """超快取得抖音影片 CDN（不需要瀏覽器，1-3 秒）"""
    # ── 方法 1：tikwm.com（同時支援抖音/TikTok，最可靠）──────────
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.post("https://tikwm.com/api/",
                data={"url": url, "hd": "1"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            d = r.json()
            if d.get("code") == 0 and d.get("data"):
                dat = d["data"]
                cdn = dat.get("hdplay") or dat.get("play") or ""
                if cdn:
                    return {
                        "title":     dat.get("title", "抖音影片"),
                        "thumbnail": dat.get("origin_cover") or dat.get("cover", ""),
                        "duration":  dat.get("duration", 0),
                        "uploader":  (dat.get("author") or {}).get("nickname", ""),
                        "cdn_url":   cdn, "cdn_audio_url": "",
                    }
    except Exception as e:
        print(f"[douyin_fast/tikwm] {e}")

    # ── 方法 2：douyin.wtf 公開 API ────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
            r = await client.get("https://api.douyin.wtf/api",
                params={"url": url, "minimal": "false"},
                headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                d = r.json()
                cdn = (d.get("video_data") or {}).get("nwm_video_url_HQ") or \
                      (d.get("video_data") or {}).get("nwm_video_url") or ""
                if cdn:
                    return {
                        "title":     d.get("desc", "抖音影片"),
                        "thumbnail": d.get("cover", ""),
                        "duration":  0,
                        "uploader":  d.get("author", {}).get("nickname", ""),
                        "cdn_url":   cdn, "cdn_audio_url": "",
                    }
    except Exception as e:
        print(f"[douyin_fast/wtf] {e}")

    # ── 方法 3：snaptik.app 公開 API（備用）────────────────────
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
            r = await client.post("https://snaptik.app/action-2025.php",
                data={"url": url, "lang": "en"},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Content-Type": "application/x-www-form-urlencoded",
                })
            import re as _re
            # 從 HTML 回應中解析 CDN URL
            cdn_m = _re.search(r'https?://[^"\'<>]+?\.mp4[^"\'<>]*', r.text)
            if cdn_m:
                return {
                    "title": "抖音影片", "thumbnail": "",
                    "duration": 0, "uploader": "",
                    "cdn_url": cdn_m.group(0), "cdn_audio_url": "",
                }
    except Exception as e:
        print(f"[douyin_fast/snaptik] {e}")

    return {}

async def _get_douyin_info_api(aweme_id: str) -> dict:
    import sys as _sys
    if DOUYIN_LIB not in _sys.path:
        _sys.path.insert(0, DOUYIN_LIB)

    result = {"title": "抖音影片", "thumbnail": "", "duration": 0,
              "uploader": "", "video_url": None, "aweme_id": aweme_id}
    try:
        from crawlers.douyin.web.utils import BogusManager
        from crawlers.douyin.web.models import PostDetail
        from urllib.parse import urlencode as _ue

        cookie_data = _load_platform_cookies().get("douyin", [])
        if cookie_data:
            cookie_str = _cookies_to_str(cookie_data)
        else:
            import yaml as _yaml, os as _os
            cfg_path = _os.path.join(DOUYIN_LIB, "crawlers/douyin/web/config.yaml")
            with open(cfg_path, encoding="utf-8") as f:
                cfg = _yaml.safe_load(f)
            cookie_str = cfg["TokenManager"]["douyin"]["headers"]["Cookie"]

        UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        params = PostDetail(aweme_id=aweme_id).dict()
        params["msToken"] = ""
        a_bogus = BogusManager.ab_model_2_endpoint(params, UA)
        endpoint = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{_ue(params)}&a_bogus={a_bogus}"

        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(endpoint, headers={
                "User-Agent": UA,
                "Referer": "https://www.douyin.com/",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Cookie": cookie_str,
            })
            data = resp.json()

        aweme = data.get("aweme_detail") or {}
        if not aweme:
            fd = data.get("filter_detail", {})
            result["_error"] = fd.get("filter_reason", "no_data")
            return result

        result["title"] = (aweme.get("desc") or "抖音影片")[:80]
        result["duration"] = int(aweme.get("duration", 0) or 0) // 1000
        try: result["uploader"] = aweme["author"]["nickname"] or ""
        except Exception: pass
        try: result["thumbnail"] = aweme["video"]["cover"]["url_list"][0] or ""
        except Exception: pass
        for field in ("play_addr", "download_addr"):
            try:
                all_urls = aweme["video"][field]["url_list"]
                if all_urls:
                    result["video_url"] = await _pick_fastest_url(
                        all_urls,
                        {"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"})
                    if not result["video_url"]:
                        result["video_url"] = all_urls[0]
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"[douyin_api] 錯誤：{e}")
    return result

async def _pw_browser(p):
    _args = ["--no-sandbox", "--disable-blink-features=AutomationControlled",
             "--autoplay-policy=no-user-gesture-required"]
    # 本機 Windows：優先用 Edge
    if os.path.exists(EDGE_PATH):
        try:
            return await p.chromium.launch(executable_path=EDGE_PATH, headless=True, args=_args)
        except Exception:
            pass
    # 雲端 / Linux：用系統 Chromium（需 playwright install chromium）
    return await p.chromium.launch(headless=True, args=_args)

async def _pick_fastest_url(urls: list[str], headers: dict | None = None, timeout: float = 4.0) -> str:
    if not urls:
        return ""
    if len(urls) == 1:
        return urls[0]
    hdrs = headers or {}
    import time as _time

    async def probe(url: str):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as cl:
                t0 = _time.monotonic()
                r = await cl.head(url, headers=hdrs)
                if r.status_code < 400:
                    return (_time.monotonic() - t0, url)
        except Exception:
            pass
        return (999.0, url)

    results = await asyncio.gather(*[probe(u) for u in urls[:6]])
    best = min(results, key=lambda x: x[0])
    print(f"[cdn_pick] best={best[1][:80]}  latency={best[0]:.2f}s")
    return best[1]

async def _get_douyin_cdn(video_url: str) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {}

    result = {"title": "抖音影片", "thumbnail": "", "duration": 0,
              "uploader": "", "cdn_url": None, "cdn_audio_url": None, "formats": []}

    CDN_DOMAINS = ("zjcdn.com", "douyinvod.com", "v26-efforg", "pull-f5",
                   "toutiaoimg.com/obj/tos", "v19-efforg", "v3-efforg",
                   "bytedance.com/obj", "p3-sign", "aweme.snssdk", "douyinvod.com")
    COVER_PATTERNS = ("tos-cn-p", "tos-cn-i", "tos-cn-avt", "douyinpic.com",
                      "p3-sign.douyinpic", "p6-sign", "p9-sign")

    try:
        async with async_playwright() as p:
            browser = await _pw_browser(p)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800})
            await _apply_cookies(ctx, video_url)
            page = await ctx.new_page()
            await _stealth(page)

            found = asyncio.Event()
            api_found = asyncio.Event()
            cdn_url: list[str] = []
            cdn_audio_url: list[str] = []
            cover_url: list[str] = []
            api_done: list[bool] = []

            async def on_response(resp):
                rurl = resp.url
                ct = resp.headers.get("content-type", "")

                if "aweme/v1/web/aweme/detail" in rurl and not api_done:
                    api_done.append(True)
                    try:
                        body = await resp.json()
                        aweme = body.get("aweme_detail") or {}
                        if aweme:
                            for field in ("play_addr", "download_addr"):
                                try:
                                    all_urls = aweme["video"][field]["url_list"]
                                    if all_urls:
                                        cdn_url.clear()
                                        cdn_url.append(all_urls[0])
                                        found.set()
                                        break
                                except Exception:
                                    pass

                            _lbl_order = {"360P":1,"480P":2,"540P":3,"720P HD":4,"1080P":5,"2K":6,"4K":7}
                            _best: dict = {}
                            try:
                                for _br in aweme.get("video", {}).get("bit_rate", []):
                                    _br_urls = _br.get("play_addr", {}).get("url_list", [])
                                    if not _br_urls: continue
                                    _qt  = _br.get("quality_type", 0)
                                    _bps = _br.get("bitrate", 0)
                                    _h   = _br.get("play_addr", {}).get("height", 0) or 0
                                    if _h >= 2160:   _lbl = "4K"
                                    elif _h >= 1440: _lbl = "2K"
                                    elif _h >= 1080: _lbl = "1080P"
                                    elif _h >= 720:  _lbl = "720P HD"
                                    elif _h >= 540:  _lbl = "540P"
                                    elif _h >= 480:  _lbl = "480P"
                                    elif _h > 0:     _lbl = f"{_h}P"
                                    else:
                                        _qt_map = {0:"360P",1:"480P",2:"540P",3:"720P HD",4:"1080P",5:"2K",6:"4K"}
                                        _lbl = _qt_map.get(_qt) or (
                                            "1080P" if _bps > 3_000_000 else
                                            "720P HD" if _bps > 1_500_000 else
                                            "540P"  if _bps > 1_000_000 else
                                            "480P"  if _bps > 700_000 else "360P")
                                    if _lbl not in _best or _bps > _best[_lbl]["bitrate"]:
                                        _best[_lbl] = {"id": str(_qt), "label": _lbl,
                                                       "url": _br_urls[0], "bitrate": _bps}
                            except Exception as _ex:
                                print(f"[douyin_cdn] bit_rate parse: {_ex}")
                            if _best:
                                result["formats"] = sorted(_best.values(),
                                    key=lambda x: _lbl_order.get(x["label"], 0))

                            dur_ms = int(aweme.get("duration", 0) or 0)
                            result["duration"] = dur_ms // 1000 if dur_ms > 1000 else dur_ms
                            if aweme.get("desc"): result["title"] = aweme["desc"][:80]
                            try: result["uploader"] = aweme["author"]["nickname"] or ""
                            except Exception: pass
                            try: result["thumbnail"] = aweme["video"]["cover"]["url_list"][0] or ""
                            except Exception: pass

                    except Exception as ex:
                        print(f"[douyin_cdn] API 攔截失敗（將 fallback）: {ex}")
                    finally:
                        api_found.set()
                    return

                if "douyinstatic.com" in rurl: return
                is_cdn = ("video" in ct or "audio" in ct) or any(d in rurl for d in CDN_DOMAINS)
                if not is_cdn: return

                is_audio = ("audio" in ct) or any(k in rurl for k in ("audio", "mp4a", "aac-", "m4a-", "media-audio"))
                if is_audio:
                    if not cdn_audio_url:
                        cdn_audio_url.append(rurl)
                else:
                    if not api_done and not cdn_url:
                        cdn_url.append(rurl)
                        found.set()
                if not cover_url and any(pat in rurl for pat in COVER_PATTERNS):
                    if "image" in ct or rurl.endswith((".jpg", ".jpeg", ".webp", ".png")):
                        cover_url.append(rurl)

            page.on("response", on_response)
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome={runtime:{}};"
                "window.outerWidth=1280;window.outerHeight=800;")

            await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            try:
                await page.evaluate("document.querySelector('video')?.play()")
            except Exception:
                pass

            try:
                await asyncio.wait_for(found.wait(), timeout=12)
            except asyncio.TimeoutError:
                pass

            try:
                await page.evaluate("document.querySelector('video')?.play()")
            except Exception:
                pass

            if not api_found.is_set():
                try:
                    await asyncio.wait_for(api_found.wait(), timeout=6)
                except asyncio.TimeoutError:
                    pass

            await page.wait_for_timeout(3000)

            if not result["title"] or result["title"] == "抖音影片":
                try:
                    result["title"] = (await page.evaluate(
                        "document.querySelector('meta[property=\"og:title\"]')?.content"
                        "||document.querySelector('h1')?.textContent||document.title||'抖音影片'"
                    ) or "抖音影片").replace("- 抖音", "").strip()
                except Exception:
                    pass
            if not result["thumbnail"]:
                try:
                    result["thumbnail"] = await page.evaluate("""
                        document.querySelector('meta[property="og:image"]')?.content
                        || document.querySelector('meta[name="twitter:image"]')?.content
                        || document.querySelector('meta[itemprop="image"]')?.content
                        || document.querySelector('video')?.poster
                        || ''
                    """) or ""
                except Exception:
                    pass
                if not result["thumbnail"] and cover_url:
                    result["thumbnail"] = cover_url[0]

            await browser.close()

            if cdn_url:
                result["cdn_url"] = cdn_url[0]
            if cdn_audio_url:
                result["cdn_audio_url"] = cdn_audio_url[0]
    except Exception as e:
        print(f"[douyin_cdn] 錯誤：{e}")

    return result


async def _get_kuaishou_cdn(video_url: str) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {}

    result = {"title": "快手影片", "thumbnail": "", "duration": 0,
              "uploader": "", "cdn_url": None, "formats": []}
    try:
        async with async_playwright() as p:
            browser = await _pw_browser(p)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800})
            await _apply_cookies(ctx, video_url)
            page = await ctx.new_page()
            await _stealth(page)

            found = asyncio.Event()

            async def on_response(resp):
                if "kuaishou.com/graphql" not in resp.url:
                    return
                try:
                    body = await resp.json()
                    data = body.get("data") or {}
                    photo = None
                    for key in ("visionVideoDetail", "visionVideoDetailExtra",
                                "visionVideoDetailOuter", "visionVideoGetPlayInfo"):
                        obj = data.get(key)
                        if isinstance(obj, dict):
                            photo = obj.get("photo") or obj.get("videoInfo") or obj
                            if photo and (photo.get("mainMvUrls") or photo.get("photoUrl")):
                                break
                            photo = None
                    if not photo:
                        return

                    cdn = ""
                    for field in ("mainMvUrls", "photoUrl", "urls", "videoUrl"):
                        v = photo.get(field)
                        if isinstance(v, list) and v:
                            cdn = (v[0].get("url") or v[0].get("cdn") or "")
                            break
                        elif isinstance(v, str) and v.startswith("http"):
                            cdn = v; break
                    if cdn:
                        result["cdn_url"] = cdn
                        found.set()

                    caption = photo.get("caption") or photo.get("title") or ""
                    if caption: result["title"] = caption[:80]
                    user = photo.get("user") or photo.get("userInfo") or {}
                    result["uploader"] = user.get("name","") or user.get("userName","")
                    covers = photo.get("coverUrls") or photo.get("webpCoverUrls") or []
                    if covers:
                        result["thumbnail"] = covers[0].get("url","")
                    dur = photo.get("duration",0) or 0
                    result["duration"] = dur // 1000 if dur > 1000 else dur
                except Exception as ex:
                    print(f"[kuaishou_cdn] GraphQL parse: {ex}")

            page.on("response", on_response)
            try:
                await page.goto(video_url, wait_until="domcontentloaded", timeout=25000)
                try:
                    await asyncio.wait_for(found.wait(), timeout=20)
                except asyncio.TimeoutError:
                    print("[kuaishou_cdn] 超時：未攔截到 GraphQL 影片 URL")
            except Exception as ex:
                print(f"[kuaishou_cdn] page load: {ex}")
            finally:
                await page.close(); await ctx.close(); await browser.close()
    except Exception as ex:
        print(f"[kuaishou_cdn] 錯誤：{ex}")
    return result

# ── 快手直連 API（不需要瀏覽器，直接打 GraphQL）──────────────────
_KUAISHOU_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://www.kuaishou.com/",
    "Content-Type": "application/json",
}

_KUAISHOU_QUERY = """
query visionVideoDetail($photoId: String, $page: String, $type: String) {
  visionVideoDetail(photoId: $photoId, page: $page, type: $type) {
    status
    photo {
      id
      caption
      duration
      height
      width
      coverUrls { url }
      mainMvUrls { url }
      photoUrl
      user { userName }
    }
  }
}
"""

async def _get_kuaishou_direct(url: str) -> dict:
    """直接打 Kuaishou GraphQL API，不走 yt-dlp 或 Playwright"""
    # 從 URL 提取 photoId
    photo_m = re.search(r'(?:short-video|photo|v)/([a-zA-Z0-9]+)', url)
    if not photo_m:
        return {}
    photo_id = photo_m.group(1)
    
    for page_val in ("home", "search"):
        try:
            async with httpx.AsyncClient(timeout=15, headers=_KUAISHOU_HEADERS, follow_redirects=True) as client:
                payload = {
                    "operationName": "visionVideoDetail",
                    "variables": {"photoId": photo_id, "page": page_val, "type": "short_video"},
                    "query": _KUAISHOU_QUERY,
                }
                resp = await client.post("https://www.kuaishou.com/graphql", json=payload)
                if resp.status_code != 200:
                    continue
                d = resp.json()
                data = d.get("data", {}) or {}
                detail = data.get("visionVideoDetail", {}) or {}
                photo = detail.get("photo") or {}
                if not photo or not photo.get("id"):
                    continue
                
                title = photo.get("caption", "快手影片") or "快手影片"
                duration = photo.get("duration", 0) or 0
                
                # 取 CDN URL
                cdn = ""
                mv_urls = photo.get("mainMvUrls") or []
                if mv_urls and isinstance(mv_urls, list):
                    cdn = mv_urls[0].get("url", "") or ""
                if not cdn:
                    cdn = photo.get("photoUrl", "") or ""
                if not cdn:
                    continue
                
                # 取縮圖
                thumb = ""
                covers = photo.get("coverUrls") or []
                if covers and isinstance(covers, list):
                    thumb = covers[0].get("url", "") or ""
                
                # 取上傳者
                user = photo.get("user") or {}
                uploader = user.get("userName", "") or ""
                
                duration_sec = duration // 1000 if duration > 1000 else duration
                
                print(f"[kuaishou_direct] OK photo_id={photo_id}")
                return {
                    "title": title[:80],
                    "thumbnail": thumb,
                    "duration": duration_sec,
                    "uploader": uploader,
                    "cdn_url": cdn,
                    "platform": "Kuaishou",
                    "formats": [{"id": "best", "label": "原始畫質", "height": 0}],
                }
        except Exception as ex:
            print(f"[kuaishou_direct] {page_val} 失敗: {ex}")
            continue
    
    # fallback：使用 tikwm（POST，跟抖音一樣的方式）
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            r = await client.post("https://tikwm.com/api/",
                data={"url": url, "hd": "1"},
                headers={"User-Agent": "Mozilla/5.0"})
            d = r.json()
            if d.get("code") == 0 and d.get("data"):
                dat = d["data"]
                cdn = dat.get("hdplay") or dat.get("play") or ""
                if cdn:
                    return {
                        "title": (dat.get("title", "快手影片") or "")[:80],
                        "thumbnail": dat.get("cover", "") or dat.get("origin_cover", "") or "",
                        "duration": dat.get("duration", 0) or 0,
                        "uploader": (dat.get("author") or {}).get("nickname", "") or "",
                        "cdn_url": cdn, "cdn_audio_url": "",
                        "platform": "Kuaishou",
                        "formats": [{"id": "best", "label": "原始畫質", "height": 0}],
                    }
    except Exception as e:
        print(f"[kuaishou_tikwm] {e}")
    
    return {}


async def _get_tiktok_via_tikwm(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(
                "https://tikwm.com/api/",
                data={"url": url, "hd": "1"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"},
            )
            d = r.json()
            if d.get("code") == 0 and d.get("data"):
                dat = d["data"]
                cdn = dat.get("play") or dat.get("hdplay") or ""
                return {
                    "title":     dat.get("title", ""),
                    "thumbnail": dat.get("origin_cover") or dat.get("cover", ""),
                    "duration":  dat.get("duration", 0),
                    "uploader":  (dat.get("author") or {}).get("nickname", ""),
                    "cdn_url":   cdn,
                    "platform":  "TikTok",
                }
    except Exception as ex:
        print(f"[tikwm] {ex}")
    return {}


def _lux_info(url: str) -> dict:
    r = subprocess.run(
        [str(LUX_PATH), "-j", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25
    )
    try:
        data = json.loads(r.stdout.strip()) if r.stdout.strip() else []
    except json.JSONDecodeError:
        data = []
    item = data[0] if data else {}

    streams = item.get("streams") or {}
    formats = []
    for sid, s in streams.items():
        qs = s.get("quality", "")
        if "4K" in qs or "2160" in qs:        lbl = "4K"
        elif "2K" in qs or "1440" in qs:      lbl = "2K"
        elif "1080" in qs and "60" in qs:      lbl = "1080P 60fps"
        elif "1080" in qs:                     lbl = "1080P"
        elif "720" in qs:                      lbl = "720P HD"
        elif "480" in qs:                      lbl = "480P"
        elif "360" in qs:                      lbl = "360P"
        else:                                  lbl = qs[:12] or sid
        formats.append({"id": sid, "label": lbl})
    _ord = {"360P":0,"480P":1,"720P HD":2,"1080P":3,"1080P 60fps":4,"2K":5,"4K":6}
    formats.sort(key=lambda f: _ord.get(f["label"], 3))

    return {"title": item.get("title", ""), "thumbnail": "",
            "duration": 0, "uploader": item.get("site", ""), "formats": formats}

def _lux_download(url: str, out_dir: Path) -> tuple[str, str]:
    before = set(out_dir.glob("*"))
    r = subprocess.run(
        [str(LUX_PATH), "-o", str(out_dir), url],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
    )
    after = set(out_dir.glob("*"))
    new_files = [f for f in (after - before) if f.suffix.lower() in (".mp4", ".mkv", ".flv", ".webm", ".m4v")]
    if new_files:
        return new_files[0].name, str(out_dir)
    vids = sorted([f for f in out_dir.iterdir() if f.suffix.lower() in (".mp4", ".mkv", ".flv", ".webm")],
                  key=lambda x: x.stat().st_mtime, reverse=True)
    if vids:
        return vids[0].name, str(out_dir)
    raise Exception(f"Lux 下載失敗：{(r.stderr or r.stdout)[:200]}")

async def _download_from_cdn(cdn_url: str, out_dir: Path, title: str,
                             cdn_audio_url: str | None = None) -> tuple[str, str]:
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
    }

    async def _dl_file(url: str, fpath: Path):
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={**headers, "Range": "bytes=0-"}) as r:
                with open(fpath, "wb") as f:
                    async for chunk in r.aiter_bytes(512 * 1024):
                        f.write(chunk)

    if cdn_audio_url:
        video_tmp = out_dir / f"{safe}_v.mp4"
        audio_tmp = out_dir / f"{safe}_a.m4a"
        final     = out_dir / f"{safe}.mp4"
        await asyncio.gather(_dl_file(cdn_url, video_tmp), _dl_file(cdn_audio_url, audio_tmp))
        subprocess.run(
            [FFMPEG_BIN, "-y", "-i", str(video_tmp), "-i", str(audio_tmp),
             "-c", "copy", str(final)],
            capture_output=True, timeout=120
        )
        video_tmp.unlink(missing_ok=True)
        audio_tmp.unlink(missing_ok=True)
        if not final.exists():
            await _dl_file(cdn_url, final)
        return final.name, str(out_dir)
    else:
        fpath = out_dir / f"{safe}.mp4"
        await _dl_file(cdn_url, fpath)
        return fpath.name, str(out_dir)


# ── Cookies 管理 ──────────────────────────────────────────
@app.post("/api/cookies/save")
async def save_cookies(platform: str = Form(...), cookies_json: str = Form(...)):
    try:
        cookies = json.loads(cookies_json)
        if not isinstance(cookies, list):
            return JSONResponse({"ok": False, "error": "格式必須是 JSON 陣列"})
        normalized = []
        for c in cookies:
            if not c.get("name") or not c.get("value"):
                continue
            normalized.append({
                "name": c["name"], "value": c["value"],
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
            })
        data = _load_platform_cookies()
        data[platform.lower()] = normalized
        COOKIES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return JSONResponse({"ok": True, "count": len(normalized)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

@app.get("/api/cookies/status")
def cookies_status():
    data = _load_platform_cookies()
    return JSONResponse({
        plat: len(data.get(plat, [])) for plat in ["douyin", "kuaishou", "tiktok"]
    })

@app.delete("/api/cookies/{platform}")
def delete_cookies(platform: str):
    data = _load_platform_cookies()
    data.pop(platform.lower(), None)
    COOKIES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"ok": True})

# ── 首頁 ──────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(str(BASE_DIR / "index.html"),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

# ── URL 預覽 ──────────────────────────────────────────────
@app.get("/api/video-info")
async def video_info(url: str):
    real_url = await resolve_short_url(url)

    if _is_shopee_url(real_url):
        info = await _get_shopee_video_info(real_url)
        if info.get("video_url"):
            return JSONResponse({
                "title": info["title"], "thumbnail": info["thumbnail"],
                "duration": 0, "uploader": info["uploader"],
                "platform": "Shopee", "url": real_url,
                "has_video": True, "cdn_url": info["video_url"],
                "formats": [{"id": "best", "label": "原始畫質", "height": 0}],
            })

    if _is_douyin(real_url):
        from urllib.parse import quote as _q
        # 快速 API（tikwm/douyin.wtf/snaptik），不需要瀏覽器，秒級回應
        fast = await _get_douyin_fast(real_url)
        if fast.get("cdn_url"):
            cdn_f = fast["cdn_url"]
            return JSONResponse({
                "title":         fast.get("title", "抖音影片"),
                "thumbnail":     fast.get("thumbnail", ""),
                "duration":      fast.get("duration", 0),
                "uploader":      fast.get("uploader", ""),
                "platform":      "Douyin",
                "url":           real_url,
                "has_video":     True,
                "proxy_url":     f"/api/proxy-video?url={_q(cdn_f, safe='')}&referer=https://www.douyin.com/",
                "cdn_url":       cdn_f,
                "cdn_audio_url": "",
                "formats":       [{"id":"best","label":"原始畫質","height":0}],
            })
        # 快速 API 全失敗，回傳錯誤（Playwright 在雲端跑不動）
        return JSONResponse({"error":"抖音解析失敗，請稍後重試","error_hint":"抖音 API 暫時無法連線，請過幾秒再試","url":real_url})

    if _is_kuaishou(real_url):
        from urllib.parse import quote as _q
        # 優先：直接打 Kuaishou GraphQL API（不需要瀏覽器，雲端可用）
        ks_direct = await _get_kuaishou_direct(real_url)
        if ks_direct.get("cdn_url"):
            cdn_ks = ks_direct["cdn_url"]
            proxy_ks = f"/api/proxy-video?url={_q(cdn_ks, safe='')}&referer=https://www.kuaishou.com/"
            return JSONResponse({
                "title": ks_direct.get("title","快手影片"),
                "thumbnail": ks_direct.get("thumbnail",""),
                "duration": ks_direct.get("duration",0),
                "uploader": ks_direct.get("uploader",""),
                "platform":"Kuaishou","url":real_url,"has_video":True,
                "proxy_url":proxy_ks,"cdn_url":cdn_ks,
                "formats":[{"id":"best","label":"原始畫質","height":0}],
            })
        # fallback：yt-dlp
        loop_ks = asyncio.get_event_loop()
        def _ks_ytdlp():
            opts_ks = {"quiet":True,"no_warnings":True,"skip_download":True,
                       "http_headers":{"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}}
            with yt_dlp.YoutubeDL(opts_ks) as ydl:
                return ydl.extract_info(real_url, download=False)
        try:
            ks_info = await asyncio.wait_for(loop_ks.run_in_executor(executor, _ks_ytdlp), timeout=15)
            if ks_info and ks_info.get("url"):
                ks_cdn = ks_info.get("url","")
                ks_proxy = f"/api/proxy-video?url={_q(ks_cdn, safe='')}&referer=https://www.kuaishou.com/" if ks_cdn else ""
                return JSONResponse({
                    "title": ks_info.get("title","快手影片"), "thumbnail": ks_info.get("thumbnail",""),
                    "duration": ks_info.get("duration",0), "uploader": ks_info.get("uploader",""),
                    "platform":"Kuaishou","url":real_url,"has_video":bool(ks_cdn),
                    "proxy_url":ks_proxy,"cdn_url":ks_cdn,
                    "formats":[{"id":"best","label":"原始畫質","height":0}],
                })
        except Exception:
            pass
        # fallback：Playwright（本機備用）
        cdn_info = await _get_kuaishou_cdn(real_url)
        cdn = cdn_info.get("cdn_url") or ""
        proxy = f"/api/proxy-video?url={_q(cdn, safe='')}&referer=https://www.kuaishou.com/" if cdn else ""
        if cdn or cdn_info.get("title","快手影片") != "快手影片":
            return JSONResponse({
                "title":     cdn_info.get("title", "快手影片"),
                "thumbnail": cdn_info.get("thumbnail", ""),
                "duration":  cdn_info.get("duration", 0),
                "uploader":  cdn_info.get("uploader", ""),
                "platform":  "Kuaishou",
                "url":       real_url,
                "has_video": bool(cdn),
                "proxy_url": proxy,
                "cdn_url":   cdn,
                "formats":   [{"id":"best","label":"原始畫質","height":0}],
            })

    loop = asyncio.get_event_loop()

    if "tiktok.com" in real_url:
        from urllib.parse import quote as _qtk
        tk = await _get_tiktok_via_tikwm(real_url)
        if tk.get("cdn_url"):
            return JSONResponse({
                **tk, "url": real_url,
                "proxy_url": "",
                "formats": [{"id": "best", "label": "原始畫質（無浮水印）", "height": 0}],
            })

    # ── B站：直打 Bilibili API（雲端/本機都能用，不靠 yt-dlp）─────
    is_bilibili = "bilibili.com" in real_url or "b23.tv" in real_url
    if is_bilibili:
        bili = await _get_bilibili_direct(real_url)
        if bili.get("title"):
            from urllib.parse import quote as _qb
            cdn_b = bili.get("cdn_url","")
            prx_b = f"/api/proxy-video?url={_qb(cdn_b,safe='')}&referer=https://www.bilibili.com/" if cdn_b else ""
            return JSONResponse({
                "title":     bili["title"],     "thumbnail": bili.get("thumbnail",""),
                "duration":  bili.get("duration",0), "uploader": bili.get("uploader",""),
                "platform":  "Bilibili",        "url":       real_url,
                "has_video": bool(cdn_b),        "proxy_url": prx_b,
                "cdn_url":   cdn_b,              "cdn_audio_url": "",
                "embed_url": bili.get("embed_url",""),
                "formats":   bili.get("formats",[{"id":"best","label":"最高畫質","height":0}]),
            })

    # ── 小紅書：直解 HTML（繞過 yt-dlp 格式問題）──────────────────
    if "xiaohongshu.com" in real_url or "xhslink.com" in real_url:
        xhs = await _get_xhs_direct(real_url)
        if xhs.get("cdn_url"):
            from urllib.parse import quote as _qx
            cdn_x = xhs["cdn_url"]
            prx_x = f"/api/proxy-video?url={_qx(cdn_x,safe='')}&referer=https://www.xiaohongshu.com/"
            return JSONResponse({
                "title": xhs.get("title","小紅書影片"), "thumbnail": xhs.get("thumbnail",""),
                "duration": 0, "uploader": "", "platform": "XiaoHongShu", "url": real_url,
                "has_video": True, "proxy_url": prx_x, "cdn_url": cdn_x, "cdn_audio_url": "",
                "formats": [{"id":"best","label":"原始畫質","height":0}],
            })

    # Lux（本機 Windows 專用，雲端不走這裡）
    if _is_lux_platform(real_url) and not is_bilibili:
        try:
            info = await loop.run_in_executor(executor, _lux_info, real_url)
            if info and info.get("title"):
                return JSONResponse({"title": info["title"], "thumbnail": "",
                                     "duration": info.get("duration",0), "uploader": info.get("uploader",""),
                                     "platform": "Lux", "url": real_url,
                                     "formats": info.get("formats",[])})
        except Exception:
            pass

    # ── YouTube：優先走 Invidious（繞過雲端 IP 封鎖）─────────────
    if "youtube.com" in real_url or "youtu.be" in real_url:
        from urllib.parse import quote as _qyt
        inv_data = await _get_youtube_via_invidious(real_url)
        if inv_data.get("cdn_url"):
            cdn_yt = inv_data["cdn_url"]
            proxy_yt = f"/api/proxy-video?url={_qyt(cdn_yt, safe='')}&referer=https://www.youtube.com/"
            return JSONResponse({
                "title":     inv_data.get("title",""),
                "thumbnail": inv_data.get("thumbnail",""),
                "duration":  inv_data.get("duration",0),
                "uploader":  inv_data.get("uploader",""),
                "platform":  "YouTube",
                "url":       real_url,
                "has_video": True,
                "proxy_url": proxy_yt,
                "cdn_url":   cdn_yt,
                "cdn_audio_url": "",
                "formats":   inv_data.get("formats",[]),
            })
        # Invidious 全失敗 → fallback yt-dlp Android

    def _info():
        from urllib.parse import urlparse as _up
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        }
        # YouTube：Android 客戶端備用（Invidious 已優先嘗試）
        if "youtube.com" in real_url or "youtu.be" in real_url:
            opts["extractor_args"] = {"youtube": {"player_client": ["android", "android_embedded", "web"]}}
        import tempfile, os as _os
        cookies_list = _get_cookies_for_url(real_url)
        _tmp_cookie_file = None
        if cookies_list:
            try:
                ck_lines = ["# Netscape HTTP Cookie File\n"]
                for c in cookies_list:
                    dom = c.get("domain","")
                    if dom and not dom.startswith("."): dom = "." + dom
                    ck_lines.append("\t".join([
                        dom, "TRUE", c.get("path","/"),
                        "TRUE" if c.get("secure") else "FALSE",
                        str(int(c.get("expires",0) or 0)),
                        c.get("name",""), c.get("value","")
                    ]) + "\n")
                tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                tf.writelines(ck_lines); tf.close()
                opts["cookiefile"] = tf.name
                _tmp_cookie_file = tf.name
            except Exception:
                pass
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(real_url, download=False)
        finally:
            if _tmp_cookie_file:
                try: _os.unlink(_tmp_cookie_file)
                except Exception: pass
    try:
        info = await loop.run_in_executor(executor, _info)
        all_fmts = info.get("formats") or []
        seen_h: set = set()
        yt_formats = []
        for f in sorted(all_fmts, key=lambda x: x.get("height") or 0, reverse=True):
            h = f.get("height")
            if h and h not in seen_h and f.get("vcodec","none") != "none":
                seen_h.add(h)
                lbl = ("4K" if h>=2160 else "2K" if h>=1440 else f"{h}P") + (" HD" if h==720 else "")
                yt_formats.append({"id": f"h{h}", "label": lbl, "height": h})
            if len(yt_formats) >= 5: break
        if not yt_formats:
            yt_formats = [{"id":"best","label":"最高畫質","height":0}]

        from urllib.parse import quote as _q2
        best_cdn = ""; cdn_audio = ""

        _MANIFEST_PROTO = ("http_dash_segments", "m3u8", "m3u8_native", "dash")
        def _is_mf(f):
            u = (f.get("url") or "").lower()
            return (any(x in u for x in ('.m3u8', '.mpd', 'm3u8?', 'manifest'))
                    or f.get("protocol","") in _MANIFEST_PROTO)

        combined = [f for f in all_fmts
                    if f.get("vcodec","none") != "none"
                    and f.get("acodec","none") != "none"
                    and f.get("url") and not _is_mf(f)]
        if combined:
            best = max(combined, key=lambda x: (x.get("height") or 0, x.get("tbr") or 0))
            best_cdn = best.get("url","")
        else:
            vfmts = [f for f in all_fmts
                     if f.get("vcodec","none") != "none"
                     and f.get("url") and not _is_mf(f)]
            if vfmts:
                best_cdn = max(vfmts, key=lambda x: x.get("height") or 0).get("url","")
            afmts = [f for f in all_fmts
                     if f.get("acodec","none") != "none"
                     and f.get("vcodec","none") == "none"
                     and f.get("url") and not _is_mf(f)]
            cdn_audio = max(afmts, key=lambda x: x.get("abr") or 0).get("url","") if afmts else ""
            if not best_cdn:
                for f in reversed(all_fmts):
                    if f.get("url") and not _is_mf(f):
                        best_cdn = f["url"]; break
        if not best_cdn:
            u = info.get("url","")
            if u and not any(x in u.lower() for x in ('.m3u8','.mpd','m3u8?','manifest')):
                best_cdn = u

        origin = re.sub(r'(https?://[^/]+).*', r'\1', real_url)
        proxy_url = f"/api/proxy-video?url={_q2(best_cdn, safe='')}&referer={_q2(origin, safe='')}" if best_cdn else ""

        return JSONResponse({"title": info.get("title",""), "thumbnail": info.get("thumbnail",""),
                             "duration": info.get("duration",0), "uploader": info.get("uploader",""),
                             "platform": info.get("extractor_key",""), "url": real_url,
                             "proxy_url": proxy_url, "cdn_url": best_cdn,
                             "cdn_audio_url": cdn_audio,
                             "formats": yt_formats})
    except Exception as ex:
        err_str = str(ex)
        el = err_str.lower()
        hint = ""
        if any(k in el for k in ("login", "cookie", "sign in", "authentication", "403", "forbidden", "private")):
            hint = "此影片需要登入 Cookies，請至設定頁面貼上 Cookies 後再試"
        elif any(k in el for k in ("geo", "region", "not available in your country", "georestrict")):
            hint = "此影片有地區限制，無法從目前位置觀看"
        elif any(k in el for k in ("not found", "removed", "deleted", "does not exist", "404")):
            hint = "此影片已刪除或不存在"
        elif _is_kuaishou(real_url):
            hint = "快手影片解析失敗。如需下載，請至設定頁面貼上快手 Cookies 後再試"
        elif "tiktok.com" in real_url:
            hint = "TikTok 影片解析失敗，請至設定頁面貼上 TikTok Cookies"
        return JSONResponse({"error": err_str, "error_hint": hint, "resolved_url": real_url})

# ── 下載去水印 ────────────────────────────────────────────
@app.post("/api/download")
async def download_video(url: str = Form(...), title: str = Form("影片"), save_path: str = Form("")):
    real_url = await resolve_short_url(url)
    if save_path and Path(save_path).is_absolute():
        try:
            out_dir = Path(save_path)
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            out_dir = DOWNLOAD_DIR
    else:
        out_dir = DOWNLOAD_DIR

    if _is_douyin(real_url):
        import tempfile as _tf

        cookie_data = _load_platform_cookies().get("douyin", [])
        tmp_ck = None

        if cookie_data:
            tmp_ck = _tf.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
            _cookies_to_netscape(cookie_data, tmp_ck.name)
            tmp_ck.close()
            safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
            tmpl = str(out_dir / f"{safe}.%(ext)s")
            opts_dy = {
                "format": "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
                "outtmpl": tmpl, "quiet": True, "no_warnings": True,
                "merge_output_format": "mp4", "cookiefile": tmp_ck.name,
                "concurrent_fragment_downloads": 8,
            }
            loop = asyncio.get_event_loop()
            def _dl_ytdlp():
                with yt_dlp.YoutubeDL(opts_dy) as ydl:
                    info2 = ydl.extract_info(real_url, download=True)
                    raw = ydl.prepare_filename(info2)
                    for ext in (".mp4", ".webm", ".mkv", ".mov"):
                        c2 = Path(raw).with_suffix(ext)
                        if c2.exists(): return c2.name, str(out_dir)
                    return Path(raw).name, str(out_dir)
            try:
                fname, saved_dir = await asyncio.wait_for(loop.run_in_executor(executor, _dl_ytdlp), timeout=90)
                fpath = Path(saved_dir) / fname
                if fpath.exists() and fpath.stat().st_size > 50000:
                    try: Path(tmp_ck.name).unlink()
                    except: pass
                    return JSONResponse({"success": True, "filename": fname, "saved_dir": saved_dir,
                                         "download_url": None, "size_mb": round(fpath.stat().st_size/1024/1024, 1)})
            except Exception as e:
                print(f"[dy_ytdlp] 失敗：{e}")
            try: Path(tmp_ck.name).unlink()
            except: pass

        try:
            aweme_id = await _resolve_aweme_id(real_url)
            if aweme_id:
                info = await _get_douyin_info_api(aweme_id)
                video_url = info.get("video_url")
                if video_url:
                    use_title = info.get("title") or title
                    safe = re.sub(r'[\\/:*?"<>|]', '_', use_title)[:60]
                    fpath = out_dir / f"{safe}.mp4"
                    cookie_str = _cookies_to_str(cookie_data) if cookie_data else ""
                    dy_h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            "Referer": "https://www.douyin.com/",
                            **({"Cookie": cookie_str} if cookie_str else {})}
                    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as cl:
                        async with cl.stream("GET", video_url, headers=dy_h) as r:
                            r.raise_for_status()
                            with open(fpath, "wb") as f:
                                async for chunk in r.aiter_bytes(512*1024): f.write(chunk)
                    size = fpath.stat().st_size if fpath.exists() else 0
                    if size > 50000:
                        return JSONResponse({"success": True, "filename": fpath.name, "saved_dir": str(out_dir),
                                             "download_url": None, "size_mb": round(size/1024/1024, 1)})
        except Exception as e:
            print(f"[dy_api_dl] 失敗：{e}")

        try:
            cdn_info = await _get_douyin_cdn(real_url)
            cdn = cdn_info.get("cdn_url")
            if not cdn:
                return JSONResponse({"success": False, "error": "無法取得抖音影片，請至後台設定 Cookies 後再試"})
            use_title = cdn_info.get("title") or title
            audio_cdn = cdn_info.get("cdn_audio_url")
            fname, saved_dir = await _download_from_cdn(cdn, out_dir, use_title, audio_cdn)
            fpath = Path(saved_dir) / fname
            size = fpath.stat().st_size if fpath.exists() else 0
            return JSONResponse({"success": True, "filename": fname, "saved_dir": saved_dir,
                                 "download_url": None, "size_mb": round(size/1024/1024, 1)})
        except Exception as ex:
            return JSONResponse({"success": False, "error": f"抖音下載失敗：{ex}"})

    loop = asyncio.get_event_loop()

    if _is_lux_platform(real_url):
        try:
            fname, saved_dir = await loop.run_in_executor(executor, _lux_download, real_url, out_dir)
            fpath = Path(saved_dir) / fname
            size  = fpath.stat().st_size if fpath.exists() else 0
            dl_url = f"/下載影片/{fname}" if Path(saved_dir) == DOWNLOAD_DIR else None
            return JSONResponse({"success": True, "filename": fname, "saved_dir": saved_dir,
                                 "download_url": dl_url, "size_mb": round(size/1024/1024, 1)})
        except Exception as lux_err:
            print(f"[lux] 失敗，fallback yt-dlp：{lux_err}")

    safe  = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
    tmpl  = str(out_dir / f"{safe}.%(ext)s")
    opts  = {"format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
             "outtmpl": tmpl, "quiet": True, "no_warnings": True, "merge_output_format": "mp4",
             "concurrent_fragment_downloads": 8, "updatetime": False,
             "postprocessor_args": {"default": ["-map_metadata", "-1"]}}

    def _dl():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(real_url, download=True)
            raw  = ydl.prepare_filename(info)
            for ext in (".mp4", ".webm", ".mkv", ".mov"):
                c = Path(raw).with_suffix(ext)
                if c.exists():
                    return c.name, str(out_dir)
            return Path(raw).name, str(out_dir)

    try:
        fname, saved_dir = await loop.run_in_executor(executor, _dl)
        fpath = Path(saved_dir) / fname
        size  = fpath.stat().st_size if fpath.exists() else 0
        dl_url = f"/下載影片/{fname}" if Path(saved_dir) == DOWNLOAD_DIR else None
        return JSONResponse({"success": True, "filename": fname, "saved_dir": saved_dir,
                             "download_url": dl_url, "size_mb": round(size/1024/1024, 1)})
    except Exception as ex:
        return JSONResponse({"success": False, "error": str(ex)})

# ── 已下載清單 ────────────────────────────────────────────
@app.get("/api/downloads")
def list_downloads(device_id: str = ""):
    all_files = [{"name": f.name, "size_mb": round(f.stat().st_size/1024/1024,1), "url": f"/下載影片/{f.name}"}
                 for f in DOWNLOAD_DIR.iterdir() if f.suffix.lower() in (".mp4",".webm",".mkv",".mov")]
    if device_id:
        allowed = _registry_get_files(device_id)
        files = [f for f in all_files if f["name"] in allowed]
    else:
        files = all_files
    return JSONResponse(sorted(files, key=lambda x: x["name"]))

@app.get("/api/douyin-cdn")
async def douyin_cdn(aweme_id: str):
    url = f"https://www.douyin.com/video/{aweme_id}"
    info = await _get_douyin_cdn(url)
    cdn = info.get("cdn_url") or ""
    return JSONResponse({"cdn_url": cdn, "ok": bool(cdn)})

@app.get("/api/proxy-video")
async def proxy_video(request: Request, url: str, referer: str = ""):
    from fastapi.responses import StreamingResponse
    from urllib.parse import unquote
    target = unquote(url)
    if not target.startswith("http"):
        return JSONResponse({"error": "invalid url"}, status_code=400)
    if not referer:
        m = re.match(r'(https?://[^/]+)', unquote(referer) if referer else target)
        referer = m.group(1) if m else "https://www.douyin.com/"
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Referer": unquote(referer),
        "Accept": "*/*",
    }
    range_hdr = request.headers.get("range")
    if range_hdr:
        req_headers["Range"] = range_hdr

    client = httpx.AsyncClient(timeout=120, follow_redirects=True)
    req2 = client.build_request("GET", target, headers=req_headers)
    resp = await client.send(req2, stream=True)
    ct = resp.headers.get("content-type", "video/mp4")

    resp_headers: dict = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if "content-range" in resp.headers:
        resp_headers["Content-Range"] = resp.headers["content-range"]
    if "content-length" in resp.headers:
        resp_headers["Content-Length"] = resp.headers["content-length"]

    async def _stream():
        try:
            async for chunk in resp.aiter_bytes(512 * 1024):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(_stream(), status_code=resp.status_code,
                             media_type=ct, headers=resp_headers)

@app.get("/api/serve-file")
async def serve_file(filename: str = "", path: str = "", cleanup: bool = False, inline: bool = False):
    from urllib.parse import quote as _uq
    from starlette.background import BackgroundTask
    if filename:
        fpath = DOWNLOAD_DIR / filename
    else:
        fpath = Path(path)
        try:
            fpath.resolve().relative_to(DOWNLOAD_DIR.resolve())
        except ValueError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
    if not fpath.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    encoded_name = _uq(fpath.name, safe="")
    ext = fpath.suffix.lower()
    mime = {"mp4":"video/mp4","mkv":"video/x-matroska","webm":"video/webm","m4v":"video/mp4"}.get(ext.lstrip("."), "application/octet-stream")
    bg = None
    if cleanup:
        def _rm():
            try:
                if fpath.exists(): fpath.unlink()
            except Exception: pass
        bg = BackgroundTask(_rm)
    hdrs = {} if inline else {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"}
    return FileResponse(str(fpath), media_type=mime, headers=hdrs, background=bg)

@app.get("/api/dl-stream")
async def dl_stream(request: Request, url: str, title: str = "影片", referer: str = ""):
    from fastapi.responses import StreamingResponse
    from urllib.parse import quote as _uq, unquote as _uuq
    if not url.startswith("http"):
        return JSONResponse({"error": "invalid url"}, status_code=400)
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', title)[:80]
    if not re.search(r'\.\w{2,4}$', safe_name):
        safe_name += '.mp4'
    encoded = _uq(safe_name, safe="")
    origin_ref = _uuq(referer) if referer else re.match(r'(https?://[^/]+)', url)
    if hasattr(origin_ref, 'group'):
        origin_ref = origin_ref.group(1)
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Referer": origin_ref or url,
        "Accept": "*/*",
    }
    range_hdr = request.headers.get("range")
    if range_hdr:
        req_headers["Range"] = range_hdr

    client = httpx.AsyncClient(timeout=120, follow_redirects=True)
    req2 = client.build_request("GET", url, headers=req_headers)
    resp = await client.send(req2, stream=True)
    ct = resp.headers.get("content-type", "video/mp4")
    if "text" in ct or "html" in ct:
        await resp.aclose(); await client.aclose()
        return JSONResponse({"error": "cdn returned non-video response"}, status_code=502)

    resp_headers: dict = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }
    for hdr in ("content-length", "content-range"):
        if hdr in resp.headers:
            resp_headers[hdr.title()] = resp.headers[hdr]

    async def _stream():
        try:
            async for chunk in resp.aiter_bytes(512 * 1024):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(_stream(), status_code=resp.status_code,
                             media_type=ct, headers=resp_headers)

@app.get("/api/pick-folder")
def pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        folder = filedialog.askdirectory(
            parent=root,
            title="選擇影片下載資料夾",
            initialdir=str(DOWNLOAD_DIR),
        )
        root.destroy()
        if folder:
            return JSONResponse({"path": str(Path(folder))})
        return JSONResponse({"path": ""})
    except Exception as e:
        return JSONResponse({"path": "", "error": str(e)})

@app.get("/api/download-progress")
async def download_progress_sse(request: Request, url: str, title: str = "影片",
                                save_path: str = "", cdn_url: str = "", cdn_audio_url: str = "",
                                quality: str = "best", device_id: str = ""):
    real_url = await resolve_short_url(url)
    if save_path and Path(save_path).is_absolute():
        try:
            out_dir = Path(save_path)
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            out_dir = DOWNLOAD_DIR
    else:
        out_dir = DOWNLOAD_DIR

    async def event_gen():
        try:
            async for evt in _dl_progress(real_url, title, out_dir,
                                           hint_cdn=cdn_url, hint_audio=cdn_audio_url,
                                           quality=quality):
                if await request.is_disconnected():
                    return
                if evt.get("type") == "done" and device_id and evt.get("filename"):
                    _registry_add(evt["filename"], device_id)
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as ex:
            yield f"data: {json.dumps({'type':'error','message':str(ex)}, ensure_ascii=False)}\n\n"

    from fastapi.responses import StreamingResponse as _SR
    return _SR(event_gen(), media_type="text/event-stream",
               headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                        "Connection": "keep-alive"})

async def _dl_progress(real_url: str, title: str, out_dir: Path,
                       hint_cdn: str = "", hint_audio: str = "", quality: str = "best"):
    loop = asyncio.get_running_loop()

    async def httpx_dl(url, fpath, headers, s=10, e=95, workers=4):
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as probe:
                hr = await probe.head(url, headers=headers)
                total = int(hr.headers.get("content-length", 0))
                supports_range = hr.headers.get("accept-ranges", "").lower() == "bytes"
        except Exception:
            total, supports_range = 0, False

        if not supports_range or total < 4*1024*1024:
            async with httpx.AsyncClient(timeout=180, follow_redirects=True) as cl:
                async with cl.stream("GET", url, headers=headers) as r:
                    r.raise_for_status()
                    tot = int(r.headers.get("content-length", 0)) or total
                    done = 0
                    with open(fpath, "wb") as f:
                        async for chunk in r.aiter_bytes(512*1024):
                            f.write(chunk); done += len(chunk)
                            pct = s+int((done/tot)*(e-s)) if tot else (s+e)//2
                            yield {"type":"progress","pct":min(pct,e),
                                   "msg":f"下載中 {done//1048576}MB{'/{:.0f}MB'.format(tot/1048576) if tot else ''}"}
            return

        workers = min(workers, 8)
        chunk = total // workers
        ranges = [(i*chunk, (i+1)*chunk-1 if i<workers-1 else total-1) for i in range(workers)]
        tmps = [fpath.parent/f".tmp_{fpath.stem}_{i}{fpath.suffix}" for i in range(workers)]
        done_arr = [0]*workers
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        last_pct = [s]

        async def dl_chunk(idx, start, end, tmp):
            try:
                hdrs = {**headers, "Range": f"bytes={start}-{end}"}
                async with httpx.AsyncClient(timeout=180, follow_redirects=True) as cl:
                    async with cl.stream("GET", url, headers=hdrs) as r:
                        with open(tmp, "wb") as f:
                            async for c in r.aiter_bytes(512*1024):
                                f.write(c); done_arr[idx] += len(c)
                                td = sum(done_arr)
                                pct = s+int(td/total*(e-s))
                                if pct > last_pct[0]:
                                    last_pct[0] = pct
                                    await q.put({"type":"progress","pct":min(pct,e),
                                                 "msg":f"下載中 {td//1048576}MB/{total//1048576}MB ({workers}線程並行)"})
            finally:
                await q.put(None)

        tasks = [asyncio.create_task(dl_chunk(i, r[0], r[1], tmps[i])) for i, r in enumerate(ranges)]
        finished = 0
        while finished < workers:
            evt = await asyncio.wait_for(q.get(), timeout=120)
            if evt is None: finished += 1
            else: yield evt
        await asyncio.gather(*tasks, return_exceptions=True)
        with open(fpath, "wb") as out:
            for tmp in tmps:
                if tmp.exists():
                    with open(tmp, "rb") as inp: out.write(inp.read())
                    tmp.unlink(missing_ok=True)
        yield {"type":"progress","pct":e,"msg":"組合完成"}

    async def ytdlp_dl(opts, url, res_list, err_list):
        q = asyncio.Queue()
        def hook(d):
            if d['status'] == 'downloading':
                dl = d.get('downloaded_bytes') or 0
                tot = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                pct = max(5, min(95, int(dl/tot*90)+5)) if tot else 50
                asyncio.run_coroutine_threadsafe(
                    q.put({"type":"progress","pct":pct,"msg":f"下載中 {d.get('_percent_str','').strip()}"}), loop)
            elif d['status'] == 'finished':
                asyncio.run_coroutine_threadsafe(q.put({"type":"progress","pct":99,"msg":"合併格式..."}), loop)
        def run():
            try:
                with yt_dlp.YoutubeDL({**opts,"progress_hooks":[hook]}) as ydl:
                    info = ydl.extract_info(url, download=True)
                    raw = ydl.prepare_filename(info)
                    for ext in (".mp4",".webm",".mkv",".mov"):
                        c = Path(raw).with_suffix(ext)
                        if c.exists(): res_list.append(c); return
                    res_list.append(Path(raw))
            except Exception as ex: err_list.append(str(ex))
            finally: asyncio.run_coroutine_threadsafe(q.put(None), loop)
        loop.run_in_executor(executor, run)
        while True:
            try: item = await asyncio.wait_for(q.get(), timeout=180)
            except asyncio.TimeoutError: err_list.append("下載超時"); break
            if item is None: break
            yield item

    def ffmerge(video, audio, out):
        subprocess.run([FFMPEG_BIN,"-y","-i",str(video),"-i",str(audio),"-c","copy",str(out)],
                       capture_output=True, timeout=120)
        video.unlink(missing_ok=True); audio.unlink(missing_ok=True)

    DY_HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                  "Referer":"https://www.douyin.com/"}

    # ══ 抖音 ══════════════════════════════════════════════════════
    if _is_douyin(real_url):
        import tempfile as _tf
        cookie_data = _load_platform_cookies().get("douyin", [])

        # ── 優先：快速 API（若預覽沒帶 hint_cdn，重新呼叫快速 API）──
        if not hint_cdn:
            yield {"type":"progress","pct":3,"msg":"快速解析抖音..."}
            fast_dy = await _get_douyin_fast(real_url)
            if fast_dy.get("cdn_url"):
                hint_cdn = fast_dy["cdn_url"]
                if not title or title == "影片":
                    title = fast_dy.get("title", title)

        if hint_cdn:
            yield {"type":"progress","pct":5,"msg":f"下載中（{quality if quality != 'best' else '最高畫質'}）..."}
            safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
            if hint_audio:
                vt = out_dir/f"{safe}_v.mp4"; at = out_dir/f"{safe}_a.m4a"; final = out_dir/f"{safe}.mp4"
                yield {"type":"progress","pct":10,"msg":"下載影片軌..."}
                async for evt in httpx_dl(hint_cdn, vt, DY_HEADERS, 10, 58): yield evt
                yield {"type":"progress","pct":60,"msg":"下載音訊軌..."}
                async for evt in httpx_dl(hint_audio, at, DY_HEADERS, 60, 83): yield evt
                yield {"type":"progress","pct":86,"msg":"合併音訊..."}
                ffmerge(vt, at, final)
                if not final.exists():
                    async for evt in httpx_dl(hint_cdn, final, DY_HEADERS, 86, 98): yield evt
            else:
                final = out_dir/f"{safe}.mp4"
                async for evt in httpx_dl(hint_cdn, final, DY_HEADERS, 5, 95): yield evt
            sz = final.stat().st_size if final.exists() else 0
            if sz > 50000:
                yield {"type":"done","filename":final.name,"saved_dir":str(out_dir),"size_mb":round(sz/1024/1024,1)}
                return
            yield {"type":"progress","pct":5,"msg":"快取 URL 已過期，重新抓取..."}

        if cookie_data:
            yield {"type":"progress","pct":2,"msg":"初始化 yt-dlp..."}
            tmp_ck = _tf.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
            _cookies_to_netscape(cookie_data, tmp_ck.name); tmp_ck.close()
            safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
            opts_dy = {"format":"bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
                       "outtmpl":str(out_dir/f"{safe}.%(ext)s"),"quiet":True,"no_warnings":True,
                       "merge_output_format":"mp4","cookiefile":tmp_ck.name,
                       "concurrent_fragment_downloads":8}
            res1, err1 = [], []
            async for evt in ytdlp_dl(opts_dy, real_url, res1, err1): yield evt
            try: Path(tmp_ck.name).unlink()
            except: pass
            if res1 and Path(res1[0]).exists() and Path(res1[0]).stat().st_size > 50000:
                sz = round(Path(res1[0]).stat().st_size/1024/1024, 1)
                yield {"type":"done","filename":Path(res1[0]).name,"saved_dir":str(out_dir),"size_mb":sz}
                return

        yield {"type":"progress","pct":5,"msg":"嘗試 API 取得影片..."}
        try:
            aweme_id = await _resolve_aweme_id(real_url)
            if aweme_id:
                info = await _get_douyin_info_api(aweme_id)
                vurl = info.get("video_url")
                if vurl:
                    use_title = info.get("title") or title
                    safe = re.sub(r'[\\/:*?"<>|]', '_', use_title)[:60]
                    fpath = out_dir / f"{safe}.mp4"
                    ck_str = _cookies_to_str(cookie_data) if cookie_data else ""
                    hdrs = {**DY_HEADERS, **({"Cookie":ck_str} if ck_str else {})}
                    async for evt in httpx_dl(vurl, fpath, hdrs, 10, 95): yield evt
                    if fpath.exists() and fpath.stat().st_size > 50000:
                        yield {"type":"done","filename":fpath.name,"saved_dir":str(out_dir),"size_mb":round(fpath.stat().st_size/1024/1024,1)}
                        return
        except Exception as e: print(f"[dy_api_dl] {e}")



    # ══ 快手 ══════════════════════════════════════════════════════
    if _is_kuaishou(real_url):
        yield {"type":"progress","pct":5,"msg":"解析快手影片..."}
        ks_h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer":"https://www.kuaishou.com/"}

        # 優先：hint_cdn（預覽時已取得）
        if hint_cdn:
            safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
            fpath = out_dir / f"{safe}.mp4"
            yield {"type":"progress","pct":10,"msg":"下載快手影片..."}
            async for evt in httpx_dl(hint_cdn, fpath, ks_h, 10, 95): yield evt
            if fpath.exists() and fpath.stat().st_size > 50000:
                yield {"type":"done","filename":fpath.name,"saved_dir":str(out_dir),"size_mb":round(fpath.stat().st_size/1024/1024,1)}
                return

        # 直接打 Kuaishou GraphQL API（雲端可用）
        yield {"type":"progress","pct":8,"msg":"嘗試直連快手 API..."}
        ks_direct = await _get_kuaishou_direct(real_url)
        cdn_direct = ks_direct.get("cdn_url","") or ""
        if cdn_direct:
            safe_d = re.sub(r'[\\/:*?"<>|]', '_', ks_direct.get("title",title))[:60]
            fpath_d = out_dir / f"{safe_d}.mp4"
            async for evt in httpx_dl(cdn_direct, fpath_d, ks_h, 10, 90): yield evt
            if fpath_d.exists() and fpath_d.stat().st_size > 50000:
                yield {"type":"done","filename":fpath_d.name,"saved_dir":str(out_dir),"size_mb":round(fpath_d.stat().st_size/1024/1024,1)}
                return

        # yt-dlp（雲端可用）
        yield {"type":"progress","pct":8,"msg":"嘗試 yt-dlp 解析快手..."}
        res_ks, err_ks = [], []
        safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
        opts_ks = {"format":"best[ext=mp4]/best","outtmpl":str(out_dir/f"{safe}.%(ext)s"),
                   "quiet":True,"no_warnings":True,"merge_output_format":"mp4"}
        async for evt in ytdlp_dl(opts_ks, real_url, res_ks, err_ks): yield evt
        if res_ks and Path(res_ks[0]).exists() and Path(res_ks[0]).stat().st_size > 50000:
            yield {"type":"done","filename":Path(res_ks[0]).name,"saved_dir":str(out_dir),"size_mb":round(Path(res_ks[0]).stat().st_size/1024/1024,1)}
            return

        # fallback：Playwright（本機）
        yield {"type":"progress","pct":5,"msg":"啟動瀏覽器解析快手..."}
        try:
            ks_info = await _get_kuaishou_cdn(real_url)
            cdn = ks_info.get("cdn_url") or ""
            use_title = ks_info.get("title") or title
            if not cdn:
                yield {"type":"error","message":"無法取得快手影片（雲端限制，建議貼入快手 Cookies）"}
                return
            safe2 = re.sub(r'[\\/:*?"<>|]', '_', use_title)[:60]
            fpath2 = out_dir / f"{safe2}.mp4"
            async for evt in httpx_dl(cdn, fpath2, ks_h, 10, 95): yield evt
            sz = fpath2.stat().st_size if fpath2.exists() else 0
            if sz > 50000:
                yield {"type":"done","filename":fpath2.name,"saved_dir":str(out_dir),"size_mb":round(sz/1024/1024,1)}
                return
            yield {"type":"error","message":"快手下載失敗，請重新解析"}
        except Exception as ex:
            yield {"type":"error","message":f"快手下載失敗：{ex}"}
        return

    # ══ 蝦皮短影音 ══════════════════════════════════════════════
    if _is_shopee_url(real_url):
        yield {"type": "progress", "pct": 5, "msg": "解析蝦皮影片（重新取 CDN）..."}
        try:
            use_title = title
            shopee_info = await _get_shopee_video_info(real_url)
            vurl = shopee_info.get("video_url") or hint_cdn
            use_title = shopee_info.get("title") or title
            if not vurl:
                yield {"type": "error", "message": "無法解析蝦皮影片網址，請確認連結是否為短影音分享連結"}
                return
            safe = re.sub(r'[\\/:*?"<>|]', '_', use_title)[:60]
            fpath = out_dir / f"{safe}.mp4"
            sp_h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Referer": "https://shopee.tw/"}
            yield {"type": "progress", "pct": 10, "msg": "下載蝦皮影片..."}
            async for evt in httpx_dl(vurl, fpath, sp_h, 10, 95): yield evt
            sz = fpath.stat().st_size if fpath.exists() else 0
            if sz > 50000:
                yield {"type": "done", "filename": fpath.name, "saved_dir": str(out_dir),
                       "size_mb": round(sz / 1024 / 1024, 1)}
                return
            yield {"type": "error", "message": "下載失敗，CDN 連結可能已過期，請重新貼上連結"}
        except Exception as ex:
            yield {"type": "error", "message": f"蝦皮下載失敗：{ex}"}
        return

    # ══ B站：直打 API 下載（雲端可用，不靠 yt-dlp/Lux）═══════════
    if "bilibili.com" in real_url or "b23.tv" in real_url:
        yield {"type":"progress","pct":5,"msg":"解析 B站影片..."}
        bili_h = {**_BILI_HEADERS}
        cdn_b = hint_cdn
        use_title = title
        if not cdn_b:
            bili = await _get_bilibili_direct(real_url)
            cdn_b     = bili.get("cdn_url","")
            use_title = bili.get("title") or title
        if cdn_b:
            yield {"type":"progress","pct":10,"msg":"下載 B站影片..."}
            safe_b = re.sub(r'[\\/:*?"<>|]', '_', use_title)[:60]
            fpath_b = out_dir / f"{safe_b}.mp4"
            async for evt in httpx_dl(cdn_b, fpath_b, bili_h, 10, 95): yield evt
            sz_b = fpath_b.stat().st_size if fpath_b.exists() else 0
            if sz_b > 50000:
                yield {"type":"done","filename":fpath_b.name,"saved_dir":str(out_dir),"size_mb":round(sz_b/1024/1024,1)}
                return
        yield {"type":"error","message":"B站下載失敗（影片可能需要登入或地區限制）"}
        return

    # ══ 小紅書：直解 HTML 下載 ══════════════════════════════════
    if "xiaohongshu.com" in real_url or "xhslink.com" in real_url:
        yield {"type":"progress","pct":5,"msg":"解析小紅書影片..."}
        cdn_x = hint_cdn
        use_title_x = title
        if not cdn_x:
            xhs = await _get_xhs_direct(real_url)
            cdn_x = xhs.get("cdn_url","")
            use_title_x = xhs.get("title") or title
        if cdn_x:
            yield {"type":"progress","pct":10,"msg":"下載小紅書影片..."}
            safe_x = re.sub(r'[\\/:*?"<>|]', '_', use_title_x)[:60]
            fpath_x = out_dir / f"{safe_x}.mp4"
            xhs_h = {"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                     "Referer":"https://www.xiaohongshu.com/"}
            async for evt in httpx_dl(cdn_x, fpath_x, xhs_h, 10, 95): yield evt
            sz_x = fpath_x.stat().st_size if fpath_x.exists() else 0
            if sz_x > 50000:
                yield {"type":"done","filename":fpath_x.name,"saved_dir":str(out_dir),"size_mb":round(sz_x/1024/1024,1)}
                return
        # fallback yt-dlp
        yield {"type":"progress","pct":5,"msg":"改用 yt-dlp 解析小紅書..."}
        safe_x2 = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
        opts_x = {"format":"best[ext=mp4]/best","outtmpl":str(out_dir/f"{safe_x2}.%(ext)s"),
                  "quiet":True,"no_warnings":True,"merge_output_format":"mp4"}
        res_x, err_x = [], []
        async for evt in ytdlp_dl(opts_x, real_url, res_x, err_x): yield evt
        if res_x and Path(res_x[0]).exists() and Path(res_x[0]).stat().st_size > 50000:
            yield {"type":"done","filename":Path(res_x[0]).name,"saved_dir":str(out_dir),"size_mb":round(Path(res_x[0]).stat().st_size/1024/1024,1)}
        else:
            yield {"type":"error","message":"小紅書下載失敗，請確認連結有效"}
        return

    # ══ B站 / Lux（本機 Windows 備用）═══════════════════════════
    if _is_lux_platform(real_url):
        yield {"type":"progress","pct":5,"msg":"啟動 Lux..."}
        before = set(out_dir.glob("*"))
        lux_done, lux_err = [], []
        lux_fmt_args = ["-f", quality] if quality and quality not in ("best", "h1080", "h720", "h480", "h360") else []
        def _lux_run():
            try:
                cmd = [str(LUX_PATH),"-o",str(out_dir)] + lux_fmt_args + [real_url]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                lux_done.append(r)
            except Exception as ex: lux_err.append(str(ex))
        fut = loop.run_in_executor(executor, _lux_run)
        pct = 10
        wait_count = 0
        while not fut.done():
            await asyncio.sleep(2)
            wait_count += 1
            if pct < 60:
                pct = min(60, pct + 8)
            elif pct < 85:
                pct = min(85, pct + 3)
            elif pct < 93:
                pct = min(93, pct + 1)
            msg = "Lux 下載中..." if wait_count < 90 else "Lux 合併中（大檔案需較長時間）..."
            yield {"type":"progress","pct":pct,"msg":msg}
        if lux_err:
            yield {"type":"error","message":f"Lux 失敗：{lux_err[0]}"}; return
        after = set(out_dir.glob("*"))
        new_files = [f for f in (after-before) if f.suffix.lower() in (".mp4",".mkv",".flv",".webm",".m4v")]
        if not new_files:
            vids = sorted([f for f in out_dir.iterdir() if f.suffix.lower() in (".mp4",".mkv",".flv",".webm")],
                          key=lambda x: x.stat().st_mtime, reverse=True)
            if not vids: yield {"type":"error","message":"Lux 下載失敗，無輸出檔案"}; return
            new_files = [vids[0]]
        yield {"type":"done","filename":new_files[0].name,"saved_dir":str(out_dir),"size_mb":round(new_files[0].stat().st_size/1024/1024,1)}
        return

    # ══ 通用快速路徑：有 hint_cdn 時直接 httpx 下載 ══
    if hint_cdn:
        yield {"type":"progress","pct":5,"msg":"下載影片..."}
        safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
        gen_h = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": re.sub(r'(https?://[^/]+).*', r'\1', real_url) or "https://www.google.com/",
        }
        if hint_audio:
            vt = out_dir/f"{safe}_v.mp4"; at = out_dir/f"{safe}_a.m4a"; final = out_dir/f"{safe}.mp4"
            yield {"type":"progress","pct":8,"msg":"下載影片軌..."}
            async for evt in httpx_dl(hint_cdn, vt, gen_h, 8, 55): yield evt
            yield {"type":"progress","pct":57,"msg":"下載音訊軌..."}
            async for evt in httpx_dl(hint_audio, at, gen_h, 57, 83): yield evt
            yield {"type":"progress","pct":85,"msg":"合併音訊..."}
            ffmerge(vt, at, final)
            if not final.exists():
                async for evt in httpx_dl(hint_cdn, final, gen_h, 85, 98): yield evt
        else:
            final = out_dir/f"{safe}.mp4"
            async for evt in httpx_dl(hint_cdn, final, gen_h, 5, 95): yield evt
        sz = final.stat().st_size if final.exists() else 0
        if sz > 50000:
            yield {"type":"done","filename":final.name,"saved_dir":str(out_dir),"size_mb":round(sz/1024/1024,1)}
            return
        yield {"type":"progress","pct":2,"msg":"CDN URL 已過期，改用 yt-dlp 重新下載..."}

    # ══ YouTube：Invidious 優先（繞過雲端封鎖）══════════════════
    if "youtube.com" in real_url or "youtu.be" in real_url:
        yield {"type":"progress","pct":5,"msg":"透過 Invidious 取得 YouTube 影片..."}
        inv = await _get_youtube_via_invidious(real_url)
        if inv.get("cdn_url"):
            yield {"type":"progress","pct":10,"msg":"開始下載 YouTube 影片..."}
            safe_yt = re.sub(r'[\\/:*?"<>|]', '_', inv.get("title") or title)[:60]
            fpath_yt = out_dir / f"{safe_yt}.mp4"
            yt_h = {"User-Agent":"Mozilla/5.0","Referer":"https://www.youtube.com/"}
            async for evt in httpx_dl(inv["cdn_url"], fpath_yt, yt_h, 10, 95): yield evt
            sz_yt = fpath_yt.stat().st_size if fpath_yt.exists() else 0
            if sz_yt > 50000:
                yield {"type":"done","filename":fpath_yt.name,"saved_dir":str(out_dir),"size_mb":round(sz_yt/1024/1024,1)}
                return
        # Invidious 失敗 → yt-dlp Android 備用
        yield {"type":"progress","pct":5,"msg":"Invidious 失敗，改用 yt-dlp..."}
        safe_yt2 = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
        opts_yt = {"format":"bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                   "outtmpl":str(out_dir/f"{safe_yt2}.%(ext)s"),"quiet":True,"no_warnings":True,
                   "merge_output_format":"mp4","concurrent_fragment_downloads":8,"updatetime":False,
                   "postprocessor_args":{"default":["-map_metadata","-1"]},
                   "extractor_args":{"youtube":{"player_client":["android","android_embedded","web"]}}}
        res_yt, err_yt = [], []
        async for evt in ytdlp_dl(opts_yt, real_url, res_yt, err_yt): yield evt
        if res_yt and Path(res_yt[0]).exists() and Path(res_yt[0]).stat().st_size > 50000:
            yield {"type":"done","filename":Path(res_yt[0]).name,"saved_dir":str(out_dir),"size_mb":round(Path(res_yt[0]).stat().st_size/1024/1024,1)}
        else:
            yield {"type":"error","message":"YouTube 下載失敗（"+(err_yt[0] if err_yt else "未知錯誤")+"）"}
        return

    # ══ 其他平台（yt-dlp）════════════════════════════════════════
    yield {"type":"progress","pct":2,"msg":"初始化下載..."}
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
    _h = re.search(r'h(\d+)', quality)
    if _h:
        _hv = _h.group(1)
        _fmt = f"bestvideo[height<={_hv}][ext=mp4]+bestaudio[ext=m4a]/best[height<={_hv}][ext=mp4]/best[height<={_hv}]"
    else:
        _fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    opts = {"format": _fmt,
            "outtmpl":str(out_dir/f"{safe}.%(ext)s"),"quiet":True,"no_warnings":True,
            "merge_output_format":"mp4","concurrent_fragment_downloads":8,
            "updatetime":False,
            "postprocessor_args":{"default":["-map_metadata","-1"]}}
    res2, err2 = [], []
    async for evt in ytdlp_dl(opts, real_url, res2, err2): yield evt
    if err2: yield {"type":"error","message":err2[0]}; return
    if res2:
        sz = round(Path(res2[0]).stat().st_size/1024/1024,1) if Path(res2[0]).exists() else 0
        yield {"type":"done","filename":Path(res2[0]).name,"saved_dir":str(out_dir),"size_mb":sz}
    else:
        yield {"type":"error","message":"下載失敗，無輸出檔案"}


def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:7790")

if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    port = int(os.environ.get("PORT", 7790))
    uvicorn.run(app, host="0.0.0.0", port=port)

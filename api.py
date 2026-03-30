import re
import traceback
import os
import time

import yt_dlp
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_ydl_opts(extra: dict = None) -> dict:
    cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")

    opts = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        },
    }

    if os.path.exists(cookies_path):
        opts["cookiefile"] = cookies_path

    try:
        import curl_cffi  # noqa
        from yt_dlp.networking.impersonate import ImpersonateTarget
        opts["impersonate"] = ImpersonateTarget("chrome", None, None, None)
    except Exception:
        pass

    if extra:
        opts.update(extra)

    return opts


def _extract_with_retry(url: str, ydl_opts: dict, max_retries: int = 3) -> dict:
    last_error = None
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            msg = str(e)
            if any(code in msg for code in ["403", "429", "503", "network", "timeout"]):
                wait = 2 ** attempt
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            last_error = e
            time.sleep(2 ** attempt)
    raise last_error


def _fmt_duration(seconds: float) -> str:
    if not seconds:
        return "Unknown"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _estimate_filesize(f: dict, duration) -> int | None:
    size = f.get("filesize") or f.get("filesize_approx") or f.get("filesize_raw")
    if size:
        return int(size)
    tbr = f.get("tbr") or f.get("vbr")
    if tbr and duration:
        return int((tbr * 1000 / 8) * duration)
    height = f.get("height", 0)
    if height and duration:
        bitrate_map = {
            2160: 16000, 1440: 8000, 1080: 5000,
            720: 2500, 480: 1200, 360: 700, 240: 400
        }
        br = bitrate_map.get(height)
        if br:
            return int((br * 1000 / 8) * duration)
    return None


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return jsonify({"status": "online", "endpoint": "POST /extract-video"})


@app.route("/extract-video", methods=["POST"])
def extract_video():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "No URL provided"}), 400

    try:
        ydl_opts = _build_ydl_opts()
        info = _extract_with_retry(url, ydl_opts)
        duration = info.get("duration")
        videos = []
        seen_urls: set[str] = set()

        for f in info.get("formats", []):
            if f.get("vcodec") == "none":
                continue
            format_url = f.get("url", "")
            if not format_url or format_url in seen_urls:
                continue
            seen_urls.add(format_url)

            filesize    = _estimate_filesize(f, duration)
            size_str    = f"{filesize / (1024 * 1024):.1f} MB" if filesize else "Unknown"
            height      = f.get("height", 0) or 0
            width       = f.get("width", 0) or 0
            quality     = f"{height}p" if height else (f"{int(width * 9 / 16)}p" if width else "Unknown")

            vcodec = (f.get("vcodec") or "").lower()
            if "av1"  in vcodec:                   codec_info = "AV1"
            elif "h264" in vcodec or "avc" in vcodec: codec_info = "H264"
            elif "vp9"  in vcodec:                 codec_info = "VP9"
            elif "hevc" in vcodec or "h265" in vcodec: codec_info = "H265"
            else: codec_info = vcodec.split(".")[0] if vcodec else "Unknown"

            format_type = "HLS" if ".m3u8" in format_url else ("DASH" if ".mpd" in format_url else "Direct")

            # Only include Direct formats — HLS/DASH can't be directly saved as a file
            if format_type != "Direct":
                continue

            videos.append({
                "url":            format_url,
                "format_id":      f.get("format_id", ""),
                "quality":        quality,
                "resolution":     f"{width}x{height}" if width and height else "Unknown",
                "size":           size_str,
                "filesize_bytes": filesize,
                "duration":       _fmt_duration(duration),
                "codec":          codec_info,
                "ext":            f.get("ext", "mp4"),
                "fps":            f.get("fps") or 0,
                "has_audio":      f.get("acodec") not in (None, "none"),
            })

        # Sort by quality descending, then size descending
        def sort_key(v):
            try:
                q = int(v["quality"].rstrip("p")) if v["quality"].endswith("p") else 0
            except ValueError:
                q = 0
            return (q, -(v["filesize_bytes"] or 0))

        videos.sort(key=sort_key, reverse=True)

        # Deduplicate by quality + codec
        unique, seen_keys = [], set()
        for v in videos:
            key = f"{v['quality']}_{v['codec']}"
            if key not in seen_keys:
                seen_keys.add(key)
                unique.append(v)

        return jsonify({
            "success":             True,
            "title":               info.get("title", "Unknown Title"),
            "url":                 url,
            "thumbnail":           info.get("thumbnail", ""),
            "description":         info.get("description", ""),
            "uploader":            info.get("uploader", ""),
            "duration_seconds":    duration or 0,
            "view_count":          info.get("view_count") or 0,
            "like_count":          info.get("like_count") or 0,
            "downloadable_videos": unique,
        })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        user_msg = msg
        if "403" in msg:
            user_msg = "403 Forbidden — this site blocks automated access."
        elif "404" in msg:
            user_msg = "404 Not Found — video may be removed."
        elif "private" in msg.lower():
            user_msg = "This video is private."
        elif "login" in msg.lower() or "sign in" in msg.lower():
            user_msg = "This video requires login."
        return jsonify({"success": False, "error": user_msg, "downloadable_videos": []}), 500

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "downloadable_videos": []}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
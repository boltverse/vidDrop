import yt_dlp

class YTDLPExtractor:

    @staticmethod
    def extract(url: str) -> dict:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        videos = []

        for f in info.get("formats", []):
            if f.get("vcodec") == "none":
                continue

            format_url = f.get("url")
            ext = f.get("ext")

            format_type = "direct"
            if ".m3u8" in format_url:
                format_type = "hls"
            elif ".mpd" in format_url:
                format_type = "dash"

            videos.append({
                "format_id": f.get("format_id"),
                "quality": f"{f.get('height')}p" if f.get("height") else "unknown",
                "filesize": f.get("filesize"),
                "format_type": format_type,
                "ext": ext
            })

        return {
            "success": True,
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "videos": videos
        }

import os
import time
import yt_dlp
from flask import Flask, request, jsonify
from flask_cors import CORS
from yt_dlp.networking.impersonate import ImpersonateTarget
app = Flask(__name__)
CORS(app)

def get_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'cookiefile': 'cookies.txt',
        'extract_flat': False,
        'skip_download': True,
        'force_generic_extractor': False,

        # ✅ ADD THIS HERE
        'impersonate': ImpersonateTarget("chrome", None, None, None),

        'retries': 5,
        'fragment_retries': 5,
        'socket_timeout': 30,

        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        }
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except Exception as e:
            print(f"Error extracting info: {e}")
            raise


def format_duration(seconds):
    """Convert seconds to MM:SS format"""
    if not seconds:
        return "Unknown"
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes}:{seconds:02d}"


def get_file_size(size_bytes):
    """Convert bytes to human readable format"""
    if not size_bytes:
        return "Unknown"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


@app.route('/extract-video', methods=['POST'])
def extract_video():
    try:
        data = request.get_json()
        url = data.get('url', '').strip()

        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400

        print(f"Extracting video from: {url}")

        # Get video info
        info = get_video_info(url)

        if not info:
            return jsonify({'success': False, 'error': 'Failed to extract video info'}), 500

        # Extract formats
        formats = []
        seen_urls = set()

        for f in info.get('formats', []):
            # Skip audio-only formats
            if f.get('vcodec') == 'none':
                continue

            video_url = f.get('url')
            if not video_url or video_url in seen_urls:
                continue

            seen_urls.add(video_url)

            # Get resolution
            height = f.get('height', 0)
            width = f.get('width', 0)

            if height:
                quality = f"{height}p"
            elif width:
                quality = f"{int(width * 9 / 16)}p"
            else:
                quality = "Unknown"

            # Get file size
            filesize = f.get('filesize') or f.get('filesize_approx')

            # Get codec
            vcodec = f.get('vcodec', '')
            if 'av01' in vcodec:
                codec = 'AV1'
            elif 'vp09' in vcodec:
                codec = 'VP9'
            elif 'avc' in vcodec or 'h264' in vcodec:
                codec = 'H.264'
            elif 'hevc' in vcodec or 'h265' in vcodec:
                codec = 'H.265'
            else:
                codec = 'Unknown'

            formats.append({
                'url': video_url,
                'format_id': f.get('format_id', ''),
                'quality': quality,
                'resolution': f"{width}x{height}" if width and height else "Unknown",
                'size': get_file_size(filesize),
                'filesize_bytes': filesize,
                'duration': format_duration(info.get('duration')),
                'codec': codec,
                'ext': f.get('ext', 'mp4'),
                'fps': f.get('fps', 0),
                'has_audio': f.get('acodec') not in (None, 'none'),
                'format_note': f.get('format_note', ''),
            })

        # Sort by quality (highest first)
        formats.sort(key=lambda x: int(x['quality'].rstrip('p')) if x['quality'].rstrip('p').isdigit() else 0,
                     reverse=True)

        # Remove duplicates (keep highest quality for each resolution)
        unique_formats = []
        seen_qualities = set()

        for f in formats:
            if f['quality'] not in seen_qualities:
                seen_qualities.add(f['quality'])
                unique_formats.append(f)

        print(f"Found {len(unique_formats)} unique formats")

        response_data = {
            'success': True,
            'title': info.get('title', 'Unknown'),
            'url': url,
            'thumbnail': info.get('thumbnail', ''),
            'description': info.get('description', ''),
            'uploader': info.get('uploader', ''),
            'duration_seconds': info.get('duration', 0),
            'view_count': info.get('view_count', 0),
            'like_count': info.get('like_count', 0),
            'downloadable_videos': unique_formats
        }

        return jsonify(response_data)

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        print(f"DownloadError: {error_msg}")

        # Provide user-friendly error messages
        if "Video unavailable" in error_msg:
            error_msg = "This video is unavailable or private"
        elif "age" in error_msg.lower():
            error_msg = "This video is age-restricted and requires login"
        elif "copyright" in error_msg.lower():
            error_msg = "This video is blocked due to copyright"
        elif "geo" in error_msg.lower():
            error_msg = "This video is not available in your region"

        return jsonify({
            'success': False,
            'error': error_msg,
            'downloadable_videos': []
        }), 500

    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()

        return jsonify({
            'success': False,
            'error': str(e),
            'downloadable_videos': []
        }), 500


@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'status': 'online',
        'message': 'Video Downloader API is running',
        'endpoint': 'POST /extract-video'
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
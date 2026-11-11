# 大肥鱼辅助服务，两件事：
#   1. 18765 端口：把提前下好的表情包压缩包交给 meme_manager（路径以 /<repo>/archive/<ref>.zip 结尾）
#   2. 8138 端口：AstrBot 和 Breeze 之间的语音中转。按 voice-settings.json 换成选中的声音，
#      再用 ffmpeg atempo 调语速（不变调）。设置每次请求现读，改了马上生效，不用重启。
import glob
import http.server
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
MEMES = os.path.join(HERE, "memes")
SETTINGS = os.path.join(HERE, "voice-settings.json")
BREEZE_HOST = "127.0.0.1"
BREEZE_PORT = 8137
BREEZE = f"http://{BREEZE_HOST}:{BREEZE_PORT}"
RATE = 24000
NO_WINDOW = 0x08000000
# 文字合成请求很小；中转上传和返回的音频也按一次语音的体量封顶，避免把内存吃光。
MAX_SPEECH_BODY = 1 << 20
MAX_PROXY_BODY = 64 << 20
MAX_AUDIO_BYTES = 64 << 20
# ffmpeg atempo 合法范围更宽，这里收到控制台实际使用的区间里。
MIN_SPEED, MAX_SPEED = 0.5, 2.0
AUDIO_TYPES = {
    "pcm": "audio/pcm",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
}
# 只认表情包仓库名和 tag，挡掉 ..、反斜杠、盘符这类路径。
MEME_PATH = re.compile(
    r"/([A-Za-z0-9._-]+)/archive/(?:refs/tags/)?([A-Za-z0-9._-]+)\.zip$"
)
CONTENT_TYPE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """上游若 302 到别的地址，原样把这次响应交回去，不再跟着跳。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    pattern = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe")
    return next(iter(glob.glob(pattern)), None)


FFMPEG = find_ffmpeg()


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def breeze_url(path):
    """只允许转到本机 Breeze。请求行里的 @、绝对地址、反斜杠都不能把主机换掉。"""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValueError("bad path")
    if any(c in path for c in "\\@ \t") or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError("bad path")
    parts = urlsplit(BREEZE + path)
    if (parts.scheme != "http" or parts.hostname != BREEZE_HOST or parts.port != BREEZE_PORT
            or parts.username or parts.password):
        raise ValueError("bad path")
    return BREEZE + path


def meme_zip(path):
    """表情包只从 memes 目录里取 <仓库名>-<tag>.zip，解析后的真实路径必须还在这个目录下。"""
    match = MEME_PATH.search(path.split("?", 1)[0].split("#", 1)[0])
    if not match:
        return None
    root = os.path.realpath(MEMES)
    candidate = os.path.realpath(os.path.join(root, f"{match.group(1)}-{match.group(2)}.zip"))
    try:
        inside = os.path.commonpath([root, candidate]) == root
    except ValueError:
        return None
    if not inside or not os.path.isfile(candidate):
        return None
    return candidate


def parse_speed(value):
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(speed):
        return None
    return min(MAX_SPEED, max(MIN_SPEED, speed))


def safe_content_type(value, default="application/octet-stream"):
    if not isinstance(value, str) or not value.strip():
        return default
    main = value.split(";", 1)[0].strip().lower()
    return main if CONTENT_TYPE.match(main) else default


def read_capped(resp, limit):
    data = resp.read(limit + 1)
    if len(data) > limit:
        return None
    return data


def wav_header(n_bytes):
    return (b"RIFF" + struct.pack("<I", 36 + n_bytes) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16) + b"data" + struct.pack("<I", n_bytes))


def ffmpeg_pcm(pcm, speed, out_args):
    """24k 单声道 s16 PCM 进，按 out_args 出；speed != 1 时用 atempo 变速不变调。"""
    if not FFMPEG:
        raise RuntimeError("ffmpeg not found")
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0"]
    if abs(speed - 1.0) > 0.01:
        args += ["-filter:a", f"atempo={speed:.2f}"]
    run = {"input": pcm, "capture_output": True}
    if os.name == "nt":
        run["creationflags"] = NO_WINDOW
    r = subprocess.run(args + out_args + ["pipe:1"], **run)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[:300] + "\n")
        raise RuntimeError("ffmpeg failed")
    if len(r.stdout) > MAX_AUDIO_BYTES:
        raise RuntimeError("audio too large")
    return r.stdout


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "dafeiyu-helper"
    sys_version = ""

    def reply(self, code, body, ctype):
        if not isinstance(body, bytes):
            body = b""
        self.send_response(code)
        self.send_header("Content-Type", safe_content_type(ctype))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, message):
        self.reply(code, json.dumps({"error": message}).encode(), "application/json")

    def read_body(self, limit):
        raw = self.headers.get("Content-Length")
        if raw is None or str(raw).strip() == "":
            return b""
        try:
            n = int(str(raw).strip())
        except ValueError:
            self.fail(400, "bad content-length")
            return None
        if n < 0 or n > limit:
            self.fail(413, "body too large")
            return None
        return self.rfile.read(n) if n else b""


class MemeHandler(_Handler):
    def do_GET(self):
        path = meme_zip(self.path)
        if not path:
            self.send_error(404)
            return
        try:
            size = os.path.getsize(path)
            fh = open(path, "rb")
        except OSError:
            self.send_error(404)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            while chunk := fh.read(1024 * 1024):
                self.wfile.write(chunk)
        finally:
            fh.close()


class VoiceHandler(_Handler):
    def passthrough(self, method):
        try:
            url = breeze_url(self.path)
        except ValueError:
            self.fail(400, "bad path")
            return
        body = self.read_body(MAX_PROXY_BODY)
        if body is None:
            return
        req = urllib.request.Request(url, body if body else None, method=method)
        ctype = safe_content_type(self.headers.get("Content-Type"), default="")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with OPENER.open(req, timeout=600) as r:
                data = read_capped(r, MAX_AUDIO_BYTES)
                status = r.status
                out_type = safe_content_type(r.headers.get("Content-Type"))
        except urllib.error.HTTPError as e:
            # 不把 302 原样交回去：调用方自己还会再跳一次。
            if 300 <= e.code < 400:
                self.fail(502, "redirect blocked")
                return
            data = read_capped(e, MAX_AUDIO_BYTES)
            status = e.code
            out_type = "application/json"
        except Exception:
            self.fail(502, "upstream failed")
            return
        if data is None:
            self.fail(502, "upstream too large")
            return
        self.reply(status, data, out_type)

    def do_GET(self):
        self.passthrough("GET")

    def do_DELETE(self):
        self.passthrough("DELETE")

    def do_POST(self):
        route = self.path.split("?", 1)[0].rstrip("/")
        if not route.endswith("/audio/speech"):
            self.passthrough("POST")
            return
        raw = self.read_body(MAX_SPEECH_BODY)
        if raw is None:
            return
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self.fail(400, "bad json")
            return
        if not isinstance(body, dict):
            self.fail(400, "bad json")
            return
        fmt = body.get("response_format") or "wav"
        if fmt not in AUDIO_TYPES:
            self.fail(400, "bad response_format")
            return
        if "speed" in body and body["speed"] is not None:
            speed = parse_speed(body.pop("speed"))
            if speed is None:
                self.fail(400, "bad speed")
                return
        else:
            speed = parse_speed(load_settings().get("speed")) or 1.0
        s = load_settings()
        voice = s.get("voice")
        if isinstance(voice, str) and voice and not body.get("keep_voice"):
            body["voice"] = voice
        body.pop("keep_voice", None)
        body.pop("speed", None)
        body["response_format"] = "pcm"
        req = urllib.request.Request(BREEZE + "/v1/audio/speech", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=600) as r:
                pcm = read_capped(r, MAX_AUDIO_BYTES)
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                self.fail(502, "redirect blocked")
                return
            err = read_capped(e, MAX_SPEECH_BODY) or b""
            self.reply(e.code, err, "application/json")
            return
        except Exception:
            self.fail(502, "upstream failed")
            return
        if pcm is None:
            self.fail(502, "upstream too large")
            return
        try:
            if abs(speed - 1.0) > 0.01:
                pcm = ffmpeg_pcm(pcm, speed, ["-f", "s16le"])
            if fmt == "pcm":
                out, ctype = pcm, AUDIO_TYPES["pcm"]
            elif fmt == "wav":
                out, ctype = wav_header(len(pcm)) + pcm, AUDIO_TYPES["wav"]
            else:
                out, ctype = ffmpeg_pcm(pcm, 1.0, ["-f", fmt]), AUDIO_TYPES[fmt]
        except Exception:
            self.fail(500, "speech failed")
            return
        self.reply(200, out, ctype)


def bind(port, handler):
    try:
        return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        sys.exit(f"端口 {port} 已经被占用，辅助服务没有启动：{e}")


def serve(port, handler):
    bind(port, handler).serve_forever()


if __name__ == "__main__":
    print(f"ffmpeg: {FFMPEG}", flush=True)
    meme = bind(18765, MemeHandler)
    voice = bind(8138, VoiceHandler)
    threading.Thread(target=meme.serve_forever, daemon=True).start()
    print("meme server on http://127.0.0.1:18765/ , voice proxy on http://127.0.0.1:8138/v1", flush=True)
    voice.serve_forever()

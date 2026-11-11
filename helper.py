# 大肥鱼辅助服务，两件事：
#   1. 18765 端口：把提前下好的表情包压缩包交给 meme_manager（路径以 /<repo>/archive/<ref>.zip 结尾）
#   2. 8138 端口：AstrBot 和 Breeze 之间的语音中转。按 voice-settings.json 换成选中的声音，
#      再用 ffmpeg atempo 调语速（不变调）。设置每次请求现读，改了马上生效，不用重启。
import glob
import http.server
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MEMES = os.path.join(HERE, "memes")
SETTINGS = os.path.join(HERE, "voice-settings.json")
BREEZE = "http://127.0.0.1:8137"
RATE = 24000
NO_WINDOW = 0x08000000
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def wav_header(n_bytes):
    return (b"RIFF" + struct.pack("<I", 36 + n_bytes) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16) + b"data" + struct.pack("<I", n_bytes))


def ffmpeg_pcm(pcm, speed, out_args):
    """24k 单声道 s16 PCM 进，按 out_args 出；speed != 1 时用 atempo 变速不变调。"""
    if not FFMPEG:
        raise RuntimeError("找不到 ffmpeg，装好后才能变速或转换格式")
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0"]
    if abs(speed - 1.0) > 0.01:
        args += ["-filter:a", f"atempo={speed:.2f}"]
    r = subprocess.run(args + out_args + ["pipe:1"], input=pcm, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
    return r.stdout


def resolve_meme_zip(url_path):
    """把 /<仓库>/archive/<版本>.zip 映射到 memes 目录里的压缩包，并且不许跳出这个目录。"""
    clean = url_path.split("?", 1)[0].split("#", 1)[0]
    m = re.search(r"/([^/]+)/archive/(?:refs/tags/)?([^/]+)\.zip$", clean)
    if not m:
        return None
    repo, ref = m.group(1), m.group(2)
    # 「..」和反斜杠拼进文件名后，在 Windows 上会被当成上级目录
    if any(part in (".", "..") or "\\" in part or ".." in part for part in (repo, ref)):
        return None
    root = os.path.realpath(MEMES)
    full = os.path.realpath(os.path.join(root, f"{repo}-{ref}.zip"))
    try:
        if os.path.commonpath([full, root]) != root:
            return None
    except ValueError:
        return None
    return full


class MemeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = resolve_meme_zip(self.path)
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                self.wfile.write(chunk)


class VoiceHandler(http.server.BaseHTTPRequestHandler):
    def reply(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, message):
        self.reply(code, json.dumps({"error": message}, ensure_ascii=False), "application/json")

    def read_len(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        return n if n >= 0 else None

    def passthrough(self, method):
        n = self.read_len()
        if n is None:
            self.fail(400, "Content-Length 不合法")
            return
        data = self.rfile.read(n) if n else None
        req = urllib.request.Request(BREEZE + self.path, data, method=method)
        if self.headers.get("Content-Type"):
            req.add_header("Content-Type", self.headers["Content-Type"])
        try:
            with OPENER.open(req, timeout=600) as r:
                self.reply(r.status, r.read(), r.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            self.reply(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except urllib.error.URLError as e:
            self.fail(502, f"连不上 Breeze：{e.reason}")

    def do_GET(self):
        self.passthrough("GET")

    def do_DELETE(self):
        self.passthrough("DELETE")

    def parse_speed(self, body, settings):
        raw = body.pop("speed") if "speed" in body else None
        if raw is None or raw == "":
            raw = settings.get("speed", 1.0)
        if raw is None or raw == "":
            raw = 1.0
        speed = float(raw)
        # 0 和 NaN 以前会被当成「没填」悄悄换成 1，ffmpeg 也接受不了负数
        if speed != speed or speed in (float("inf"), float("-inf")) or speed <= 0:
            raise ValueError("语速不合法")
        return speed

    def do_POST(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if not path.rstrip("/").endswith("/audio/speech"):
            self.passthrough("POST")
            return
        n = self.read_len()
        if n is None:
            self.fail(400, "Content-Length 不合法")
            return
        raw_body = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw_body or b"{}")
        except ValueError:
            self.fail(400, "请求体不是 JSON")
            return
        if not isinstance(body, dict):
            self.fail(400, "请求体必须是 JSON 对象")
            return
        s = load_settings()
        fmt = body.get("response_format") or "wav"
        try:
            speed = self.parse_speed(body, s)
        except (TypeError, ValueError):
            self.fail(400, "语速不合法")
            return
        if s.get("voice") and not body.get("keep_voice"):
            body["voice"] = s["voice"]
        body.pop("keep_voice", None)
        body["response_format"] = "pcm"
        req = urllib.request.Request(BREEZE + "/v1/audio/speech", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=600) as r:
                pcm = r.read()
        except urllib.error.HTTPError as e:
            self.reply(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
            return
        except urllib.error.URLError as e:
            self.fail(502, f"连不上 Breeze：{e.reason}")
            return
        try:
            if abs(speed - 1.0) > 0.01:
                pcm = ffmpeg_pcm(pcm, speed, ["-f", "s16le"])
            if fmt == "pcm":
                out, ctype = pcm, "audio/pcm"
            elif fmt == "wav":
                out, ctype = wav_header(len(pcm)) + pcm, "audio/wav"
            else:
                out, ctype = ffmpeg_pcm(pcm, 1.0, ["-f", fmt]), f"audio/{fmt}"
        except Exception as e:
            self.fail(500, str(e))
            return
        self.reply(200, out, ctype)


def serve(port, handler):
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    print(f"ffmpeg: {FFMPEG}", flush=True)
    threading.Thread(target=serve, args=(18765, MemeHandler), daemon=True).start()
    print("meme server on http://127.0.0.1:18765/ , voice proxy on http://127.0.0.1:8138/v1", flush=True)
    serve(8138, VoiceHandler)

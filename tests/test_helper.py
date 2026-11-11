"""辅助服务：表情包文件服务，以及 AstrBot 和 Breeze 之间的语音中转。"""
import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import helper


class Quiet:
    def log_message(self, fmt, *args):
        return


class QuietMeme(Quiet, helper.MemeHandler):
    pass


class QuietVoice(Quiet, helper.VoiceHandler):
    pass


def start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def request(method, url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


class FakeBreeze(Quiet, BaseHTTPRequestHandler):
    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.seen.append(("GET", self.path, b""))
        if self.server.fail:
            self._send(503, b'{"error":"down"}', "application/json")
            return
        self._send(200, b'[{"id":"dafeiyu","saved":true}]', "application/json")

    def do_DELETE(self):
        self.server.seen.append(("DELETE", self.path, b""))
        self._send(200, b'{"deleted":true}', "application/json")

    def do_POST(self):
        body = self._read()
        self.server.seen.append(("POST", self.path, body))
        if self.server.fail == "http":
            self._send(422, b'{"error":"bad voice"}', "application/json")
            return
        if self.server.fail == "drop":
            self.close_connection = True
            return
        self._send(200, self.server.pcm, "application/octet-stream")


class HelperCase(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enter_tmp()
        self.old = {
            "MEMES": helper.MEMES,
            "SETTINGS": helper.SETTINGS,
            "BREEZE": helper.BREEZE,
            "FFMPEG": helper.FFMPEG,
        }
        helper.MEMES = os.path.join(self.tmp, "memes")
        helper.SETTINGS = os.path.join(self.tmp, "voice-settings.json")
        os.makedirs(helper.MEMES)
        self.breeze, self.breeze_url = start_server(FakeBreeze)
        self.breeze.seen = []
        self.breeze.pcm = b"\x01\x02" * 1200
        self.breeze.fail = None
        helper.BREEZE = self.breeze_url
        self.voice, self.voice_url = start_server(QuietVoice)
        self.meme, self.meme_url = start_server(QuietMeme)

    def enter_tmp(self):
        import tempfile
        directory = tempfile.mkdtemp(prefix="helper-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        return directory

    def tearDown(self):
        for server in (self.voice, self.meme, self.breeze):
            server.shutdown()
            server.server_close()
        helper.MEMES = self.old["MEMES"]
        helper.SETTINGS = self.old["SETTINGS"]
        helper.BREEZE = self.old["BREEZE"]
        helper.FFMPEG = self.old["FFMPEG"]

    def write_settings(self, data):
        with open(helper.SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def speech(self, payload, headers=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        extra = {"Content-Type": "application/json"}
        extra.update(headers or {})
        return request("POST", self.voice_url + "/v1/audio/speech", raw, extra)


class WavHeaderTests(unittest.TestCase):
    def test_header_is_playable_pcm_wav(self):
        pcm = b"\x00\x01" * 240
        blob = helper.wav_header(len(pcm)) + pcm
        with wave.open(io.BytesIO(blob), "rb") as fh:
            self.assertEqual(fh.getnchannels(), 1)
            self.assertEqual(fh.getframerate(), 24000)
            self.assertEqual(fh.getsampwidth(), 2)
            self.assertEqual(fh.readframes(fh.getnframes()), pcm)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="settings-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.old = helper.SETTINGS
        helper.SETTINGS = os.path.join(self.tmp, "voice-settings.json")

    def tearDown(self):
        helper.SETTINGS = self.old

    def test_missing_broken_and_non_object_settings_are_empty(self):
        self.assertEqual(helper.load_settings(), {})
        with open(helper.SETTINGS, "w", encoding="utf-8") as fh:
            fh.write("{")
        self.assertEqual(helper.load_settings(), {})
        with open(helper.SETTINGS, "w", encoding="utf-8") as fh:
            fh.write("[1, 2]")
        self.assertEqual(helper.load_settings(), {})

    def test_bom_and_real_values_roundtrip(self):
        with open(helper.SETTINGS, "w", encoding="utf-8-sig") as fh:
            json.dump({"voice": "大肥鱼", "speed": 1.2}, fh, ensure_ascii=False)
        self.assertEqual(helper.load_settings()["speed"], 1.2)
        self.assertEqual(helper.load_settings()["voice"], "大肥鱼")


class MemeServerTests(HelperCase):
    def save_zip(self, name, payload=b"PK\x03\x04meme"):
        path = os.path.join(helper.MEMES, name)
        with open(path, "wb") as fh:
            fh.write(payload)
        return path

    def test_github_archive_urls_map_to_local_zip(self):
        payload = b"PK\x03\x04blue-fish"
        self.save_zip("blue-fat-fish-meme-pack-v1.0.0.zip", payload)
        for path in (
            "/anka-afk/blue-fat-fish-meme-pack/archive/v1.0.0.zip",
            "/anka-afk/blue-fat-fish-meme-pack/archive/refs/tags/v1.0.0.zip?download=1",
        ):
            status, ctype, body = request("GET", self.meme_url + path)
            self.assertEqual(status, 200, path)
            self.assertIn("application/zip", ctype)
            self.assertEqual(body, payload)

    def test_missing_pack_is_404(self):
        status, _, body = request("GET", self.meme_url + "/nope/archive/v9.zip")
        self.assertEqual(status, 404)
        self.assertNotIn(b"PK", body)

    def test_parent_directory_and_symlink_cannot_escape(self):
        secret = os.path.join(self.tmp, "secret.zip")
        with open(secret, "wb") as fh:
            fh.write(b"TOP-SECRET-BYTES")
        status, _, body = request("GET", self.meme_url + "/../archive/secret.zip")
        self.assertEqual(status, 404)
        self.assertNotIn(b"TOP-SECRET-BYTES", body)

        link = os.path.join(helper.MEMES, "evil-pack.zip")
        os.symlink(secret, link)
        status, _, body = request("GET", self.meme_url + "/evil/archive/pack.zip")
        self.assertEqual(status, 404)
        self.assertNotIn(b"TOP-SECRET-BYTES", body)

    def test_backslash_and_dotdot_names_are_rejected(self):
        self.assertIsNone(helper.resolve_meme_zip(r"/..\..\windows/archive/system.zip"))
        self.assertIsNone(helper.resolve_meme_zip("/ok/archive/../../etc/passwd.zip"))
        self.assertIsNone(helper.resolve_meme_zip("/foo..bar/archive/v1.zip"))


class VoiceProxyTests(HelperCase):
    def test_wav_speech_uses_settings_voice_and_strips_proxy_fields(self):
        self.write_settings({"voice": "dafeiyu", "speed": 1.0})
        status, ctype, body = self.speech({
            "model": "tts-1", "voice": "alloy", "input": "在吗", "response_format": "wav",
        })
        self.assertEqual(status, 200)
        self.assertIn("audio/wav", ctype)
        with wave.open(io.BytesIO(body), "rb") as fh:
            self.assertEqual(fh.readframes(fh.getnframes()), self.breeze.pcm)
        sent = json.loads(self.breeze.seen[-1][2])
        self.assertEqual(sent["voice"], "dafeiyu")
        self.assertEqual(sent["response_format"], "pcm")
        self.assertNotIn("speed", sent)
        self.assertNotIn("keep_voice", sent)

    def test_keep_voice_and_request_speed_win(self):
        self.write_settings({"voice": "dafeiyu", "speed": 1.8})
        calls = []

        def fake_run(args, input, capture_output, creationflags):
            calls.append(args)
            class Result:
                returncode = 0
                stdout = input[::2]
                stderr = b""
            return Result()

        helper.subprocess.run = fake_run
        self.addCleanup(lambda: setattr(helper.subprocess, "run", __import__("subprocess").run))
        status, ctype, body = self.speech({
            "voice": "preview", "keep_voice": True, "input": "试听", "speed": 1.25, "response_format": "pcm",
        })
        self.assertEqual(status, 200)
        self.assertIn("audio/pcm", ctype)
        self.assertEqual(body, self.breeze.pcm[::2])
        sent = json.loads(self.breeze.seen[-1][2])
        self.assertEqual(sent["voice"], "preview")
        self.assertIn("atempo=1.25", " ".join(str(part) for part in calls[0]))

    def test_settings_speed_is_used_when_request_omits_it(self):
        self.write_settings({"voice": "dafeiyu", "speed": "1.50"})
        calls = []

        def fake_run(args, input, capture_output, creationflags):
            calls.append((args, input))
            class Result:
                returncode = 0
                stdout = b"\x03\x04"
                stderr = b""
            return Result()

        helper.subprocess.run = fake_run
        self.addCleanup(lambda: setattr(helper.subprocess, "run", __import__("subprocess").run))
        status, ctype, body = self.speech({"input": "你好", "response_format": "wav"})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"RIFF"))
        self.assertIn(b"\x03\x04", body)
        self.assertTrue(any("atempo=1.50" in str(part) for part in calls[0][0]))

    def test_speed_one_wav_does_not_need_ffmpeg(self):
        self.write_settings({"speed": 1.0})
        helper.FFMPEG = None

        def boom(*args, **kwargs):
            raise AssertionError("speed 1 的 wav 不应该调用 ffmpeg")

        helper.subprocess.run = boom
        self.addCleanup(lambda: setattr(helper.subprocess, "run", __import__("subprocess").run))
        status, _, body = self.speech({"input": "你好"})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"RIFF"))

    def test_other_formats_and_missing_ffmpeg_return_500(self):
        helper.FFMPEG = None
        status, _, body = self.speech({"input": "你好", "response_format": "mp3"})
        self.assertEqual(status, 500)
        self.assertIn("ffmpeg", json.loads(body)["error"])

    def test_ffmpeg_failure_is_json_500(self):
        self.write_settings({"speed": 1.4})

        def fake_run(args, input, capture_output, creationflags):
            class Result:
                returncode = 1
                stdout = b""
                stderr = b"\xff boom"
            return Result()

        helper.subprocess.run = fake_run
        self.addCleanup(lambda: setattr(helper.subprocess, "run", __import__("subprocess").run))
        status, ctype, body = self.speech({"input": "你好", "speed": 1.4})
        self.assertEqual(status, 500)
        self.assertIn("application/json", ctype)
        self.assertIn("boom", json.loads(body)["error"])

    def test_bad_requests_stay_on_the_server(self):
        cases = [
            (b"{", 400),
            (b"[1, 2]", 400),
            (b'{"speed": 0}', 400),
            (b'{"speed": "fast"}', 400),
            (b'{"speed": null, "input": "x"}', 200),
        ]
        self.write_settings({"speed": 1})
        for payload, expected in cases:
            status, _, _ = self.speech(payload)
            self.assertEqual(status, expected, payload)
        status, _, _ = request(
            "POST", self.voice_url + "/v1/audio/speech", b"{}",
            {"Content-Length": "nope", "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        # 坏请求之后服务还在，下一条正常语音仍能合成
        status, _, body = self.speech({"input": "还在"})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"RIFF"))

    def test_query_string_still_counts_as_speech(self):
        status, ctype, _ = self.speech({"input": "hi"})
        self.assertEqual(status, 200, ctype)
        status, _, body = request(
            "POST", self.voice_url + "/v1/audio/speech?x=1",
            b'{"input":"hi"}', {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"RIFF"))

    def test_upstream_http_error_is_forwarded(self):
        self.breeze.fail = "http"
        status, _, body = self.speech({"input": "hi"})
        self.assertEqual(status, 422)
        self.assertIn("bad voice", body.decode())

    def test_upstream_down_is_502_and_get_delete_passthrough(self):
        helper.BREEZE = "http://127.0.0.1:9"
        status, _, body = self.speech({"input": "hi"})
        self.assertEqual(status, 502)
        self.assertIn("Breeze", json.loads(body)["error"])

        helper.BREEZE = self.breeze_url
        status, _, body = request("GET", self.voice_url + "/v1/voices")
        self.assertEqual(status, 200)
        self.assertIn(b"dafeiyu", body)
        status, _, body = request("DELETE", self.voice_url + "/v1/voices/dafeiyu")
        self.assertEqual(status, 200)
        self.assertIn(b"deleted", body)
        status, _, body = request(
            "POST", self.voice_url + "/v1/audio/voices", b'{"name":"x"}',
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.breeze.seen[-1][1], "/v1/audio/voices")

    def test_proxy_environment_is_ignored(self):
        old_http = os.environ.get("http_proxy")
        old_https = os.environ.get("https_proxy")
        os.environ["http_proxy"] = "http://127.0.0.1:9"
        os.environ["https_proxy"] = "http://127.0.0.1:9"
        self.addCleanup(self._restore_proxy, old_http, old_https)
        status, _, body = self.speech({"input": "不走代理"})
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"RIFF"))

    def _restore_proxy(self, http_proxy, https_proxy):
        if http_proxy is None:
            os.environ.pop("http_proxy", None)
        else:
            os.environ["http_proxy"] = http_proxy
        if https_proxy is None:
            os.environ.pop("https_proxy", None)
        else:
            os.environ["https_proxy"] = https_proxy

    def test_settings_are_reread_every_request(self):
        self.write_settings({"voice": "first", "speed": 1})
        self.speech({"input": "a", "response_format": "pcm"})
        self.assertEqual(json.loads(self.breeze.seen[-1][2])["voice"], "first")
        self.write_settings({"voice": "second", "speed": 1})
        self.speech({"input": "b", "response_format": "pcm"})
        self.assertEqual(json.loads(self.breeze.seen[-1][2])["voice"], "second")


if __name__ == "__main__":
    unittest.main()

"""配置工具：连通 NapCat、语音、群、表情包和预设声音。"""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_setup():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "setup.py")
    spec = importlib.util.spec_from_file_location("dafeiyu_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load_setup()


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Quiet:
    def log_message(self, fmt, *args):
        return


class FakeDownload(Quiet, BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.server.body
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeBreeze(Quiet, BaseHTTPRequestHandler):
    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        self._send(200, json.dumps(self.server.voices).encode())

    def do_POST(self):
        body = self._read()
        self.server.posts.append((self.path, body))
        if self.path.endswith("/audio/speech"):
            payload = json.loads(body)
            rule = self.server.speech.get(payload.get("instructions"))
            if rule == "error":
                self._send(500, b'{"error":"nope"}')
                return
            pcm = rule if isinstance(rule, bytes) else self.server.default_pcm(payload["input"])
            self._send(200, pcm, "application/octet-stream")
            return
        self.server.uploads.append(body)
        self._send(200, b'{"ok":true}')


def start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class SetupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self._old = {
            "ROOT": setup.ROOT,
            "CONFIG_PATH": setup.CONFIG_PATH,
            "BREEZE": setup.BREEZE,
            "GH_ACCELERATORS": setup.GH_ACCELERATORS,
            "MEME_PACKS": setup.MEME_PACKS,
            "VOICE_PRESETS": setup.VOICE_PRESETS,
        }
        setup.ROOT = self.root
        setup.CONFIG_PATH = os.path.join(self.root, "config.json")

    def tearDown(self):
        for key, value in self._old.items():
            setattr(setup, key, value)

    def use_config(self, **extra):
        cfg = {"bot_qq": "10001", "astrbot_dir": "astrbot", "napcat_dir": "napcat"}
        cfg.update(extra)
        write_json(setup.CONFIG_PATH, cfg)
        return cfg


class SaveAndConfigTests(SetupCase):
    def test_failed_save_does_not_destroy_the_original(self):
        path = os.path.join(self.root, "cmd.json")
        setup.save(path, {"keep": "原来的", "n": 1}, backup=False)
        with self.assertRaises(TypeError):
            setup.save(path, {"bad": object()})
        self.assertEqual(read_json(path)["keep"], "原来的")
        self.assertFalse(os.path.exists(path + ".tmp"))
        setup.save(path, {"keep": "新的"})
        self.assertEqual(read_json(path + ".bak")["keep"], "原来的")
        with open(path, encoding="utf-8") as fh:
            self.assertIn("新的", fh.read())

    def test_config_rejects_missing_blank_null_and_broken_files(self):
        with self.assertRaises(SystemExit) as raised:
            setup.config()
        self.assertIn("config.example.json", str(raised.exception))

        write_json(setup.CONFIG_PATH, {"bot_qq": None, "owner_qq": "1"})
        with self.assertRaises(SystemExit) as raised:
            setup.config()
        self.assertIn("bot_qq", str(raised.exception))

        write_json(setup.CONFIG_PATH, {"bot_qq": "   "})
        with self.assertRaises(SystemExit):
            setup.config()

        write_json(setup.CONFIG_PATH, ["nope"])
        with self.assertRaises(SystemExit) as raised:
            setup.config()
        self.assertIn("JSON 对象", str(raised.exception))

        with open(setup.CONFIG_PATH, "w", encoding="utf-8") as fh:
            fh.write("{")
        with self.assertRaises(SystemExit) as raised:
            setup.config()
        self.assertIn("读不了", str(raised.exception))

    def test_config_strips_qq_and_accepts_numbers(self):
        write_json(setup.CONFIG_PATH, {"bot_qq": " 10001 "})
        self.assertEqual(setup.config()["bot_qq"], "10001")
        write_json(setup.CONFIG_PATH, {"bot_qq": 10001})
        self.assertEqual(setup.config()["bot_qq"], "10001")


class LinkTests(SetupCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.use_config(owner_qq="20002")
        self.cmd = os.path.join(self.root, "astrbot", "data", "cmd_config.json")
        self.ob = os.path.join(self.root, "napcat", "config", "onebot11_10001.json")
        self.webui = os.path.join(self.root, "napcat", "config", "webui.json")
        write_json(self.cmd, {
            "dashboard": None,
            "platform": [{"id": "tg", "type": "telegram", "enable": True}, "skip-me"],
            "note": "留下",
        })
        write_json(self.ob, {"network": None, "other": 1})
        write_json(self.webui, {"host": "0.0.0.0", "port": 6099})

    def test_link_binds_localhost_and_shares_tokens(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.link(setup.config())
        cmd = read_json(self.cmd)
        ob = read_json(self.ob)
        webui = read_json(self.webui)
        cfg = read_json(setup.CONFIG_PATH)
        qq = [p for p in cmd["platform"] if p.get("type") == "aiocqhttp"]
        self.assertEqual(len(qq), 1)
        self.assertEqual(qq[0]["ws_reverse_host"], "127.0.0.1")
        self.assertEqual(qq[0]["ws_reverse_port"], 6199)
        self.assertEqual(cmd["dashboard"]["host"], "127.0.0.1")
        self.assertEqual(cmd["note"], "留下")
        self.assertEqual([p["type"] for p in cmd["platform"] if isinstance(p, dict) and p["type"] != "aiocqhttp"], ["telegram"])
        client = ob["network"]["websocketClients"][0]
        http_server = ob["network"]["httpServers"][0]
        self.assertEqual(client["token"], qq[0]["ws_reverse_token"])
        self.assertEqual(http_server["token"], cfg["napcat_http_token"])
        self.assertNotEqual(client["token"], http_server["token"])
        self.assertEqual(len(client["token"]), 32)
        self.assertEqual(client["url"], "ws://127.0.0.1:6199/ws")
        self.assertEqual(http_server["host"], "127.0.0.1")
        self.assertEqual(webui["host"], "127.0.0.1")
        self.assertEqual(webui["port"], 6099)
        self.assertIn("已连通", buf.getvalue())
        # 再跑一次不能留下两个 QQ 平台，原来的 telegram 还在
        with redirect_stdout(io.StringIO()):
            setup.link(setup.config())
        again = read_json(self.cmd)
        types = [p.get("type") for p in again["platform"]]
        self.assertEqual(types.count("aiocqhttp"), 1)
        self.assertIn("telegram", types)

    def test_link_stops_before_writing_when_a_file_is_missing_or_broken(self):
        os.remove(self.ob)
        before = read_json(self.cmd)
        with self.assertRaises(SystemExit) as raised:
            setup.link(setup.config())
        self.assertIn("onebot11_10001.json", str(raised.exception))
        self.assertEqual(read_json(self.cmd), before)

        write_json(self.ob, {"network": {}})
        with open(self.cmd, "w", encoding="utf-8") as fh:
            fh.write("{")
        with self.assertRaises(SystemExit) as raised:
            setup.link(setup.config())
        self.assertIn("还没改", str(raised.exception))
        self.assertEqual(read_json(self.ob), {"network": {}})


class TtsAndGroupTests(SetupCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.use_config()
        real_plugins = os.path.join(self._old["ROOT"], "plugins")
        shutil.copytree(real_plugins, os.path.join(self.root, "plugins"))
        self.cmd = os.path.join(self.root, "astrbot", "data", "cmd_config.json")
        write_json(self.cmd, {
            "provider": None,
            "provider_tts_settings": {"enable": False, "trigger_probability": "high", "dual_output": True},
        })

    def test_tts_installs_plugin_without_a_config_directory(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.tts(self.cfg)
        cmd = read_json(self.cmd)
        edge = {"id": "edge", "type": "edge_tts", "enable": True}
        # 重新放一个 edge，再跑一次，确认会关掉而且不会重复添加 Breeze
        cmd["provider"].append(edge)
        write_json(self.cmd, cmd)
        vr = os.path.join(self.root, "astrbot", "data", "config", "astrbot_plugin_dafeiyu_voice_ratio_config.json")
        self.assertTrue(os.path.isfile(os.path.join(
            self.root, "astrbot", "data", "plugins", "astrbot_plugin_dafeiyu_voice_ratio", "main.py")))
        created = read_json(vr)
        self.assertEqual(created, {"group_probability": 0.33, "private_probability": 0.33})
        write_json(vr, {"group_probability": 0.1, "private_probability": 0.8})
        vs = os.path.join(self.root, "voice-settings.json")
        self.assertEqual(read_json(vs)["speed"], 1.2)
        write_json(vs, {"voice": "custom", "speed": 0.9, "labels": {}})
        with redirect_stdout(io.StringIO()):
            setup.tts(self.cfg)
        cmd = read_json(self.cmd)
        breeze = [p for p in cmd["provider"] if p["id"] == "大肥鱼语音-Breeze"]
        edge_rows = [p for p in cmd["provider"] if p.get("type") == "edge_tts"]
        self.assertEqual(len(breeze), 1)
        self.assertEqual(breeze[0]["api_base"], "http://127.0.0.1:8138/v1")
        self.assertEqual(len(edge_rows), 1)
        self.assertFalse(edge_rows[0]["enable"])
        self.assertTrue(cmd["provider_tts_settings"]["dual_output"])
        self.assertEqual(cmd["provider_tts_settings"]["trigger_probability"], 1.0)
        self.assertEqual(read_json(vr)["private_probability"], 0.8)
        self.assertEqual(read_json(vs)["voice"], "custom")

    def test_tts_keeps_an_existing_probability_below_one(self):
        write_json(self.cmd, {"provider": [], "provider_tts_settings": {"trigger_probability": 0}})
        with redirect_stdout(io.StringIO()):
            setup.tts(self.cfg)
        vr = read_json(os.path.join(
            self.root, "astrbot", "data", "config", "astrbot_plugin_dafeiyu_voice_ratio_config.json"))
        self.assertEqual(vr["group_probability"], 0)
        self.assertEqual(vr["private_probability"], 0)

    def test_group_whitelist_and_probabilities(self):
        write_json(self.cmd, {"platform_settings": {"id_whitelist": None, "other": True}})
        gp = os.path.join(self.root, "astrbot", "data", "config", "astrbot_plugin_group_chat_plus_config.json")
        write_json(gp, {
            "enabled_groups": [555, "555"],
            "initial_probability": "0.02",
            "after_reply_probability": "high",
            "note": "留",
        })
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.group(self.cfg, " 555 ")
            setup.group(self.cfg, "555")
        cmd = read_json(self.cmd)
        self.assertEqual(cmd["platform_settings"]["id_whitelist"], ["qq:GroupMessage:555"])
        self.assertTrue(cmd["platform_settings"]["other"])
        group_cfg = read_json(gp)
        self.assertEqual(group_cfg["enabled_groups"], ["555"])
        self.assertEqual(group_cfg["initial_probability"], 0.1)
        self.assertEqual(group_cfg["after_reply_probability"], 0.6)
        self.assertEqual(group_cfg["note"], "留")
        self.assertIn("555", buf.getvalue())

        write_json(gp, {"enabled_groups": "bad", "initial_probability": "0.2", "after_reply_probability": 0.2})
        with redirect_stdout(io.StringIO()):
            setup.group(self.cfg, "777")
        group_cfg = read_json(gp)
        self.assertEqual(group_cfg["enabled_groups"], ["777"])
        self.assertEqual(group_cfg["initial_probability"], 0.2)
        self.assertEqual(group_cfg["after_reply_probability"], 0.2)
        self.assertIn("qq:GroupMessage:777", read_json(self.cmd)["platform_settings"]["id_whitelist"])

    def test_group_without_plugin_and_bad_ids(self):
        write_json(self.cmd, {})
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.group(self.cfg, "42")
        self.assertIn("没装群聊插件", buf.getvalue())
        self.assertEqual(read_json(self.cmd)["platform_settings"]["id_whitelist"], ["qq:GroupMessage:42"])
        for bad in ("", "12a", "12 3", "-1"):
            with self.assertRaises(SystemExit):
                setup.group(self.cfg, bad)


class MemeTests(SetupCase):
    def test_existing_pack_is_skipped_and_html_is_not_saved(self):
        setup.MEME_PACKS = ["anka-afk/blue-fat-fish-meme-pack@v1.0.0"]
        target = os.path.join(self.root, "memes", "blue-fat-fish-meme-pack-v1.0.0.zip")
        write_json(os.path.join(self.root, "memes", "placeholder.json"), {})
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(b"PK\x03\x04already")
        server = start_server(FakeDownload)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        server.body = b"<html>not a zip</html>"
        setup.GH_ACCELERATORS = [f"http://127.0.0.1:{server.server_address[1]}/"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.memes({})
        self.assertIn("已有", buf.getvalue())
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"PK\x03\x04already")

        os.remove(target)
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.memes({})
        self.assertFalse(os.path.exists(target))
        self.assertFalse(os.path.exists(target + ".part"))
        self.assertIn("不是压缩包", buf.getvalue())
        self.assertIn("没下下来", buf.getvalue())

    def test_zip_download_is_kept(self):
        setup.MEME_PACKS = ["anka-afk/demo-pack@v9"]
        server = start_server(FakeDownload)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        server.body = b"PK\x03\x04hello-memes"
        setup.GH_ACCELERATORS = [f"http://127.0.0.1:{server.server_address[1]}/"]
        with redirect_stdout(io.StringIO()):
            setup.memes({})
        target = os.path.join(self.root, "memes", "demo-pack-v9.zip")
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), server.body)
        self.assertFalse(os.path.exists(target + ".part"))


class VoicePresetTests(SetupCase):
    def setUp(self):
        super().setUp()
        self.server = start_server(FakeBreeze)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.server.posts = []
        self.server.uploads = []
        self.server.voices = []
        self.server.speech = {}

        def default_pcm(text):
            return b"\x00\x01" * (len(text) * 4800)

        self.server.default_pcm = default_pcm
        setup.BREEZE = f"http://127.0.0.1:{self.server.server_address[1]}"
        setup.VOICE_PRESETS = [
            ("dafeiyu", "大肥鱼（软萌撒娇）", "软萌", None),
            ("yuanqi", "元气少女", "元气", "今天天气不错呀"),
        ]

    def test_presets_are_uploaded_once_and_bad_audio_is_skipped(self):
        write_json(os.path.join(self.root, "voice-settings.json"), {
            "voice": "dafeiyu", "speed": 1.2, "labels": {"dafeiyu": "我的名字"},
        })
        self.server.voices = [{"id": "dafeiyu", "saved": True}, {"id": "temp", "saved": False}]
        self.server.speech["元气"] = b""
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.voices({})
        labels = read_json(os.path.join(self.root, "voice-settings.json"))["labels"]
        self.assertEqual(labels["dafeiyu"], "我的名字")
        self.assertNotIn("yuanqi", labels)
        self.assertEqual(self.server.uploads, [])
        self.assertIn("长度不正常", buf.getvalue())
        self.assertIn("已有", buf.getvalue())

        self.server.speech["元气"] = None
        self.server.voices = []
        with redirect_stdout(io.StringIO()):
            setup.voices({})
        labels = read_json(os.path.join(self.root, "voice-settings.json"))["labels"]
        self.assertEqual(labels["dafeiyu"], "我的名字")
        self.assertEqual(labels["yuanqi"], "元气少女")
        self.assertEqual(len(self.server.uploads), 2)
        joined = b"\n".join(self.server.uploads)
        self.assertIn("dafeiyu".encode(), joined)
        self.assertIn("yuanqi".encode(), joined)
        self.assertIn(b"RIFF", joined)
        speech = [json.loads(body) for path, body in self.server.posts if path.endswith("/audio/speech")]
        self.assertTrue(any(item["input"] == setup.DESIGN_TEXT for item in speech))
        self.assertTrue(any(item["input"] == "今天天气不错呀" for item in speech))

    def test_upstream_errors_do_not_abort_the_rest(self):
        setup.VOICE_PRESETS = [
            ("bad", "坏掉的", "会失败", "短"),
            ("good", "好的", "会成功", "短句呀"),
        ]
        self.server.speech["会失败"] = "error"
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup.voices({})
        labels = read_json(os.path.join(self.root, "voice-settings.json"))["labels"]
        self.assertNotIn("bad", labels)
        self.assertEqual(labels["good"], "好的")
        self.assertIn("长度不正常", buf.getvalue())
        self.assertEqual(len(self.server.uploads), 1)

    def test_missing_breeze_exits(self):
        setup.BREEZE = "http://127.0.0.1:9"
        with self.assertRaises(SystemExit) as raised:
            setup.voices({})
        self.assertIn("连不上 Breeze", str(raised.exception))


class ShortcutAndMainTests(SetupCase):
    def test_shortcut_invokes_powershell_without_a_console(self):
        self.use_config(icon="dafeiyu.ico")
        seen = {}

        def fake_run(args, check):
            seen["args"] = args
            seen["check"] = check
            class Result:
                returncode = 0
            return Result()

        setup.subprocess.run = fake_run
        self.addCleanup(lambda: setattr(setup.subprocess, "run", __import__("subprocess").run))
        with redirect_stdout(io.StringIO()):
            setup.shortcut(setup.config())
        self.assertEqual(seen["args"][0], "powershell")
        self.assertTrue(seen["check"])
        blob = " ".join(seen["args"])
        self.assertIn("pythonw.exe", blob)
        self.assertIn("dafeiyu_console.pyw", blob)

    def test_main_dispatches_group_and_rejects_unknown_commands(self):
        self.use_config()
        write_json(os.path.join(self.root, "astrbot", "data", "cmd_config.json"), {})
        old_argv = setup.sys.argv
        self.addCleanup(lambda: setattr(setup.sys, "argv", old_argv))
        setup.sys.argv = ["setup.py", "group", "321"]
        with redirect_stdout(io.StringIO()):
            setup.main()
        wl = read_json(os.path.join(self.root, "astrbot", "data", "cmd_config.json"))
        self.assertEqual(wl["platform_settings"]["id_whitelist"], ["qq:GroupMessage:321"])

        setup.sys.argv = ["setup.py", "group"]
        with self.assertRaises(SystemExit) as raised:
            setup.main()
        self.assertIn("群号", str(raised.exception))

        setup.sys.argv = ["setup.py", "nope"]
        with self.assertRaises(SystemExit) as raised:
            setup.main()
        self.assertIn("setup.py link", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

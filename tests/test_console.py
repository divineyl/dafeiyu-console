"""控制台：配置、进程识别、设置保存、日志和窗口状态。"""
import gc
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import tests.bootstrap  # noqa: F401  必须在控制台之前
import importlib.util
from importlib.machinery import SourceFileLoader

import psutil
import tkinter as tk


def load_console():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dafeiyu_console.pyw")
    loader = SourceFileLoader("dafeiyu_console", path)
    spec = importlib.util.spec_from_loader("dafeiyu_console", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


console = load_console()


class Proc:
    def __init__(self, name, args, children=None, fail_children=False, fail_cmdline=False):
        self.info = {"name": name}
        self.args = list(args)
        self.child_list = list(children or [])
        self.fail_children = fail_children
        self.fail_cmdline = fail_cmdline
        self.killed = False

    def name(self):
        return self.info["name"]

    def cmdline(self):
        if self.fail_cmdline:
            raise psutil.Error("denied")
        return list(self.args)

    def children(self, recursive=False):
        if self.fail_children:
            raise psutil.Error("denied")
        return list(self.child_list)

    def kill(self):
        self.killed = True


class Label:
    def __init__(self):
        self.conf = {}

    def config(self, **kwargs):
        self.conf.update(kwargs)


class FakeRoot:
    def __init__(self):
        self.jobs = []
        self.cancelled = []
        self.destroyed = False

    def after(self, delay, fn):
        self.jobs.append((delay, fn))
        return len(self.jobs)

    def after_cancel(self, job):
        self.cancelled.append(job)

    def destroy(self):
        self.destroyed = True


class FakeText:
    def __init__(self, bottom=True):
        self.bottom = bottom
        self.content = ""
        self.seen = False

    def yview(self):
        return (0.0, 1.0 if self.bottom else 0.2)

    def delete(self, start, end):
        self.content = ""

    def insert(self, where, text):
        self.content += text

    def see(self, where):
        self.seen = True


class FakeList:
    def __init__(self):
        self.items = []
        self.pick = ()

    def delete(self, start, end):
        self.items.clear()

    def insert(self, where, text):
        self.items.append(text)

    def selection_set(self, where):
        self.pick = (len(self.items) - 1,)

    def curselection(self):
        return self.pick


class Entry:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name


class ConfigTests(TempCase):
    def setUp(self):
        super().setUp()
        self.old = (console.CONFIG_PATH, console.ROOT, console.CFG)
        console.CONFIG_PATH = os.path.join(self.root, "config.json")
        console.ROOT = self.root

    def tearDown(self):
        console.CONFIG_PATH, console.ROOT, console.CFG = self.old
        console.CONFIG_ERROR = None

    def test_relative_absolute_null_and_qq(self):
        absolute = os.path.join(self.root, "elsewhere", "astrbot")
        write_json(console.CONFIG_PATH, {
            "bot_qq": " 10001 ",
            "astrbot_dir": "astrbot",
            "qq_exe": absolute,
            "icon": None,
            "breeze_dir": 12,
            "napcat_http_token": "abc",
        })
        cfg = console.load_config()
        self.assertEqual(cfg["bot_qq"], "10001")
        self.assertEqual(cfg["astrbot_dir"], os.path.join(self.root, "astrbot"))
        self.assertEqual(cfg["qq_exe"], absolute)
        self.assertEqual(cfg["icon"], os.path.join(self.root, console.DEFAULTS["icon"]))
        self.assertTrue(cfg["breeze_dir"].endswith(console.DEFAULTS["breeze_dir"]))
        self.assertIsNone(console.CONFIG_ERROR)

        write_json(console.CONFIG_PATH, {"bot_qq": 10001})
        self.assertEqual(console.load_config()["bot_qq"], "10001")
        write_json(console.CONFIG_PATH, {"bot_qq": None})
        self.assertEqual(console.load_config()["bot_qq"], "")

    def test_broken_config_is_reported_instead_of_crashing(self):
        with open(console.CONFIG_PATH, "w", encoding="utf-8") as fh:
            fh.write("{")
        cfg = console.load_config()
        self.assertEqual(cfg["bot_qq"], "")
        self.assertIn("读不了", console.CONFIG_ERROR)
        write_json(console.CONFIG_PATH, ["nope"])
        console.load_config()
        self.assertIn("读不了", console.CONFIG_ERROR)

    def test_bom_roundtrip_and_failed_save_keeps_original(self):
        path = os.path.join(self.root, "voice-settings.json")
        with open(path, "w", encoding="utf-8-sig") as fh:
            json.dump({"voice": "大肥鱼", "speed": 1.2}, fh, ensure_ascii=False)
        self.assertEqual(console.load_json(path)["voice"], "大肥鱼")
        console.save_json(path, {"voice": "新声音"})
        with open(path, encoding="utf-8") as fh:
            self.assertIn("新声音", fh.read())
        with self.assertRaises(TypeError):
            console.save_json(path, {"bad": object()})
        self.assertEqual(console.load_json(path)["voice"], "新声音")
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_token_is_reread_from_disk(self):
        write_json(console.CONFIG_PATH, {"napcat_http_token": "new-token"})
        self.assertEqual(console.napcat_token(), "new-token")
        os.remove(console.CONFIG_PATH)
        self.assertEqual(console.napcat_token(), str(console.CFG.get("napcat_http_token") or ""))


class ProcessTests(TempCase):
    def test_port_and_http_checks(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            self.assertTrue(console.port_open(port))
        self.assertFalse(console.port_open(port))

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        url = f"http://127.0.0.1:{server.server_address[1]}/health"
        self.assertTrue(console.http_ok(url))
        self.assertFalse(console.http_ok("http://127.0.0.1:9/nope", timeout=0.2))

    def test_runs_file_and_kill_tree(self):
        helper_py = os.path.join("C:", "app", "helper.py")
        meme_py = os.path.join("D:", "x", "meme-server.py")
        self.assertTrue(console.runs_file(Proc("helper.py", ["helper.py"]), "helper.py"))
        self.assertTrue(console.runs_file(Proc("pythonw.exe", ["pythonw.exe", helper_py]), "helper.py"))
        self.assertTrue(console.runs_file(Proc("python.exe", ["python.exe", meme_py]), "meme-server.py"))
        self.assertFalse(console.runs_file(Proc("python.exe", ["python.exe", "helper.py.bak"]), "helper.py"))
        self.assertFalse(console.runs_file(Proc("notepad.exe", ["notepad.exe", "helper.py"]), "helper.py"))
        self.assertFalse(console.runs_file(Proc("python.exe", ["python.exe"], fail_cmdline=True), "helper.py"))
        self.assertEqual(console.cmdline(Proc("qq.exe", ["qq.exe"], fail_cmdline=True)), "")

        child = Proc("child.exe", ["child.exe"])
        parent = Proc("parent.exe", ["parent.exe"], children=[child])
        waited = {}

        def wait_procs(procs, timeout):
            waited["procs"] = procs

        with patch.object(console.psutil, "wait_procs", wait_procs):
            console.kill_tree(parent)
        self.assertTrue(parent.killed and child.killed)
        self.assertEqual(waited["procs"], [child, parent])

        lonely = Proc("lonely.exe", ["lonely.exe"], fail_children=True)
        with patch.object(console.psutil, "wait_procs", lambda *args, **kwargs: None):
            console.kill_tree(lonely)
        self.assertTrue(lonely.killed)

    def test_service_detection_does_not_use_qq_prefix(self):
        procs = [
            Proc("Breeze-Server.EXE", ["Breeze-Server.EXE"]),
            Proc("notepad.exe", ["notepad.exe"]),
            Proc("python.exe", ["python.exe", r"E:\dafeiyu\helper.py"]),
            Proc("python.exe", ["python.exe", "helper.py.bak"]),
            Proc("astrbot.exe", ["astrbot.exe", "run"]),
            Proc("astrbot", ["astrbot", "run"]),
            Proc("python.exe", ["python.exe", os.path.join("C:", "AstrBot", "astrbot.exe"), "run"]),
            Proc("python.exe", ["python.exe", "helper.py", "run"]),
            Proc("astrbot.exe", ["astrbot.exe", "run", "--extra"]),
            Proc("NapCatWinBootMain.exe", ["NapCatWinBootMain.exe"]),
            Proc("QQ.exe", ["QQ.exe", "-q", "10001"]),
            Proc("QQ.exe", ["QQ.exe", "-q", "100011"]),
            Proc("QQ.exe", ["QQ.exe", "-q", "10001", "--type=renderer"]),
            Proc("QQ.exe", ["QQ.exe"]),
        ]
        with patch.object(console.psutil, "process_iter", lambda *args, **kwargs: iter(procs)):
            breeze = console.Breeze("breeze", "b", "d")
            helper = console.Helper("helper", "h", "d")
            astrbot = console.AstrBot("astrbot", "a", "d")
            old_qq = console.BOT_QQ
            console.BOT_QQ = "10001"
            try:
                napcat = console.NapCat("napcat", "n", "d")
                self.assertEqual(len(breeze.processes()), 1)
                self.assertEqual(len(helper.processes()), 1)
                self.assertEqual(len(astrbot.processes()), 3)
                found = napcat.processes()
            finally:
                console.BOT_QQ = old_qq
        names = [p.info["name"].lower() for p in found]
        self.assertIn("napcatwinbootmain.exe", names)
        self.assertEqual(names.count("qq.exe"), 1)
        self.assertEqual(found[-1].args[-1] if found[-1].info["name"].lower() == "qq.exe" else "10001", "10001")

    def test_empty_bot_qq_does_not_match_every_qq(self):
        procs = [Proc("QQ.exe", ["QQ.exe", "-q", "10001"]), Proc("NapCatWinBootMain.exe", ["x"])]
        old = console.BOT_QQ
        console.BOT_QQ = ""
        try:
            with patch.object(console.psutil, "process_iter", lambda *args, **kwargs: iter(procs)):
                found = console.NapCat("napcat", "n", "d").processes()
        finally:
            console.BOT_QQ = old
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].info["name"].lower(), "napcatwinbootmain.exe")

    def test_spawn_and_breeze_command(self):
        service = console.Helper("helper", "辅助", "d")
        old_logs = console.LOGS
        console.LOGS = os.path.join(self.root, "logs")
        seen = {}

        def fake_popen(args, cwd, env, stdout, stderr, stdin, creationflags):
            seen.update(args=args, cwd=cwd, env=env, creationflags=creationflags, stdin=stdin)
            class Handle:
                pass
            return Handle()

        try:
            with patch.object(console.subprocess, "Popen", fake_popen):
                service.spawn([console.PYTHON, "-u", "helper.py"], self.root, {"DEMO": "1"})
            self.assertTrue(os.path.exists(service.log))
            self.assertEqual(seen["cwd"], self.root)
            self.assertEqual(seen["env"]["DEMO"], "1")
            self.assertIn("PATH", seen["env"])
            self.assertEqual(seen["creationflags"], console.NO_WINDOW | console.NEW_GROUP)
            self.assertEqual(seen["stdin"], console.subprocess.DEVNULL)

            breeze_root = os.path.join(self.root, "breeze")
            os.makedirs(os.path.join(breeze_root, "bin", "nested"))
            exe = os.path.join(breeze_root, "bin", "nested", "Breeze-Server.exe")
            with open(exe, "wb") as fh:
                fh.write(b"")
            old_breeze, old_cfg = console.BREEZE, console.CFG
            console.BREEZE = breeze_root
            console.CFG = {**console.CFG, "breeze_model": "model.gguf"}
            breeze = console.Breeze("breeze", "Breeze", "d")
            with patch.object(console.subprocess, "Popen", fake_popen):
                breeze.start()
            self.assertEqual(seen["args"][0], exe)
            self.assertIn("127.0.0.1", seen["args"])
            self.assertIn("8137", seen["args"])
            self.assertIn(os.path.join(breeze_root, "models", "model.gguf"), seen["args"])
            console.BREEZE = os.path.join(self.root, "missing")
            with self.assertRaises(FileNotFoundError):
                breeze.start()
            console.BREEZE, console.CFG = old_breeze, old_cfg
        finally:
            console.LOGS = old_logs


class SettingsFlowTests(TempCase):
    def app(self):
        app = console.App.__new__(console.App)
        app.messages = []
        app.say = lambda text, color="#1a6": app.messages.append((text, color))
        app.started = []
        app.start_one = lambda service: app.started.append(service)
        app.wait_for = lambda check, seconds: True
        app.vars = {
            "initial": Var(33),
            "after": Var(60),
            "duration": Var(90),
            "tts_group": Var(20),
            "tts_private": Var(80),
        }
        return app

    def setUp(self):
        super().setUp()
        self.old = {
            "GCP": console.GCP_CONFIG,
            "CMD": console.CMD_CONFIG,
            "VR_DIR": console.VOICE_RATIO_PLUGIN,
            "VR": console.VOICE_RATIO_CONFIG,
        }
        console.GCP_CONFIG = os.path.join(self.root, "group.json")
        console.CMD_CONFIG = os.path.join(self.root, "cmd.json")
        console.VOICE_RATIO_CONFIG = os.path.join(self.root, "vr.json")
        console.VOICE_RATIO_PLUGIN = os.path.join(self.root, "plugin")
        self.old_stop = console.BY_KEY["astrbot"].stop

    def tearDown(self):
        console.GCP_CONFIG = self.old["GCP"]
        console.CMD_CONFIG = self.old["CMD"]
        console.VOICE_RATIO_PLUGIN = self.old["VR_DIR"]
        console.VOICE_RATIO_CONFIG = self.old["VR"]
        console.BY_KEY["astrbot"].stop = self.old_stop

    def test_apply_does_not_stop_the_bot_when_config_cannot_be_read(self):
        app = self.app()
        stopped = []
        console.BY_KEY["astrbot"].stop = lambda: stopped.append("stop")
        app.apply_settings()
        self.assertEqual(stopped, [])
        self.assertTrue(any("没读成功" in text for text, _ in app.messages))

        write_json(console.GCP_CONFIG, [])
        write_json(console.CMD_CONFIG, {"provider_tts_settings": {}})
        app.messages.clear()
        app.apply_settings()
        self.assertEqual(stopped, [])
        self.assertTrue(any("格式不对" in text for text, _ in app.messages))

    def test_apply_splits_voice_probability_when_plugin_exists(self):
        os.makedirs(console.VOICE_RATIO_PLUGIN)
        write_json(console.GCP_CONFIG, {"initial_probability": 0.1, "note": "留"})
        write_json(console.CMD_CONFIG, {"provider_tts_settings": None, "other": 1})
        app = self.app()
        stopped = []
        console.BY_KEY["astrbot"].stop = lambda: stopped.append("stop")
        app.apply_settings()
        self.assertEqual(stopped, ["stop"])
        self.assertEqual(app.started, [console.BY_KEY["astrbot"]])
        group = read_json(console.GCP_CONFIG)
        cmd = read_json(console.CMD_CONFIG)
        voice = read_json(console.VOICE_RATIO_CONFIG)
        self.assertEqual(group["initial_probability"], 0.33)
        self.assertEqual(group["after_reply_probability"], 0.6)
        self.assertEqual(group["probability_duration"], 90)
        self.assertEqual(group["note"], "留")
        self.assertEqual(cmd["other"], 1)
        self.assertEqual(cmd["provider_tts_settings"]["trigger_probability"], 1.0)
        self.assertEqual(voice["group_probability"], 0.2)
        self.assertEqual(voice["private_probability"], 0.8)
        self.assertTrue(any("已生效" in text for text, _ in app.messages))

    def test_apply_uses_one_probability_without_plugin_and_restarts_after_save_error(self):
        write_json(console.GCP_CONFIG, {})
        write_json(console.CMD_CONFIG, {})
        app = self.app()
        console.BY_KEY["astrbot"].stop = lambda: None
        app.apply_settings()
        self.assertEqual(read_json(console.CMD_CONFIG)["provider_tts_settings"]["trigger_probability"], 0.8)
        self.assertFalse(os.path.exists(console.VOICE_RATIO_CONFIG))

        app.wait_for = lambda check, seconds: False
        app.messages.clear()
        app.started.clear()
        real_save = console.save_json

        def fail_cmd(path, data):
            if path == console.CMD_CONFIG:
                raise OSError("disk full")
            real_save(path, data)

        with patch.object(console, "save_json", fail_cmd):
            app.apply_settings()
        self.assertEqual(app.started, [console.BY_KEY["astrbot"]])
        self.assertTrue(any("保存失败" in text for text, _ in app.messages))


class UiLogicTests(TempCase):
    @classmethod
    def setUpClass(cls):
        cls.tk = tk.Tk()
        cls.tk.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.tk.destroy()
        gc.collect()

    def test_settings_tab_survives_bad_numbers_and_shares_one_slider(self):
        old = (console.GCP_CONFIG, console.CMD_CONFIG, console.VOICE_RATIO_CONFIG, console.VOICE_RATIO_PLUGIN)
        console.GCP_CONFIG = os.path.join(self.root, "g.json")
        console.CMD_CONFIG = os.path.join(self.root, "c.json")
        console.VOICE_RATIO_CONFIG = os.path.join(self.root, "v.json")
        console.VOICE_RATIO_PLUGIN = os.path.join(self.root, "missing-plugin")
        write_json(console.GCP_CONFIG, {
            "initial_probability": "fast",
            "after_reply_probability": 0.4,
            "probability_duration": "150",
        })
        write_json(console.CMD_CONFIG, {"provider_tts_settings": "nope"})
        app = console.App.__new__(console.App)
        try:
            frame = app.build_settings(self.tk)
            frame.destroy()
            self.assertIs(app.vars["tts_group"], app.vars["tts_private"])
            self.assertEqual(app.vars["initial"].get(), 10)
            self.assertEqual(app.vars["after"].get(), 40)
            self.assertEqual(app.vars["duration"].get(), 150)
            self.assertEqual(app.vars["tts_private"].get(), 33)

            os.makedirs(console.VOICE_RATIO_PLUGIN)
            write_json(console.VOICE_RATIO_CONFIG, {"group_probability": 0.2, "private_probability": 0.5})
            frame = app.build_settings(self.tk)
            frame.destroy()
            self.assertIsNot(app.vars["tts_group"], app.vars["tts_private"])
            self.assertEqual(app.vars["tts_group"].get(), 20)
            self.assertEqual(app.vars["tts_private"].get(), 50)
        finally:
            console.GCP_CONFIG, console.CMD_CONFIG, console.VOICE_RATIO_CONFIG, console.VOICE_RATIO_PLUGIN = old

    def test_voice_list_order_selection_and_candidate_ids(self):
        app = console.App.__new__(console.App)
        app.voice_list = FakeList()
        app.messages = []
        app.say = lambda text, color="#1a6": app.messages.append(text)
        old = console.VOICE_SETTINGS
        console.VOICE_SETTINGS = os.path.join(self.root, "voice-settings.json")
        try:
            write_json(console.VOICE_SETTINGS, {"voice": "b", "speed": 1, "labels": {"b": "乙", "a": "甲"}})
            app.show_voices(None)
            self.assertIn("没在运行", app.voice_list.items[0])
            app.show_voices(["c", "a", "b"])
            self.assertEqual(app.voice_list.items[0], "乙  ✔ 正在用")
            self.assertEqual(app.voice_list.items[1], "甲")
            self.assertTrue(app.voice_list.items[2].startswith("c"))
            app.voice_list.pick = ()
            self.assertIsNone(app.selected_voice())
            self.assertTrue(any("点选" in text for text in app.messages))
            app.voice_list.pick = (1,)
            self.assertEqual(app.selected_voice(), "a")

            captured = []
            app.breeze = lambda *args, **kwargs: captured.append(args)
            app.root = FakeRoot()
            app.refresh_voices = lambda: None
            app.new_name = Entry("  软萌  ")
            path = os.path.join(self.root, "cand.wav")
            with open(path, "wb") as fh:
                fh.write(b"RIFF-demo")
            with patch.object(console.time, "strftime", return_value="v01020304"), patch.object(
                console.random, "randint", side_effect=[7, 8]
            ):
                app.save_candidate(path, "描述很长很长")
                app.save_candidate(path, "描述很长很长")
            labels = read_json(console.VOICE_SETTINGS)["labels"]
            self.assertEqual(labels["v01020304007"], "软萌")
            self.assertIn("v01020304008", labels)
            self.assertNotEqual(*[item for item in labels if item.startswith("v0102")])
            body = captured[0][2]
            self.assertIn(b'name="name"', body)
            self.assertIn(b"v01020304007", body)
            self.assertIn(b"RIFF-demo", body)
        finally:
            console.VOICE_SETTINGS = old

    def test_delete_preview_and_speak_to_file(self):
        app = console.App.__new__(console.App)
        app.messages = []
        app.say = lambda text, color="#1a6": app.messages.append(text)
        app.voice_list = FakeList()
        app.voice_ids = ["keep", "drop"]
        app.voice_list.pick = (0,)
        app.speed = Var(1.25)
        old = console.VOICE_SETTINGS
        console.VOICE_SETTINGS = os.path.join(self.root, "voice.json")
        write_json(console.VOICE_SETTINGS, {"voice": "keep", "labels": {"keep": "在用", "drop": "旧的"}})
        calls = []
        app.breeze = lambda *args, **kwargs: calls.append(args)
        app.refresh_voices = lambda: calls.append("refresh")
        try:
            with patch.object(console.messagebox, "showinfo") as info:
                app.delete_voice()
            info.assert_called()
            self.assertEqual(calls, [])

            app.voice_list.pick = (1,)
            with patch.object(console.messagebox, "askyesno", return_value=False):
                app.delete_voice()
            self.assertEqual(calls, [])
            with patch.object(console.messagebox, "askyesno", return_value=True):
                app.delete_voice()
            self.assertEqual(calls[0][0], "DELETE")
            self.assertIn("/v1/voices/drop", calls[0][1])
            self.assertNotIn("drop", read_json(console.VOICE_SETTINGS)["labels"])

            class Response(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            out = os.path.join(self.root, "preview.wav")
            with patch.object(console.NO_PROXY, "open", return_value=Response(b"WAVDATA")):
                app.speak_to_file("keep", out, "试听")
            with open(out, "rb") as fh:
                self.assertEqual(fh.read(), b"WAVDATA")
        finally:
            console.VOICE_SETTINGS = old

    def test_log_tail_strips_ansi_and_does_not_stack_timers(self):
        app = console.App.__new__(console.App)
        app.root = FakeRoot()
        app.log_choice = Var("astrbot")
        app.log_text = FakeText(bottom=False)
        old = console.LOGS
        console.LOGS = os.path.join(self.root, "logs")
        os.makedirs(console.LOGS)
        try:
            payload = b"x" * 60000 + "半行不要\n\x1b[31m保留\x1b[0m\n".encode()
            with open(os.path.join(console.LOGS, "astrbot.log"), "wb") as fh:
                fh.write(payload)
            app.refresh_log()
            self.assertIn("保留", app.log_text.content)
            self.assertNotIn("半行不要", app.log_text.content)
            self.assertNotIn("\x1b", app.log_text.content)
            self.assertFalse(app.log_text.seen)
            app.log_text.bottom = True
            app.refresh_log()
            self.assertTrue(app.log_text.seen)
            self.assertEqual(app.root.cancelled, [1])
            self.assertEqual(len(app.root.jobs), 2)
        finally:
            console.LOGS = old

    def test_status_summary_and_close(self):
        app = console.App.__new__(console.App)
        app.dots = {s.key: Label() for s in console.SERVICES}
        app.states = {s.key: Label() for s in console.SERVICES}
        app.qq_label = Label()
        app.summary = Label()
        base = {s.key: False for s in console.SERVICES}
        base.update(connected=False, login={})
        app.show_status(base)
        self.assertEqual(app.summary.conf["text"], "⚪ 已下线")
        self.assertEqual(app.qq_label.conf["text"], "QQ：未连接")

        up = {s.key: True for s in console.SERVICES}
        up.update(connected=False, login={})
        app.show_status(up)
        self.assertEqual(app.states["napcat"].conf["text"], "登录中")
        self.assertEqual(app.summary.conf["text"], "🟠 部分运行")

        up["connected"] = True
        up["login"] = {}
        app.show_status(up)
        self.assertEqual(app.qq_label.conf["text"], "QQ：已连接")
        up["login"] = {"nickname": None, "user_id": 10001}
        app.show_status(up)
        self.assertIn("已登录（10001）", app.qq_label.conf["text"])
        self.assertEqual(app.summary.conf["text"], "🟢 大肥鱼在线")

        app.root = FakeRoot()
        app._log_after = 3
        app._poll_after = 4
        stopped = []
        app.stop_all = lambda: stopped.append(True)
        with patch.object(console.messagebox, "askyesnocancel", return_value=None):
            app.on_close()
        self.assertFalse(app.root.destroyed)
        with patch.object(console.messagebox, "askyesnocancel", return_value=False):
            app.on_close()
        self.assertEqual(stopped, [])
        self.assertEqual(app.root.cancelled, [3, 4])
        self.assertTrue(app.root.destroyed)

    def test_autostart_reports_failure(self):
        app = console.App.__new__(console.App)
        app.messages = []
        app.say = lambda text, color="#1a6": app.messages.append((text, color))
        app.autostart_var = Var(True)
        old = console.STARTUP_LNK
        console.STARTUP_LNK = os.path.join(self.root, "startup.lnk")

        class Result:
            def __init__(self, code):
                self.returncode = code

        try:
            with patch.object(console.subprocess, "run", return_value=Result(1)):
                app.toggle_autostart()
            self.assertFalse(app.autostart_var.get())
            self.assertIn("#c33", app.messages[-1][1])

            app.autostart_var.set(True)
            app.messages.clear()
            with patch.object(console.subprocess, "run", return_value=Result(0)):
                app.toggle_autostart()
            self.assertFalse(app.autostart_var.get())

            app.autostart_var.set(True)

            def ok_run(*args, **kwargs):
                with open(console.STARTUP_LNK, "w", encoding="utf-8") as fh:
                    fh.write("lnk")
                return Result(0)

            with patch.object(console.subprocess, "run", ok_run):
                app.toggle_autostart()
            self.assertTrue(app.autostart_var.get())
            self.assertIn("已设置", app.messages[-1][0])

            app.autostart_var.set(False)
            app.toggle_autostart()
            self.assertFalse(os.path.exists(console.STARTUP_LNK))
        finally:
            console.STARTUP_LNK = old


class LifecycleTests(unittest.TestCase):
    def app(self):
        app = console.App.__new__(console.App)
        app.messages = []
        app.say = lambda text, color="#1a6": app.messages.append(text)
        app.root = FakeRoot()
        app.busy = False
        app.wait_for = lambda check, seconds: check()
        return app

    def test_start_stop_and_restart_order(self):
        app = self.app()
        order = []

        class Svc:
            def __init__(self, key, up=False):
                self.key = key
                self.name = key
                self.up = up

            def running(self):
                return self.up

            def start(self):
                order.append(("start", self.key))
                self.up = True

            def stop(self):
                order.append(("stop", self.key))
                self.up = False

            def connected(self):
                return True

        breeze, helper, astrbot = Svc("breeze"), Svc("helper"), Svc("astrbot")
        nap = console.NapCat.__new__(console.NapCat)
        nap.key, nap.name, nap.up, nap.online = "napcat", "nap", False, False
        nap.running = lambda: nap.up
        nap.start = lambda: order.append(("start", "napcat")) or setattr(nap, "up", True)
        nap.stop = lambda: order.append(("stop", "napcat"))
        nap.connected = lambda: nap.online
        old = console.SERVICES, console.BY_KEY
        console.SERVICES = [breeze, helper, astrbot, nap]
        console.BY_KEY = {s.key: s for s in console.SERVICES}
        try:
            breeze.up = True
            app.start_all()
            self.assertNotIn(("start", "breeze"), order)
            self.assertIn(("start", "helper"), order)
            nap.online = False
            app.start_one(nap)
            self.assertTrue(any("扫码" in text for text in app.messages))
            self.assertTrue(app.root.jobs)
            nap.online = True
            app.messages.clear()
            app.start_all()
            self.assertTrue(any("已上线" in text for text in app.messages))
            order.clear()
            app.stop_all()
            self.assertEqual([name for _, name in order], ["napcat", "astrbot", "helper", "breeze"])
            with patch.object(console.time, "sleep"):
                app.restart_one(helper)
            self.assertIn(("stop", "helper"), order)
            self.assertIn(("start", "helper"), order)
        finally:
            console.SERVICES, console.BY_KEY = old

    def test_run_bg_rejects_overlapping_work_and_reports_errors(self):
        app = self.app()
        gate = threading.Event()
        app.run_bg(lambda: gate.wait(2))
        self.assertTrue(app.busy)
        app.run_bg(lambda: None)
        self.assertTrue(any("上一个操作" in text for text in app.messages))
        gate.set()
        for _ in range(50):
            if not app.busy:
                break
            time.sleep(0.01)
        self.assertFalse(app.busy)

        def boom():
            raise RuntimeError("炸了")

        app.run_bg(boom)
        for _ in range(50):
            if any("炸了" in text for text in app.messages):
                break
            time.sleep(0.01)
        self.assertTrue(any("炸了" in text for text in app.messages))
        self.assertFalse(app.busy)

    def test_login_info_sends_bearer_and_hides_errors(self):
        nap = console.NapCat("napcat", "n", "d")
        seen = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(req, timeout=2):
            seen["header"] = req.get_header("Authorization")
            seen["url"] = req.full_url
            return Response(b'{"data":{"nickname":"\xe5\xa4\xa7\xe8\x82\xa5\xe9\xb1\xbc","user_id":10001}}')

        with patch.object(console, "napcat_token", return_value="tok"), patch.object(console.NO_PROXY, "open", fake_open):
            info = nap.login_info()
        self.assertEqual(seen["header"], "Bearer tok")
        self.assertTrue(seen["url"].endswith("/get_login_info"))
        self.assertEqual(info["user_id"], 10001)
        with patch.object(console.NO_PROXY, "open", side_effect=OSError("down")):
            self.assertEqual(nap.login_info(), {})

    def test_main_explains_bad_config_and_missing_qq(self):
        warnings = []

        class FakeTk:
            def withdraw(self):
                return None

        with patch.object(console.tk, "Tk", FakeTk), patch.object(
            console.messagebox, "showwarning", lambda title, text: warnings.append(text)
        ):
            console.CONFIG_ERROR = "config.json 读不了"
            console.main()
            console.CONFIG_ERROR = None
            gc.collect()
            old = console.BOT_QQ
            console.BOT_QQ = ""
            try:
                console.main()
            finally:
                console.BOT_QQ = old
            gc.collect()
        self.assertTrue(any("读不了" in text for text in warnings))
        self.assertTrue(any("bot_qq" in text for text in warnings))


class WindowSmokeTests(unittest.TestCase):
    def test_window_builds_and_closes_without_stopping_services(self):
        class InlineThread:
            def __init__(self, target=None, daemon=False):
                self.target = target

            def start(self):
                if self.target:
                    self.target()

        root = tk.Tk()
        root.withdraw()
        # 后台线程再去碰已经关掉的窗口，会把 Tcl 整个打崩。构造窗口时改成当场执行。
        with patch.object(console.threading, "Thread", InlineThread):
            app = console.App(root, False)
        root.update_idletasks()
        stopped = []
        app.stop_all = lambda: stopped.append(True)
        with patch.object(console.messagebox, "askyesnocancel", return_value=False):
            app.on_close()
        self.assertEqual(stopped, [])
        try:
            exists = bool(root.winfo_exists())
        except tk.TclError:
            exists = False
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()

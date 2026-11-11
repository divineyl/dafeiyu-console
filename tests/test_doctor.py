"""配置体检的单元测试。用夹具目录，不启动 Breeze、辅助服务、AstrBot 或 NapCat。"""
import ast
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import doctor

FIXTURE = os.path.join(REPO, "tests", "fixtures", "healthy")
BROKEN = os.path.join(REPO, "tests", "fixtures", "broken-config.json")
SENSITIVE_KEY = re.compile(r"(token|password|secret|api_key|authorization|cookie)", re.I)


def snapshot(root):
    found = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            info = os.lstat(path)
            with open(path, "rb") as fh:
                body = fh.read()
            rel = os.path.relpath(path, root)
            found[rel] = (hashlib.sha256(body).hexdigest(), info.st_size, info.st_mtime_ns, stat.S_IMODE(info.st_mode))
    return found


def collect_secrets(root):
    found = []

    def walk(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(key, str) and SENSITIVE_KEY.search(key) and isinstance(value, str) and len(value) >= 8:
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(data, list):
            for item in data:
                walk(item)

    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            try:
                walk(json.loads(text))
            except json.JSONDecodeError:
                for match in re.finditer(
                        r'"(?:[^"\\]*(?:token|password|secret|api_key)[^"\\]*)"\s*:\s*"([^"\\]+)"',
                        text, re.I):
                    if len(match.group(1)) >= 8:
                        found.append(match.group(1))
    return found


def codes(report):
    return [item["code"] for item in report["findings"]]


def levels(report, level):
    return [item for item in report["findings"] if item["level"] == level]


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def materialize(self):
        dst = os.path.join(self.tmp.name, "install")
        shutil.copytree(FIXTURE, dst)
        manifest = os.path.join(dst, "EMPTY_FILES.txt")
        with open(manifest, encoding="utf-8") as fh:
            for line in fh:
                rel = line.strip()
                if not rel or rel.startswith("#"):
                    continue
                path = os.path.join(dst, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if not os.path.exists(path):
                    with open(path, "wb"):
                        pass
        os.remove(manifest)
        return dst

    def load_json(self, root, rel):
        path = os.path.join(root, *rel.split("/"))
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), path

    def save_json(self, path, data):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    def diagnose_clean(self, root, ffmpeg_lookup=None):
        if ffmpeg_lookup is None:
            ffmpeg_lookup = lambda name: "/usr/bin/ffmpeg"
        before = snapshot(root)
        report = doctor.diagnose(root, ffmpeg_lookup=ffmpeg_lookup)
        after = snapshot(root)
        self.assertEqual(before, after, "体检改动了文件")
        text = doctor.format_report(report)
        blob = text + json.dumps(doctor.public_report(report), ensure_ascii=False)
        for secret in collect_secrets(root):
            self.assertNotIn(secret, blob)
        self.assertNotIn("［已隐藏］", blob)
        self.assertIn("没有连接任何地址", text)
        self.assertIn("没有启动、停止或修改文件", text)
        return report, text

    def test_healthy_fixture_is_clean(self):
        seen = []

        def lookup(name):
            seen.append(name)
            if name != "ffmpeg":
                raise AssertionError(name)
            return "/usr/bin/ffmpeg"

        root = self.materialize()
        report, text = self.diagnose_clean(root, ffmpeg_lookup=lookup)
        self.assertEqual(seen, ["ffmpeg"])
        self.assertEqual({item["level"] for item in report["findings"]}, {"ok"})
        self.assertEqual({item["program"] for item in report["findings"]}, set(doctor.PROGRAM_NAMES))
        for port in doctor.PORTS.values():
            self.assertIn(str(port), text)
        self.assertIn("10001", text)
        self.assertIn("四个程序的路径和端口配置对得上", text)
        public = doctor.public_report(report)
        self.assertTrue(public["ok"])
        self.assertEqual(public["error_count"], 0)
        self.assertEqual(public["warn_count"], 0)

    def test_main_text_and_json_exit_codes(self):
        root = self.materialize()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = doctor.main(["--root", root])
        self.assertEqual(code, 0)
        self.assertIn("大肥鱼配置体检", buf.getvalue())

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = doctor.main(["--root", root, "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertTrue(data["ok"])
        self.assertIn("findings", data)

        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = doctor.main(["--root", empty, "--json"])
        self.assertEqual(code, 1)
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertGreaterEqual(data["error_count"], 1)
        self.assertEqual(os.listdir(empty), [])

    def test_missing_config_does_not_create_file(self):
        root = os.path.join(self.tmp.name, "bare")
        os.makedirs(root)
        report, text = self.diagnose_clean(root)
        self.assertIn("config_missing", codes(report))
        self.assertIn("skipped", codes(report))
        self.assertIn("config.json", text)
        self.assertEqual(os.listdir(root), [])
        self.assertFalse(any(item["program"] != "config" for item in report["findings"]))

    def test_broken_config_reports_reason_and_keeps_bytes(self):
        root = self.materialize()
        target = os.path.join(root, "config.json")
        shutil.copyfile(BROKEN, target)
        with open(target, "rb") as fh:
            original = fh.read()
        report, text = self.diagnose_clean(root)
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertIn("config_unreadable", codes(report))
        self.assertIn("skipped", codes(report))
        self.assertIn("不是合法的 JSON", text)
        self.assertIn("原文件没有改", text)
        self.assertNotIn("doctortestbrokenoken01", text)

    def test_unreadable_config_reports_os_error(self):
        if os.geteuid() == 0:
            self.skipTest("root 仍能读取权限为 0 的文件")
        root = self.materialize()
        path = os.path.join(root, "config.json")
        with open(path, "rb") as fh:
            original = fh.read()
        mode = os.stat(path).st_mode
        os.chmod(path, 0)
        try:
            report = doctor.diagnose(root, ffmpeg_lookup=lambda name: "/usr/bin/ffmpeg")
            text = doctor.format_report(report)
        finally:
            os.chmod(path, mode)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertIn("config_unreadable", codes(report))
        self.assertIn("读不了", text)
        self.assertIn("原文件没有改", text)
        self.assertNotIn("doctortesthttptoken0001", text)

    def test_bad_bot_qq_is_not_used_as_a_path(self):
        root = self.materialize()
        secret = "doctortesttraversesecret"
        leaked = os.path.join(root, "leaked.json")
        with open(leaked, "w", encoding="utf-8") as fh:
            json.dump({"token": secret}, fh)
        cfg, path = self.load_json(root, "config.json")
        cfg["bot_qq"] = "../../../../leaked"
        self.save_json(path, cfg)
        report, text = self.diagnose_clean(root)
        self.assertIn("bot_qq_invalid", codes(report))
        self.assertIn("onebot_skipped", codes(report))
        self.assertNotIn(secret, text)
        self.assertNotIn("../../../../leaked", text)
        self.assertNotIn("leaked.json", text)

    def test_model_name_cannot_leave_models_dir(self):
        root = self.materialize()
        outside = os.path.join(root, "breeze-tts", "secret.gguf")
        with open(outside, "wb") as fh:
            fh.write(b"doctortestmodelbody01")
        cfg, path = self.load_json(root, "config.json")
        cfg["breeze_model"] = "../secret.gguf"
        self.save_json(path, cfg)
        report, text = self.diagnose_clean(root)
        self.assertIn("model_unsafe", codes(report))
        self.assertNotIn("secret.gguf", text)
        self.assertNotIn("doctortestmodelbody01", text)
        self.assertTrue(os.path.isfile(outside))

    def test_symlink_outside_onebot_is_not_read(self):
        root = self.materialize()
        outside = tempfile.mkdtemp(dir=self.tmp.name)
        secret = "doctortestoutsidesecret"
        target = os.path.join(outside, "onebot11_10001.json")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump({"token": secret, "network": {"httpServers": [{"token": secret}]}}, fh)
        link = os.path.join(root, "napcat", "config", "onebot11_10001.json")
        os.remove(link)
        try:
            os.symlink(target, link)
        except OSError:
            self.skipTest("当前环境不能建符号链接")
        with open(target, "rb") as fh:
            before = fh.read()
        report, text = self.diagnose_clean(root)
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertIn("onebot_outside", codes(report))
        self.assertNotIn(secret, text)

    def test_remote_and_public_addresses_are_not_contacted(self):
        root = self.materialize()
        cmd, cmd_path = self.load_json(root, "astrbot/data/cmd_config.json")
        cmd["dashboard"]["host"] = "0.0.0.0"
        cmd["platform"][0]["ws_reverse_host"] = "198.51.100.8"
        cmd["provider"][0]["api_base"] = "http://user:doctortestuserinfo01@203.0.113.9:8138/v1"
        self.save_json(cmd_path, cmd)
        ob, ob_path = self.load_json(root, "napcat/config/onebot11_10001.json")
        ob["network"]["websocketClients"][0]["url"] = "ws://198.51.100.8:6199/ws?access_token=doctortestquerysecret"
        ob["network"]["httpServers"][0]["host"] = "0.0.0.0"
        self.save_json(ob_path, ob)
        webui, webui_path = self.load_json(root, "napcat/config/webui.json")
        webui["host"] = "0.0.0.0"
        self.save_json(webui_path, webui)

        def boom(*_args, **_kwargs):
            raise AssertionError("体检去连接了")

        with mock.patch("socket.socket", boom), \
                mock.patch("socket.create_connection", boom), \
                mock.patch("urllib.request.urlopen", boom), \
                mock.patch("urllib.request.Request", boom):
            report, text = self.diagnose_clean(root)
        self.assertIn("dashboard_host", codes(report))
        self.assertIn("ws_host", codes(report))
        self.assertIn("tts_hidden", codes(report))
        self.assertIn("ws_hidden", codes(report))
        self.assertIn("http_host", codes(report))
        self.assertIn("webui_host", codes(report))
        self.assertIn("198.51.100.8", text)
        self.assertIn("0.0.0.0", text)
        self.assertNotIn("doctortestuserinfo01", text)
        self.assertNotIn("doctortestquerysecret", text)
        self.assertNotIn("access_token", text)

    def test_port_mismatch_between_astrbot_and_napcat(self):
        root = self.materialize()
        cmd, path = self.load_json(root, "astrbot/data/cmd_config.json")
        cmd["platform"][0]["ws_reverse_port"] = 7000
        cmd["provider"][0]["api_base"] = "http://127.0.0.1:9000/v1"
        self.save_json(path, cmd)
        ob, ob_path = self.load_json(root, "napcat/config/onebot11_10001.json")
        ob["network"]["httpServers"][0]["port"] = 3001
        self.save_json(ob_path, ob)
        report, text = self.diagnose_clean(root)
        self.assertIn("ws_port", codes(report))
        self.assertIn("tts_port", codes(report))
        self.assertIn("http_port", codes(report))
        self.assertIn("7000", text)
        self.assertIn("9000", text)
        self.assertIn("3001", text)
        self.assertIn("6199", text)
        self.assertIn("8138", text)
        self.assertIn("3000", text)

    def test_token_mismatch_is_not_printed(self):
        root = self.materialize()
        ob, path = self.load_json(root, "napcat/config/onebot11_10001.json")
        ob["network"]["websocketClients"][0]["token"] = "doctortestwstoken999999"
        ob["network"]["httpServers"][0]["token"] = "doctortesthttptoken9999"
        self.save_json(path, ob)
        report, text = self.diagnose_clean(root)
        self.assertIn("ws_token_mismatch", codes(report))
        self.assertIn("http_token_mismatch", codes(report))
        self.assertIn("不一致", text)
        self.assertNotIn("doctortestwstoken999999", text)
        self.assertNotIn("doctortesthttptoken9999", text)
        self.assertNotIn("doctortestwstoken000001", text)
        self.assertNotIn("doctortesthttptoken0001", text)

    def test_unsafe_token_is_not_printed(self):
        root = self.materialize()
        ob, path = self.load_json(root, "napcat/config/onebot11_10001.json")
        ob["network"]["websocketClients"][0]["token"] = "bad token\nInjected: doctortestevilheader"
        self.save_json(path, ob)
        report, text = self.diagnose_clean(root)
        self.assertIn("ws_token_unsafe", codes(report))
        self.assertNotIn("bad token", text)
        self.assertNotIn("Injected", text)
        self.assertNotIn("doctortestevilheader", text)
        self.assertIn("不写原文", text)

    def test_missing_programs_are_named(self):
        root = self.materialize()
        os.remove(os.path.join(root, "qq", "QQ.exe"))
        os.remove(os.path.join(root, "breeze-tts", "bin", "win", "breeze-server.exe"))
        report, text = self.diagnose_clean(root)
        self.assertIn("qq_missing", codes(report))
        self.assertIn("server_missing", codes(report))
        self.assertIn("QQ", text)
        self.assertIn("breeze-server.exe", text)

    def test_voice_settings_bad_json_speed_and_voice(self):
        root = self.materialize()
        path = os.path.join(root, "voice-settings.json")
        with open(path, "rb") as fh:
            original = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"voice": "dafeiyu", "speed": ')
        with open(path, "rb") as fh:
            broken = fh.read()
        report, text = self.diagnose_clean(root)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), broken)
        self.assertNotEqual(broken, original)
        self.assertIn("settings_unreadable", codes(report))
        self.assertIn("原文件没有改", text)

        self.save_json(path, {"voice": "../secret", "speed": "not-a-number"})
        report, text = self.diagnose_clean(root)
        self.assertIn("settings_voice", codes(report))
        self.assertIn("settings_speed", codes(report))
        self.assertNotIn("../secret", text)
        self.assertNotIn("not-a-number", text)

        self.save_json(path, {"voice": "dafeiyu", "speed": 9})
        report, _text = self.diagnose_clean(root)
        self.assertIn("settings_speed", codes(report))

    def test_helper_public_bind_and_remote_breeze(self):
        root = self.materialize()
        path = os.path.join(root, "helper.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read().replace('"127.0.0.1"', '"0.0.0.0"', 1)
        text = text.replace('BREEZE = "http://127.0.0.1:8137"', 'BREEZE = "http://203.0.113.9:8137"')
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

        def boom(*_args, **_kwargs):
            raise AssertionError("体检去连接了")

        with mock.patch("socket.socket", boom), mock.patch("urllib.request.urlopen", boom):
            report, shown = self.diagnose_clean(root)
        self.assertIn("bind_host", codes(report))
        self.assertIn("breeze_host", codes(report))
        self.assertIn("0.0.0.0", shown)
        self.assertIn("203.0.113.9", shown)
        self.assertIn("没有连接", shown)

    def test_warnings_do_not_fail_the_run(self):
        root = self.materialize()
        shutil.rmtree(os.path.join(root, "memes"))
        os.remove(os.path.join(root, "napcat", "config", "webui.json"))
        report, text = self.diagnose_clean(root, ffmpeg_lookup=lambda name: None)
        self.assertEqual(levels(report, "error"), [])
        self.assertIn("memes_missing", codes(report))
        self.assertIn("webui_missing", codes(report))
        self.assertIn("ffmpeg_missing", codes(report))
        self.assertIn("没有硬伤", text)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = doctor.main(["--root", root])
        self.assertEqual(code, 0)
        self.assertEqual(doctor.public_report(report)["error_count"], 0)
        self.assertIn("注意", buf.getvalue())

    def test_wrong_types_do_not_crash(self):
        root = self.materialize()
        cfg, path = self.load_json(root, "config.json")
        cfg["qq_exe"] = 12
        cfg["bot_qq"] = True
        self.save_json(path, cfg)
        with open(os.path.join(root, "astrbot", "data", "cmd_config.json"), "w", encoding="utf-8") as fh:
            fh.write("[]\n")
        report, text = self.diagnose_clean(root)
        self.assertIn("qq_missing", codes(report))
        self.assertIn("bot_qq_invalid", codes(report))
        self.assertIn("cmd_type", codes(report))
        self.assertIn("文字路径", text)

    def test_numeric_bot_qq_and_trimmed_text(self):
        root = self.materialize()
        cfg, path = self.load_json(root, "config.json")
        cfg["bot_qq"] = 10001
        cfg["owner_qq"] = " 10002 "
        self.save_json(path, cfg)
        report, _text = self.diagnose_clean(root)
        self.assertEqual(levels(report, "error"), [])
        self.assertNotIn("bot_qq_invalid", codes(report))

    def test_same_report_twice(self):
        root = self.materialize()
        first, _text = self.diagnose_clean(root)
        second, _text = self.diagnose_clean(root)
        self.assertEqual(first["findings"], second["findings"])

    def test_repo_helper_ports_match(self):
        findings = []
        ok = doctor.inspect_helper_source(os.path.join(REPO, "helper.py"), REPO, findings)
        self.assertTrue(ok, findings)
        self.assertEqual(levels({"findings": findings}, "error"), [])

    def test_port_table_matches_current_sources(self):
        with open(os.path.join(REPO, "dafeiyu_console.pyw"), encoding="utf-8") as fh:
            console = fh.read()
        with open(os.path.join(REPO, "helper.py"), encoding="utf-8") as fh:
            helper = fh.read()
        with open(os.path.join(REPO, "tools", "setup.py"), encoding="utf-8") as fh:
            setup = fh.read()
        self.assertEqual(doctor.PORTS["breeze"], 8137)
        self.assertEqual(doctor.PORTS["helper_voice"], 8138)
        self.assertEqual(doctor.PORTS["helper_meme"], 18765)
        self.assertEqual(doctor.PORTS["astrbot_dashboard"], 6185)
        self.assertEqual(doctor.PORTS["astrbot_ws"], 6199)
        self.assertEqual(doctor.PORTS["napcat_http"], 3000)
        self.assertIn("http://127.0.0.1:8137", console)
        self.assertIn("port_open(18765)", console)
        self.assertIn("port_open(8138)", console)
        self.assertIn("port_open(6185)", console)
        self.assertIn("127.0.0.1:3000", console)
        self.assertIn("6199", console)
        self.assertIn('BREEZE = "http://127.0.0.1:8137"', helper)
        self.assertIn("18765", helper)
        self.assertIn("8138", helper)
        self.assertIn('"ws_reverse_port": 6199', setup)
        self.assertIn('"port": 3000', setup)
        self.assertIn("http://127.0.0.1:8138/v1", setup)

    def test_defaults_match_example_config(self):
        with open(os.path.join(REPO, "config.example.json"), encoding="utf-8") as fh:
            example = json.load(fh)
        for key in ("qq_exe", "astrbot_exe", "astrbot_dir", "napcat_dir", "breeze_dir", "breeze_model", "icon"):
            self.assertEqual(doctor.DEFAULTS[key], example[key])

    def test_doctor_source_does_not_import_network_or_process_tools(self):
        banned = (
            "socket", "subprocess", "http", "requests", "psutil", "tkinter", "webbrowser",
            "threading", "asyncio", "urllib.request", "urllib.error",
        )
        with open(doctor.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        def rejected(name):
            return any(name == item or name.startswith(item + ".") for item in banned)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(rejected(alias.name), alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse(rejected(node.module or ""), node.module)

    def test_help_describes_a_read_only_check(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                doctor.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("不连接", buf.getvalue())

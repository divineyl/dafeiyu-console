"""导入配置包：自动回答，密码不落盘，表情包改走本机。"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout


def load_import_pack():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "import_pack.py")
    spec = importlib.util.spec_from_file_location("dafeiyu_import_pack", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = load_import_pack()


FAKE_TOOL = '''
import json
import os
import sys

GH_ACCELERATORS = ["https://gh.dpik.top/", "", "http://127.0.0.1:18765/"]
ASKED = []

def ask(name, prompt, default=None, secret=False, choices=None):
    ASKED.append(name)
    if name == "password":
        raise AssertionError("password should be handled by import_pack")
    return default

def main():
    mode = os.environ.get("FAKE_IMPORT_MODE", "ok")
    path = sys.argv[sys.argv.index("--answers") + 1]
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    report = {
        "saved": saved,
        "accelerators": list(GH_ACCELERATORS),
        "cwd": os.getcwd(),
        "argv": list(sys.argv),
        "asked": list(ASKED),
    }
    if mode == "ask":
        report["password"] = ask("password", "pw", secret=True)
        report["base"] = ask("base_url", "url", default="from-default")
    with open(os.environ["FAKE_IMPORT_REPORT"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False)
    if mode == "delete-and-fail":
        os.remove(path)
        raise RuntimeError("导入失败")
    if mode == "interrupt":
        raise KeyboardInterrupt
'''


class ImportPackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        pack = os.path.join(self.root, "pack")
        os.makedirs(os.path.join(pack, "tools"))
        with open(os.path.join(pack, "tools", "import_dafeiyu.py"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_TOOL)
        self.report = os.path.join(self.root, "report.json")
        self.old_env = {
            "FAKE_IMPORT_MODE": os.environ.get("FAKE_IMPORT_MODE"),
            "FAKE_IMPORT_REPORT": os.environ.get("FAKE_IMPORT_REPORT"),
        }
        os.environ["FAKE_IMPORT_REPORT"] = self.report
        os.environ["FAKE_IMPORT_MODE"] = "ok"
        self.passwords = []
        self.old_getpass = importer.getpass.getpass
        importer.getpass.getpass = self.fake_getpass

    def tearDown(self):
        importer.getpass.getpass = self.old_getpass
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def fake_getpass(self, prompt):
        self.assertIn("密码", prompt)
        if not self.passwords:
            return ""
        return self.passwords.pop(0)

    def write_config(self, data):
        with open(os.path.join(self.root, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_answers_password_and_local_meme_source(self):
        self.write_config({
            "owner_qq": " 20002 ",
            "config_pack_dir": "pack",
            "astrbot_username": None,
            "import_memory": None,
            "import_voice": 0,
        })
        self.passwords[:] = ["", "secret"]
        os.environ["FAKE_IMPORT_MODE"] = "ask"
        cwd = os.getcwd()
        with redirect_stdout(io.StringIO()):
            importer.main(self.root)
        self.assertEqual(os.getcwd(), cwd)
        self.assertFalse(os.path.exists(os.path.join(self.root, "logs", "import-answers.json")))
        with open(self.report, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["saved"]["owner_qq"], "20002")
        self.assertEqual(report["saved"]["username"], "astrbot")
        self.assertEqual(report["saved"]["memory"], "0")
        self.assertEqual(report["saved"]["voice"], "0")
        self.assertNotIn("password", report["saved"])
        self.assertEqual(report["password"], "secret")
        self.assertEqual(report["base"], "from-default")
        self.assertEqual(report["accelerators"][0], "http://127.0.0.1:18765/")
        self.assertEqual(report["accelerators"].count("http://127.0.0.1:18765/"), 1)
        self.assertEqual(report["accelerators"][-1], "")
        self.assertIn("https://gh.dpik.top/", report["accelerators"])
        self.assertEqual(report["cwd"], os.path.join(self.root, "pack"))
        self.assertEqual(report["argv"][1:], ["--answers", os.path.join(self.root, "logs", "import-answers.json")])

    def test_failure_still_deletes_answers_and_keeps_the_error(self):
        self.write_config({"owner_qq": 30003, "config_pack_dir": "pack"})
        os.environ["FAKE_IMPORT_MODE"] = "delete-and-fail"
        with self.assertRaises(RuntimeError):
            importer.main(self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "logs", "import-answers.json")))

    def test_keyboard_interrupt_is_a_clean_cancel(self):
        self.write_config({"owner_qq": "1", "config_pack_dir": "pack"})
        os.environ["FAKE_IMPORT_MODE"] = "interrupt"
        buf = io.StringIO()
        with redirect_stdout(buf):
            importer.main(self.root)
        self.assertIn("已取消", buf.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.root, "logs", "import-answers.json")))

    def test_missing_pack_owner_and_bad_config_exit(self):
        with self.assertRaises(SystemExit) as raised:
            importer.main(self.root)
        self.assertIn("config.json", str(raised.exception))

        with open(os.path.join(self.root, "config.json"), "w", encoding="utf-8") as fh:
            fh.write("[")
        with self.assertRaises(SystemExit) as raised:
            importer.main(self.root)
        self.assertIn("读不了", str(raised.exception))

        self.write_config({"owner_qq": "1"})
        with self.assertRaises(SystemExit) as raised:
            importer.main(self.root)
        self.assertIn("没找到配置包", str(raised.exception))

        self.write_config({"owner_qq": None, "config_pack_dir": "pack"})
        with self.assertRaises(SystemExit) as raised:
            importer.main(self.root)
        self.assertIn("owner_qq", str(raised.exception))

        self.write_config([])
        with self.assertRaises(SystemExit) as raised:
            importer.main(self.root)
        self.assertIn("JSON 对象", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

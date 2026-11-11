"""运行 dafeiyu-config-pack 的导入工具，除了 AstrBot 后台密码，其余问题都按 config.json 自动回答。

和直接双击配置包里的「导入大肥鱼.bat」相比，多了两件事：
  - 表情包先从辅助服务（本机 18765 端口，取 memes\\ 里提前下好的压缩包）拿，几秒装完，不用等 GitHub
  - 主人 QQ、记忆、语音这些选项不用一个个敲

用法：先用控制台启动 AstrBot 和辅助服务，再运行
  python tools\\import_pack.py
密码只在这个窗口里输一次，不会保存。
"""
import getpass
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
if not os.path.isfile(CONFIG_PATH):
    sys.exit("没找到 config.json。先把 config.example.json 复制成 config.json，填好 owner_qq。")
try:
    with open(CONFIG_PATH, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)
except (OSError, json.JSONDecodeError) as e:
    sys.exit(f"读不了 config.json：{e}")
if not isinstance(cfg, dict):
    sys.exit("config.json 的最外层必须是一组配置项。")
pack_dir = cfg.get("config_pack_dir") or "dafeiyu-config-pack"
if not isinstance(pack_dir, str) or not pack_dir.strip():
    pack_dir = "dafeiyu-config-pack"
PACK = os.path.join(ROOT, os.path.expanduser(pack_dir))
if not os.path.isfile(os.path.join(PACK, "tools", "import_dafeiyu.py")):
    sys.exit(f"没找到配置包：{PACK}\n先 git clone https://github.com/zexuanw958-svg/dafeiyu-config-pack 到这里，"
             "或在 config.json 的 config_pack_dir 里写它的位置。")
owner_qq = str(cfg.get("owner_qq", "")).strip()
if not owner_qq.isdigit() or not 4 <= len(owner_qq) <= 20:
    sys.exit("config.json 里的 owner_qq（你自己的大号，她认作主人）只能填写数字 QQ 号。")

sys.path.insert(0, os.path.join(PACK, "tools"))
os.chdir(PACK)
import import_dafeiyu as imp  # noqa: E402

answers = {
    "base_url": "http://127.0.0.1:6185",
    "username": cfg.get("astrbot_username") or "astrbot",
    "owner_qq": owner_qq,
    "memory": str(cfg.get("import_memory", "0")),
    "voice": str(cfg.get("import_voice", "1")),
}
answers_path = os.path.join(ROOT, "logs", "import-answers.json")
os.makedirs(os.path.dirname(answers_path), exist_ok=True)
# 答案里有 QQ 号。写完就给导入工具用，结束时删掉；权限收紧到当前用户。
fd = os.open(answers_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(answers, fh, ensure_ascii=False)

try:
    _ask = imp.ask

    def ask(name, prompt, default=None, secret=False, choices=None):
        if name == "password":
            while True:
                value = getpass.getpass("请输入 AstrBot 后台密码（输入时不显示，输完按回车）：").strip()
                if value:
                    return value
        return _ask(name, prompt, default, secret, choices)

    imp.ask = ask
    # 本机的表情包源排第一；导入工具发现插件里现在的加速地址在列表里，就会换成第一个
    imp.GH_ACCELERATORS = ["http://127.0.0.1:18765/"] + [a for a in imp.GH_ACCELERATORS if a] + [""]
    sys.argv = [sys.argv[0], "--answers", answers_path]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    imp.main()
except KeyboardInterrupt:
    print("\n已取消")
finally:
    try:
        os.remove(answers_path)
    except OSError:
        pass

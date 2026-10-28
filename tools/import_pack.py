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
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8-sig"))
PACK = os.path.join(ROOT, os.path.expanduser(cfg.get("config_pack_dir") or "dafeiyu-config-pack"))
if not os.path.exists(os.path.join(PACK, "tools", "import_dafeiyu.py")):
    sys.exit(f"没找到配置包：{PACK}\n先 git clone https://github.com/zexuanw958-svg/dafeiyu-config-pack 到这里，"
             "或在 config.json 的 config_pack_dir 里写它的位置。")
if not str(cfg.get("owner_qq", "")).strip():
    sys.exit("config.json 里的 owner_qq（你自己的大号，她认作主人）还没填。")

sys.path.insert(0, os.path.join(PACK, "tools"))
os.chdir(PACK)
import import_dafeiyu as imp  # noqa: E402

answers = {
    "base_url": "http://127.0.0.1:6185",
    "username": cfg.get("astrbot_username") or "astrbot",
    "owner_qq": str(cfg["owner_qq"]).strip(),
    "memory": str(cfg.get("import_memory", "0")),
    "voice": str(cfg.get("import_voice", "1")),
}
answers_path = os.path.join(ROOT, "logs", "import-answers.json")
os.makedirs(os.path.dirname(answers_path), exist_ok=True)
json.dump(answers, open(answers_path, "w", encoding="utf-8"), ensure_ascii=False)

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
try:
    imp.main()
except KeyboardInterrupt:
    print("\n已取消")
finally:
    os.remove(answers_path)

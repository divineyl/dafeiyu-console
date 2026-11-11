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


def main(root=None):
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(root, "config.json")
    try:
        with open(cfg_path, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        sys.exit("没找到 config.json。先复制 config.example.json，填好后再运行。")
    except (OSError, ValueError) as e:
        sys.exit(f"config.json 读不了：{e}")
    if not isinstance(cfg, dict):
        sys.exit("config.json 必须是 JSON 对象。")

    pack = os.path.join(root, os.path.expanduser(str(cfg.get("config_pack_dir") or "dafeiyu-config-pack")))
    if not os.path.exists(os.path.join(pack, "tools", "import_dafeiyu.py")):
        sys.exit(f"没找到配置包：{pack}\n先 git clone https://github.com/zexuanw958-svg/dafeiyu-config-pack 到这里，"
                 "或在 config.json 的 config_pack_dir 里写它的位置。")
    owner = cfg.get("owner_qq")
    if owner is None or not str(owner).strip():
        sys.exit("config.json 里的 owner_qq（你自己的大号，她认作主人）还没填。")

    tool_path = os.path.join(pack, "tools")
    sys.path.insert(0, tool_path)
    previous_cwd = os.getcwd()
    previous_argv = sys.argv
    answers_path = os.path.join(root, "logs", "import-answers.json")
    os.chdir(pack)
    try:
        sys.modules.pop("import_dafeiyu", None)
        import import_dafeiyu as imp  # noqa: E402

        def text(key, default):
            value = cfg.get(key, default)
            if value is None or not str(value).strip():
                value = default
            return str(value).strip()

        answers = {
            "base_url": "http://127.0.0.1:6185",
            "username": text("astrbot_username", "astrbot"),
            "owner_qq": str(owner).strip(),
            "memory": text("import_memory", "0"),
            "voice": text("import_voice", "1"),
        }
        os.makedirs(os.path.dirname(answers_path), exist_ok=True)
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump(answers, fh, ensure_ascii=False)

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
        local = "http://127.0.0.1:18765/"
        current = list(getattr(imp, "GH_ACCELERATORS", []))
        imp.GH_ACCELERATORS = [local] + [a for a in current if a and a != local] + [""]
        sys.argv = [sys.argv[0], "--answers", answers_path]
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        imp.main()
    except KeyboardInterrupt:
        print("\n已取消")
    finally:
        sys.argv = previous_argv
        os.chdir(previous_cwd)
        try:
            sys.path.remove(tool_path)
        except ValueError:
            pass
        try:
            os.remove(answers_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()

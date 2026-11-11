"""大肥鱼配置体检。

看四个程序：Breeze 语音、辅助服务、AstrBot、QQ/NapCat。
只核对三件事：配置的路径在不在、配置文件读不读得开、端口和地址是不是还对着本机同一套。

这是一份报告，不是看守程序。不启动、不停止、不改任何文件，
也不连接配置里写到的地址（本机端口也不连，避免把别的程序当成它们）。

用法（在安装目录里）：
  python tools/doctor.py
  python tools/doctor.py --json
"""
import argparse
import hmac
import json
import os
import re
import shutil
import sys
from urllib.parse import urlparse  # 只拆开文字里的地址，不会发起连接

# 和 dafeiyu_console.pyw、helper.py、tools/setup.py 里写死的本机端口一致。
PORTS = {
    "breeze": 8137,
    "helper_voice": 8138,
    "helper_meme": 18765,
    "astrbot_dashboard": 6185,
    "astrbot_ws": 6199,
    "napcat_http": 3000,
}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
WIDE_OPEN = {"0.0.0.0", "::", "[::]"}
# 控制台 DEFAULTS 里会拿来拼路径的那些项；缺了就按这个在内存里补，不写回文件。
DEFAULTS = {
    "bot_qq": "",
    "owner_qq": "",
    "napcat_http_token": "",
    "qq_exe": r"C:\Program Files\Tencent\QQNT\QQ.exe",
    "astrbot_exe": r"~\.local\bin\astrbot.exe",
    "astrbot_dir": "astrbot",
    "napcat_dir": "napcat",
    "breeze_dir": "breeze-tts",
    "breeze_model": "breeze-tts-2-q8_0.gguf",
    "config_pack_dir": "dafeiyu-config-pack",
    "icon": "dafeiyu.ico",
}
NAPCAT_FILES = (
    ("NapCatWinBootMain.exe", "启动 QQ 时要用"),
    ("NapCatWinBootHook.dll", "注入 QQ 时要用"),
    ("loadNapCat.js", "加载 NapCat 时要用"),
    ("napcat.mjs", "NapCat 主程序"),
    ("qqnt.json", "补丁包路径"),
)
PROGRAM_NAMES = {
    "config": "总配置",
    "breeze": "Breeze 语音",
    "helper": "辅助服务",
    "astrbot": "AstrBot",
    "napcat": "QQ / NapCat",
}
SENSITIVE_KEY = re.compile(r"(token|password|secret|api_key|authorization|cookie)", re.I)
QQ_ID = re.compile(r"[0-9]{4,20}")
MODEL_NAME = re.compile(r"[A-Za-z0-9._-]{1,120}")
TOKEN_SAFE = re.compile(r"[A-Za-z0-9]{8,128}")
VOICE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_JSON_BYTES = 2 * 1024 * 1024
HELPER_BIND = re.compile(r"""ThreadingHTTPServer\(\(\s*(['"])([^'"]+)\1\s*,""")
HELPER_PORT = re.compile(r"(?:serve\(\s*|args=\()\s*(\d{2,5})\b")
HELPER_BREEZE = re.compile(r"""^BREEZE\s*=\s*(['"])([^'"]*)\1""", re.M)


def add(findings, level, program, code, message):
    findings.append({"level": level, "program": program, "code": code, "message": message})


class SecretGuard:
    """记下读到的密钥，最后从报告句子里抹掉。抹掉时不把原文写进异常。"""

    def __init__(self):
        self._values = []

    def note(self, value):
        if not isinstance(value, str):
            return
        value = value.strip()
        if len(value) < 8 or value.isdigit() or value.lower() in LOOPBACK:
            return
        if value not in self._values:
            self._values.append(value)

    def note_tree(self, data, depth=0):
        if depth > 12:
            return
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(key, str) and SENSITIVE_KEY.search(key):
                    self.note(value)
                self.note_tree(value, depth + 1)
        elif isinstance(data, list):
            for item in data:
                self.note_tree(item, depth + 1)

    def scrub(self, message):
        for secret in sorted(self._values, key=len, reverse=True):
            if secret in message:
                message = message.replace(secret, "［已隐藏］")
        return message


def install_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def display_path(root, path):
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path
    if rel.startswith(".."):
        return path
    return rel


def read_json(path, root):
    """只读。失败时说明原因，调用方不能据此去写文件。"""
    shown = display_path(root, path)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_JSON_BYTES + 1)
    except OSError as e:
        reason = e.strerror or "读失败"
        return None, f"读不了 {shown}：{reason}。原文件没有改。"
    if len(raw) > MAX_JSON_BYTES:
        return None, f"{shown} 超过 2MB，没有继续解析。原文件没有改。"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, f"{shown} 不是 UTF-8 文本。原文件没有改。"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"{shown} 不是合法的 JSON（第 {e.lineno} 行：{e.msg}）。原文件没有改。"


def stays_within(root_dir, candidate):
    """真实路径必须还在 root_dir 里面。符号链接指到外面时返回 False，调用方就不要打开。"""
    try:
        root_real = os.path.realpath(root_dir)
        cand_real = os.path.realpath(candidate)
        return os.path.commonpath([root_real, cand_real]) == root_real
    except (OSError, ValueError):
        return False


def resolve_path(root, value):
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or "\x00" in raw:
        return None
    raw = os.path.expanduser(raw)
    if os.path.isabs(raw) or re.match(r"^[A-Za-z]:[\\/]", raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(root, raw))


def is_loopback(host):
    if not isinstance(host, str):
        return False
    return host.lower().strip("[]") in LOOPBACK


def short_host(host):
    if not isinstance(host, str):
        return None
    host = host.strip()
    if not host or len(host) > 80 or re.search(r"[\s@/?#]", host):
        return None
    return host


def as_port(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 1 <= value <= 65535:
        return value
    if isinstance(value, str) and value.isdigit():
        number = int(value)
        if 1 <= number <= 65535:
            return number
    return None


def parse_endpoint(url):
    """拆地址。地址里如果夹了账号、问号参数，就不把原文交回去。"""
    if not isinstance(url, str) or not url.strip():
        return "empty"
    if any(ch in url for ch in "\r\n\x00"):
        return "hidden"
    parts = urlparse(url.strip())
    if parts.username or parts.password or parts.query or parts.fragment:
        return "hidden"
    if parts.scheme not in {"http", "https", "ws", "wss"} or not parts.hostname:
        return "bad"
    return {
        "scheme": parts.scheme,
        "host": parts.hostname,
        "port": parts.port,
        "path": parts.path or "",
    }


def check_loopback(findings, program, code, label, host):
    if host is None or (isinstance(host, str) and not host.strip()):
        add(findings, "error", program, code, f"{label}没有写。应该是 127.0.0.1。体检没有连接。")
        return False
    if is_loopback(host):
        return True
    if isinstance(host, str) and host.strip("[]").lower() in WIDE_OPEN:
        shown = host.strip("[]")
        add(findings, "error", program, code,
            f"{label}绑在所有网卡（{shown}）。应该只听 127.0.0.1。体检没有连接它。")
        return False
    shown = short_host(host)
    if shown:
        add(findings, "error", program, code,
            f"{label}指向 {shown}，不是本机。体检没有连接它。")
    else:
        add(findings, "error", program, code,
            f"{label}不是本机地址。体检没有连接它，也不把原样写进报告。")
    return False


def check_port(findings, program, code, label, actual, expected):
    if actual is None:
        add(findings, "error", program, code, f"{label}没有写有效端口，应该是 {expected}。")
        return False
    if actual != expected:
        add(findings, "error", program, code, f"{label}端口是 {actual}，应该是 {expected}。")
        return False
    return True


def token_status(value):
    """返回 missing / unsafe / ok。ok 时第二个值只在内存里比较，不能写进报告。"""
    if not isinstance(value, str) or not value.strip():
        return "missing", None
    if TOKEN_SAFE.fullmatch(value.strip()) is None:
        return "unsafe", None
    return "ok", value.strip()


def classify_qq(findings, field, value, required, label):
    if isinstance(value, bool):
        add(findings, "error", "config", f"{field}_invalid", f"{label}只能是 4 到 20 位数字。")
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    elif value is None or value == "":
        text = ""
    else:
        add(findings, "error", "config", f"{field}_type", f"{label}应该是数字。")
        return None
    if text == "":
        level = "error" if required else "warn"
        code = f"{field}_missing"
        if required:
            message = f"还没填{label}。把 config.example.json 复制成 config.json 后填上。"
        else:
            message = f"还没填{label}。导入配置包时会用到，不影响这四个程序启动。"
        add(findings, level, "config", code, message)
        return None
    if QQ_ID.fullmatch(text) is None:
        add(findings, "error", "config", f"{field}_invalid", f"{label}只能是 4 到 20 位数字。")
        return None
    return text


def require_path(findings, program, code, root, value, kind, label):
    path = resolve_path(root, value)
    if path is None:
        add(findings, "error", program, code, f"{label}应该是文字路径。")
        return None
    if kind == "file" and os.path.isfile(path):
        return path
    if kind == "dir" and os.path.isdir(path):
        return path
    add(findings, "error", program, code, f"找不到{label}：{path}")
    return None


def finish(findings, program, ok_message):
    if ok_message and not any(item["program"] == program and item["level"] == "error" for item in findings):
        add(findings, "ok", program, "ok", ok_message)


def scrub_findings(findings, guard):
    for item in findings:
        item["message"] = guard.scrub(item["message"])


def check_breeze(root, cfg, findings):
    breeze_dir = require_path(findings, "breeze", "dir_missing", root, cfg.get("breeze_dir"), "dir", "Breeze 目录")
    if breeze_dir is None:
        return "Breeze 的目录、程序和模型都在。"
    model = cfg.get("breeze_model")
    model_path = None
    if not isinstance(model, str) or MODEL_NAME.fullmatch(model) is None or model in {".", ".."}:
        add(findings, "error", "breeze", "model_unsafe", "模型名只能是文件名，不能带目录。没有去 models 外面找。")
    else:
        models = os.path.join(breeze_dir, "models")
        model_path = os.path.join(models, model)
        if not stays_within(models, model_path):
            add(findings, "error", "breeze", "model_outside", "模型路径跑到了 models 目录外面，没有打开。")
        elif not os.path.isfile(model_path):
            add(findings, "error", "breeze", "model_missing", f"找不到语音模型：{model_path}")
    server = find_breeze_server(breeze_dir, findings)
    voices = os.path.join(breeze_dir, "voices")
    if not os.path.isdir(voices):
        add(findings, "warn", "breeze", "voices_missing", "没有 voices 目录。换声音时会用到，不影响先看配置。")
    if server and model_path and os.path.isfile(model_path):
        return f"找到语音程序和模型，端口按本机 {PORTS['breeze']} 这套来（这次没有连接）。"
    return "Breeze 的目录、程序和模型都在。"


def find_breeze_server(breeze_dir, findings):
    bin_dir = os.path.join(breeze_dir, "bin")
    if not os.path.isdir(bin_dir):
        add(findings, "error", "breeze", "server_missing", "没有 bin 目录，里面应该有 breeze-server.exe。")
        return None
    if not stays_within(breeze_dir, bin_dir):
        add(findings, "error", "breeze", "server_outside", "bin 目录指向了 Breeze 目录外面，没有进去找。")
        return None
    seen = 0
    for dirpath, dirnames, filenames in os.walk(bin_dir):
        dirnames[:] = [name for name in dirnames if not os.path.islink(os.path.join(dirpath, name))]
        if not stays_within(bin_dir, dirpath):
            dirnames[:] = []
            continue
        seen += len(filenames)
        for name in filenames:
            if name.lower() != "breeze-server.exe":
                continue
            found = os.path.join(dirpath, name)
            if not stays_within(bin_dir, found):
                add(findings, "error", "breeze", "server_outside",
                    "bin 里的 breeze-server.exe 指向了目录外面，没有打开它。")
                return None
            return found
        if seen > 4000:
            add(findings, "warn", "breeze", "bin_large", "bin 目录文件很多，没有全部看完。")
            break
    add(findings, "error", "breeze", "server_missing", "在 bin 目录里找不到 breeze-server.exe。")
    return None


def check_helper(root, cfg, findings, guard, ffmpeg_lookup):
    script = os.path.join(root, "helper.py")
    ports_ok = inspect_helper_source(script, root, findings)
    settings = inspect_voice_settings(root, findings, guard)
    memes = os.path.join(root, "memes")
    if not os.path.isdir(memes):
        add(findings, "warn", "helper", "memes_missing", "没有 memes 目录。表情包要用，不影响四个程序的配置是否对得上。")
    ffmpeg_note = ""
    if settings.get("needs_ffmpeg"):
        try:
            found = ffmpeg_lookup("ffmpeg")
        except OSError:
            found = None
        if found:
            ffmpeg_note = "ffmpeg 已找到。"
        else:
            add(findings, "warn", "helper", "ffmpeg_missing", "没找到 ffmpeg。语速不是 1 倍时，语音中转需要它。")
    if not ports_ok:
        return ""
    return (f"脚本在，只听本机：表情包 {PORTS['helper_meme']}、语音 {PORTS['helper_voice']}，"
            f"转到本机 Breeze {PORTS['breeze']}。{ffmpeg_note}")


def inspect_helper_source(path, root, findings):
    shown = display_path(root, path)
    if not os.path.isfile(path):
        add(findings, "error", "helper", "script_missing", f"找不到辅助服务脚本：{shown}")
        return False
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_JSON_BYTES + 1)
    except OSError as e:
        add(findings, "error", "helper", "script_unreadable",
            f"读不了 {shown}：{e.strerror or '读失败'}。原文件没有改。")
        return False
    if len(raw) > MAX_JSON_BYTES:
        add(findings, "error", "helper", "script_unreadable", f"{shown} 太大，没有继续看。原文件没有改。")
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        add(findings, "error", "helper", "script_unreadable", f"{shown} 不是 UTF-8 文本。原文件没有改。")
        return False
    ok = True
    binds = [host for _quote, host in HELPER_BIND.findall(text)]
    if not binds:
        add(findings, "error", "helper", "bind_unknown", "看不出辅助服务绑在哪个地址。应该是 127.0.0.1。")
        ok = False
    elif not all(is_loopback(host) for host in binds):
        bad = next(host for host in binds if not is_loopback(host))
        check_loopback(findings, "helper", "bind_host", "辅助服务", bad)
        ok = False
    ports = {int(number) for number in HELPER_PORT.findall(text)}
    expected = {PORTS["helper_meme"], PORTS["helper_voice"]}
    if ports != expected:
        shown_ports = "、".join(str(number) for number in sorted(ports)) or "没有认出来"
        add(findings, "error", "helper", "ports",
            f"辅助服务写出的端口是 {shown_ports}，应该是 {PORTS['helper_meme']}（表情包）和 {PORTS['helper_voice']}（语音）。")
        ok = False
    match = HELPER_BREEZE.search(text)
    if not match:
        add(findings, "error", "helper", "breeze_unknown",
            f"认不出辅助服务要转去的 Breeze 地址。应该是本机 {PORTS['breeze']}。")
        return False
    endpoint = parse_endpoint(match.group(2))
    if endpoint == "hidden":
        add(findings, "error", "helper", "breeze_hidden",
            "Breeze 地址里夹着账号或参数。这里不原文展示，也不会去连。")
        return False
    if not isinstance(endpoint, dict):
        add(findings, "error", "helper", "breeze_unknown", "Breeze 地址不是能看懂的网址。体检没有连接它。")
        return False
    if not check_loopback(findings, "helper", "breeze_host", "语音中转的 Breeze", endpoint["host"]):
        ok = False
    if endpoint["scheme"] != "http":
        add(findings, "error", "helper", "breeze_scheme",
            f"语音中转的 Breeze 不是 http。应该是 http://127.0.0.1:{PORTS['breeze']}。体检没有连接它。")
        ok = False
    if not check_port(findings, "helper", "breeze_port", "语音中转的 Breeze", endpoint["port"], PORTS["breeze"]):
        ok = False
    return ok


def inspect_voice_settings(root, findings, guard):
    path = os.path.join(root, "voice-settings.json")
    info = {"needs_ffmpeg": False}
    if not os.path.exists(path):
        add(findings, "warn", "helper", "settings_missing",
            "还没有 voice-settings.json。会先按默认声音和 1 倍语速，运行 setup.py tts 后会生成。")
        return info
    data, err = read_json(path, root)
    if err:
        add(findings, "error", "helper", "settings_unreadable", err)
        return info
    guard.note_tree(data)
    if not isinstance(data, dict):
        add(findings, "error", "helper", "settings_type", "voice-settings.json 顶层应该是一组设置。原文件没有改。")
        return info
    voice = data.get("voice", "dafeiyu")
    if not isinstance(voice, str) or VOICE_ID.fullmatch(voice) is None:
        add(findings, "error", "helper", "settings_voice", "声音编号只能是字母、数字、下划线和短横线。")
    speed = data.get("speed", 1.0)
    number = as_speed(speed)
    if number is None:
        add(findings, "error", "helper", "settings_speed", "语速不是正常数字。原文件没有改。")
    elif number < 0.5 or number > 2.0:
        add(findings, "error", "helper", "settings_speed", "语速要在 0.5 到 2.0 之间。原文件没有改。")
    elif abs(number - 1.0) > 0.01:
        info["needs_ffmpeg"] = True
    return info


def as_speed(value):
    if isinstance(value, bool) or (isinstance(value, str) and len(value) > 16):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def check_astrbot(root, cfg, findings, guard):
    require_path(findings, "astrbot", "exe_missing", root, cfg.get("astrbot_exe"), "file", "AstrBot 程序")
    folder = require_path(findings, "astrbot", "dir_missing", root, cfg.get("astrbot_dir"), "dir", "AstrBot 目录")
    info = {"ws": []}
    if folder is None:
        return info, "AstrBot 程序和配置都在，后台与反向连接只听本机。"
    cmd_path = os.path.join(folder, "data", "cmd_config.json")
    if os.path.lexists(cmd_path) and not stays_within(folder, cmd_path):
        add(findings, "error", "astrbot", "cmd_outside", "AstrBot 的 cmd_config.json 指向了目录外面，没有读取。")
        return info, "AstrBot 的后台和反向连接都只听本机。"
    data, err = read_json(cmd_path, root)
    if err:
        add(findings, "error", "astrbot", "cmd_unreadable", err)
        return info, "AstrBot 的后台和反向连接都只听本机。"
    guard.note_tree(data)
    if not isinstance(data, dict):
        add(findings, "error", "astrbot", "cmd_type", "cmd_config.json 顶层应该是一组设置。原文件没有改。")
        return info, "AstrBot 的后台和反向连接都只听本机。"
    check_dashboard(data, findings)
    info["ws"] = check_platforms(data, findings)
    voice = check_tts(data, findings)
    return info, (f"后台只听本机 {PORTS['astrbot_dashboard']}，反向连接只听本机 {PORTS['astrbot_ws']}。{voice}")


def check_dashboard(data, findings):
    dash = data.get("dashboard", {})
    if dash is None:
        dash = {}
    if not isinstance(dash, dict):
        add(findings, "error", "astrbot", "dashboard_type", "AstrBot 后台那一项不是一组设置。")
        return
    host = dash.get("host", "127.0.0.1")
    check_loopback(findings, "astrbot", "dashboard_host", "AstrBot 后台", host)
    port = as_port(dash.get("port", PORTS["astrbot_dashboard"]))
    if port is None:
        add(findings, "error", "astrbot", "dashboard_port", "AstrBot 后台端口不是有效数字。")
    elif port != PORTS["astrbot_dashboard"]:
        add(findings, "warn", "astrbot", "dashboard_port",
            f"后台端口是 {port}，控制台按钮打开的是 {PORTS['astrbot_dashboard']}，两边对不上。")


def check_platforms(data, findings):
    platforms = data.get("platform", [])
    if not isinstance(platforms, list):
        add(findings, "error", "astrbot", "platform_type", "AstrBot 的平台列表读不了。")
        return []
    entries = [item for item in platforms if isinstance(item, dict) and item.get("type") == "aiocqhttp"]
    if not entries:
        add(findings, "error", "astrbot", "platform_missing", "AstrBot 配置里没有 QQ 平台（aiocqhttp）。")
        return []
    tokens = []
    for entry in entries:
        if entry.get("enable") is False:
            add(findings, "error", "astrbot", "platform_disabled", "AstrBot 里的 QQ 平台被关掉了。")
            continue
        host = entry.get("ws_reverse_host")
        if not check_loopback(findings, "astrbot", "ws_host", "AstrBot 等 NapCat 连过来的地址", host):
            tokens.append(("skip", None))
            continue
        check_port(findings, "astrbot", "ws_port", "AstrBot 反向连接",
                   as_port(entry.get("ws_reverse_port")), PORTS["astrbot_ws"])
        tokens.append(token_status(entry.get("ws_reverse_token")))
    return tokens


def check_tts(data, findings):
    providers = data.get("provider", [])
    if providers is None:
        providers = []
    if not isinstance(providers, list):
        add(findings, "warn", "astrbot", "tts_type", "语音提供商那一项读不了，没有当成已经接好。")
        return "语音提供商读不了。"
    breeze = [item for item in providers
              if isinstance(item, dict) and item.get("type") == "openai_tts_api" and item.get("enable") is not False]
    if not breeze:
        add(findings, "warn", "astrbot", "tts_missing",
            f"还没把语音接到辅助服务（127.0.0.1:{PORTS['helper_voice']}）。需要的话运行 python tools/setup.py tts。")
        return "语音还没接到辅助服务。"
    wired = False
    for item in breeze:
        endpoint = parse_endpoint(item.get("api_base"))
        if endpoint == "hidden":
            add(findings, "error", "astrbot", "tts_hidden",
                "语音地址里夹着账号或参数。这里不原文展示，也不会去连。")
            continue
        if not isinstance(endpoint, dict):
            add(findings, "error", "astrbot", "tts_bad", "语音地址不是能看懂的网址。体检没有连接它。")
            continue
        if not check_loopback(findings, "astrbot", "tts_host", "语音地址", endpoint["host"]):
            continue
        if not check_port(findings, "astrbot", "tts_port", "语音地址", endpoint["port"], PORTS["helper_voice"]):
            continue
        if endpoint["path"].rstrip("/") != "/v1":
            add(findings, "warn", "astrbot", "tts_path", "语音地址的路径不是 /v1。")
        elif endpoint["scheme"] != "http":
            add(findings, "warn", "astrbot", "tts_scheme",
                f"语音地址不是 http。辅助服务这套用的是 http://127.0.0.1:{PORTS['helper_voice']}/v1。")
        else:
            wired = True
    for item in providers:
        if isinstance(item, dict) and item.get("type") == "edge_tts" and item.get("enable") is True:
            add(findings, "warn", "astrbot", "edge_tts", "Edge 语音还开着。setup.py tts 会改成走本机 Breeze。")
            break
    if wired:
        return f"语音接到本机 {PORTS['helper_voice']}。"
    return "语音地址还没对上。"


def check_napcat(root, cfg, findings, guard, bot_qq):
    info = {"ws": [], "http": []}
    require_path(findings, "napcat", "qq_missing", root, cfg.get("qq_exe"), "file", "QQ 程序")
    napcat_dir = require_path(findings, "napcat", "dir_missing", root, cfg.get("napcat_dir"), "dir", "NapCat 目录")
    if napcat_dir is None:
        return info, "QQ 和 NapCat 的文件都在，端口只听本机。"
    for name, why in NAPCAT_FILES:
        path = os.path.join(napcat_dir, name)
        if os.path.lexists(path) and not stays_within(napcat_dir, path):
            add(findings, "error", "napcat", "file_outside", f"{name} 指向了 NapCat 目录外面，没有打开。")
        elif not os.path.isfile(path):
            add(findings, "error", "napcat", "file_missing", f"缺少 {name}（{why}）。")
    info = read_onebot(root, napcat_dir, findings, guard, bot_qq)
    read_webui(root, napcat_dir, findings, guard)
    return info, (f"QQ 和 NapCat 文件都在。HTTP 只听本机 {PORTS['napcat_http']}，"
                  f"反向连接指向本机 {PORTS['astrbot_ws']}。")


def read_onebot(root, napcat_dir, findings, guard, bot_qq):
    info = {"ws": [], "http": []}
    if not bot_qq:
        add(findings, "error", "napcat", "onebot_skipped", "小号不是纯数字，所以没有去拼 NapCat 的配置文件名。")
        return info
    name = f"onebot11_{bot_qq}.json"
    if not re.fullmatch(r"onebot11_[0-9]{4,20}\.json", name) or os.path.basename(name) != name:
        add(findings, "error", "napcat", "onebot_skipped", "小号不是纯数字，所以没有去拼 NapCat 的配置文件名。")
        return info
    config_dir = os.path.join(napcat_dir, "config")
    path = os.path.join(config_dir, name)
    if os.path.basename(path) != name or not stays_within(napcat_dir, path):
        add(findings, "error", "napcat", "onebot_outside", "NapCat 的 QQ 配置指向了 napcat 目录外面，没有读取。")
        return info
    if not os.path.lexists(path):
        add(findings, "error", "napcat", "onebot_missing",
            f"找不到 config/{name}。先用小号登录一次 NapCat，它才会生成这个文件。")
        return info
    if os.path.islink(path) and not stays_within(napcat_dir, path):
        add(findings, "error", "napcat", "onebot_outside", "NapCat 的 QQ 配置指向了 napcat 目录外面，没有读取。")
        return info
    if not stays_within(napcat_dir, path):
        add(findings, "error", "napcat", "onebot_outside", "NapCat 的 QQ 配置指向了 napcat 目录外面，没有读取。")
        return info
    data, err = read_json(path, root)
    if err:
        add(findings, "error", "napcat", "onebot_unreadable", err)
        return info
    guard.note_tree(data)
    if not isinstance(data, dict):
        add(findings, "error", "napcat", "onebot_type", "NapCat 的 QQ 配置顶层应该是一组设置。原文件没有改。")
        return info
    network = data.get("network", {})
    if not isinstance(network, dict):
        add(findings, "error", "napcat", "network_type", "NapCat 的网络配置读不了。原文件没有改。")
        return info
    info["ws"] = check_ws_clients(network.get("websocketClients"), findings)
    info["http"] = check_http_servers(network.get("httpServers"), findings)
    return info


def check_ws_clients(clients, findings):
    if not isinstance(clients, list) or not clients:
        add(findings, "error", "napcat", "ws_missing", "NapCat 没有配置连到 AstrBot 的 WebSocket。")
        return []
    tokens = []
    enabled = False
    for client in clients:
        if not isinstance(client, dict):
            add(findings, "error", "napcat", "ws_type", "NapCat 的 WebSocket 配置有一项读不了。")
            continue
        endpoint = parse_endpoint(client.get("url"))
        if endpoint == "hidden":
            add(findings, "error", "napcat", "ws_hidden",
                "NapCat 的连接地址里夹着账号或参数。这里不原文展示，也不会去连。")
            continue
        if not isinstance(endpoint, dict):
            add(findings, "error", "napcat", "ws_bad", "NapCat 的连接地址不是能看懂的网址。体检没有连接它。")
            continue
        local = check_loopback(findings, "napcat", "ws_host", "NapCat 要连的 AstrBot", endpoint["host"])
        port_ok = check_port(findings, "napcat", "ws_port", "NapCat 要连的 AstrBot",
                             endpoint["port"], PORTS["astrbot_ws"])
        if endpoint["path"].rstrip("/") != "/ws":
            add(findings, "warn", "napcat", "ws_path", "NapCat 的连接路径不是 /ws。AstrBot 等的是 /ws。")
        if endpoint["scheme"] != "ws":
            add(findings, "warn", "napcat", "ws_scheme", "NapCat 用的不是 ws://。这套连通方式是 ws://127.0.0.1:6199/ws。")
        if client.get("enable") is False:
            add(findings, "warn", "napcat", "ws_disabled", "有一条连到 AstrBot 的 WebSocket 是关着的。")
            continue
        enabled = True
        if local and port_ok:
            tokens.append(token_status(client.get("token")))
        else:
            tokens.append(("skip", None))
    if not enabled:
        add(findings, "error", "napcat", "ws_disabled_all", "NapCat 没有启用连到 AstrBot 的 WebSocket。")
    return tokens


def check_http_servers(servers, findings):
    if not isinstance(servers, list) or not servers:
        add(findings, "error", "napcat", "http_missing", "NapCat 没有本机 HTTP 接口。控制台靠它看 QQ 在不在线。")
        return []
    tokens = []
    enabled = False
    for server in servers:
        if not isinstance(server, dict):
            add(findings, "error", "napcat", "http_type", "NapCat 的 HTTP 配置有一项读不了。")
            continue
        local = check_loopback(findings, "napcat", "http_host", "NapCat 的 HTTP 接口", server.get("host"))
        port_ok = check_port(findings, "napcat", "http_port", "NapCat 的 HTTP 接口",
                             as_port(server.get("port")), PORTS["napcat_http"])
        if server.get("enable") is False:
            add(findings, "warn", "napcat", "http_disabled", "有一条 NapCat HTTP 接口是关着的。")
            continue
        enabled = True
        if local and port_ok:
            tokens.append(token_status(server.get("token")))
        else:
            tokens.append(("skip", None))
    if not enabled:
        add(findings, "error", "napcat", "http_disabled_all", "NapCat 没有启用本机 HTTP 接口。控制台靠它看 QQ 在不在线。")
    return tokens


def read_webui(root, napcat_dir, findings, guard):
    path = os.path.join(napcat_dir, "config", "webui.json")
    if not stays_within(napcat_dir, path):
        add(findings, "error", "napcat", "webui_outside", "NapCat 管理页配置指向了目录外面，没有读取。")
        return
    if not os.path.lexists(path):
        add(findings, "warn", "napcat", "webui_missing", "没有 config/webui.json。管理页可能还没收回本机。")
        return
    if not stays_within(napcat_dir, path):
        add(findings, "error", "napcat", "webui_outside", "NapCat 管理页配置指向了目录外面，没有读取。")
        return
    data, err = read_json(path, root)
    if err:
        add(findings, "error", "napcat", "webui_unreadable", err)
        return
    guard.note_tree(data)
    if not isinstance(data, dict):
        add(findings, "error", "napcat", "webui_type", "webui.json 顶层应该是一组设置。原文件没有改。")
        return
    check_loopback(findings, "napcat", "webui_host", "NapCat 管理页", data.get("host"))


def compare_tokens(findings, astrbot_ws, napcat_ws, http_tokens, config_token):
    compare_pair(findings, ("astrbot", "napcat"), "ws_token", "反向连接密钥", astrbot_ws, napcat_ws,
                 "AstrBot 等 NapCat 用的密钥", "NapCat 拿去连 AstrBot 的密钥")
    compare_pair(findings, ("config", "napcat"), "http_token", "HTTP 接口密钥",
                 [token_status(config_token)], http_tokens,
                 "config.json 里的接口密钥", "NapCat HTTP 配置里的密钥")


def compare_pair(findings, programs, code, label, left, right, left_name, right_name):
    left = [item for item in left if item[0] != "skip"]
    right = [item for item in right if item[0] != "skip"]
    # 某一边根本没有这项配置时，前面已经报过「找不到」。这里不再猜密钥。
    if not left or not right:
        return

    def tell(suffix, message):
        for program in programs:
            add(findings, "error", program, f"{code}_{suffix}", message)

    if any(item[0] == "unsafe" for item in left + right):
        tell("unsafe", f"{label}里有空格或符号，不适合放进请求头。报告里不写原文。")
        return
    left_ok = [item[1] for item in left if item[0] == "ok"]
    right_ok = [item[1] for item in right if item[0] == "ok"]
    if not left_ok or not right_ok:
        tell("missing", f"没有写{label}。" if not left_ok and not right_ok else f"有一边没写{label}。")
        return
    if not tokens_agree(left_ok, right_ok):
        tell("mismatch", f"{left_name}和{right_name}不一致。")


def tokens_agree(left, right):
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if not hmac.compare_digest(a, b):
            return False
    return True


def check_config_paths(root, cfg, findings):
    icon = resolve_path(root, cfg.get("icon"))
    if icon is None:
        add(findings, "warn", "config", "icon_type", "图标路径应该是文字。不影响四个程序。")
    elif not os.path.isfile(icon):
        add(findings, "warn", "config", "icon_missing", f"找不到窗口图标：{icon}。不影响四个程序。")
    pack = resolve_path(root, cfg.get("config_pack_dir"))
    if pack is None:
        add(findings, "warn", "config", "pack_type", "配置包路径应该是文字。不影响四个程序。")
    elif not os.path.isdir(pack):
        add(findings, "warn", "config", "pack_missing",
            f"还没找到配置包目录：{pack}。导入人格时才需要，不影响四个程序启动。")


def diagnose(root, ffmpeg_lookup=None):
    root = os.path.abspath(root)
    findings = []
    guard = SecretGuard()
    if ffmpeg_lookup is None:
        ffmpeg_lookup = shutil.which
    if not os.path.isdir(root):
        add(findings, "error", "config", "root_missing", f"安装目录不存在：{root}")
        scrub_findings(findings, guard)
        return {"root": root, "findings": findings}

    cfg_path = os.path.join(root, "config.json")
    if not os.path.exists(cfg_path):
        add(findings, "error", "config", "config_missing",
            "还没找到 config.json。把 config.example.json 复制成 config.json，填上小号后再检查。原文件没有改。")
        add(findings, "warn", "config", "skipped", "配置没读成，没有继续核对四个程序。")
        scrub_findings(findings, guard)
        return {"root": root, "findings": findings}
    cfg, err = read_json(cfg_path, root)
    if err:
        add(findings, "error", "config", "config_unreadable", err)
        add(findings, "warn", "config", "skipped", "配置没读成，没有继续核对四个程序。")
        scrub_findings(findings, guard)
        return {"root": root, "findings": findings}
    if not isinstance(cfg, dict):
        add(findings, "error", "config", "config_type", "config.json 顶层应该是一组设置。原文件没有改。")
        add(findings, "warn", "config", "skipped", "配置没读成，没有继续核对四个程序。")
        scrub_findings(findings, guard)
        return {"root": root, "findings": findings}

    guard.note_tree(cfg)
    merged = dict(DEFAULTS)
    merged.update(cfg)
    bot_qq = classify_qq(findings, "bot_qq", merged.get("bot_qq"), True, "机器人小号")
    classify_qq(findings, "owner_qq", merged.get("owner_qq"), False, "主人 QQ")
    check_config_paths(root, merged, findings)

    breeze_ok = check_breeze(root, merged, findings)
    helper_ok = check_helper(root, merged, findings, guard, ffmpeg_lookup)
    astrbot_info, astrbot_ok = check_astrbot(root, merged, findings, guard)
    napcat_info, napcat_ok = check_napcat(root, merged, findings, guard, bot_qq)
    compare_tokens(findings, astrbot_info.get("ws", []), napcat_info.get("ws", []),
                   napcat_info.get("http", []), merged.get("napcat_http_token"))

    qq_text = bot_qq or "已填写"
    finish(findings, "config", f"config.json 能读，小号是 {qq_text}。")
    finish(findings, "breeze", breeze_ok)
    finish(findings, "helper", helper_ok)
    finish(findings, "astrbot", astrbot_ok)
    finish(findings, "napcat", napcat_ok)
    scrub_findings(findings, guard)
    return {"root": root, "findings": findings}


def counts(report):
    errors = sum(item["level"] == "error" for item in report["findings"])
    warns = sum(item["level"] == "warn" for item in report["findings"])
    return errors, warns


def public_report(report):
    errors, warns = counts(report)
    return {
        "root": report["root"],
        "ok": errors == 0,
        "error_count": errors,
        "warn_count": warns,
        "findings": report["findings"],
    }


def format_report(report):
    lines = ["大肥鱼配置体检", f"安装目录：{report['root']}", ""]
    labels = {"ok": "正常", "warn": "注意", "error": "问题"}
    for program in PROGRAM_NAMES:
        items = [item for item in report["findings"] if item["program"] == program]
        if not items:
            continue
        lines.append(f"【{PROGRAM_NAMES[program]}】")
        for item in items:
            lines.append(f"  [{labels[item['level']]}] {item['message']}")
        lines.append("")
    errors, warns = counts(report)
    if errors:
        lines.append(f"结果：{errors} 个问题，{warns} 条注意。问题没处理之前，不建议启动。")
    elif warns:
        lines.append(f"结果：没有硬伤，有 {warns} 条注意。")
    else:
        lines.append("结果：四个程序的路径和端口配置对得上。")
    lines.append("这次只看了文件：没有连接任何地址，也没有启动、停止或修改文件。")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="检查 Breeze 语音、辅助服务、AstrBot、QQ/NapCat 的配置。不启动程序，不改文件，不连接任何地址。",
    )
    parser.add_argument("--root", help="安装目录。不写的话就是这个脚本的上一级目录。")
    parser.add_argument("--json", action="store_true", help="用 JSON 打出报告，方便别的程序读取。")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root) if args.root else install_root()
    report = diagnose(root)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if args.json:
        json.dump(public_report(report), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_report(report))
    errors, _warns = counts(report)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

"""大肥鱼控制台的配置工具。把部署时要手改的几处配置做成命令，改之前都会备份成 .bak。

用法（在安装目录下运行，先停掉 AstrBot，改完再用控制台启动）：
  python tools\\setup.py link            连通 NapCat 和 AstrBot：反向 WebSocket + 本机 HTTP 接口，都只听 127.0.0.1、都带随机密钥
  python tools\\setup.py tts             让 AstrBot 的语音走 Breeze（经辅助服务调语速、换声音），关掉 Edge 语音，
                                        并装上群聊/私聊分开设语音概率的插件
  python tools\\setup.py group 群号       让她在这个群里潜水聊天（白名单 + 群聊插件只对这个群生效）
  python tools\\setup.py memes           把配置包要的两套表情包下到 memes\\，导入时从本机取，不用等 GitHub
  python tools\\setup.py voices          在 Breeze 里做一批预设声音（要先启动 Breeze）
  python tools\\setup.py shortcut        在桌面放一个「大肥鱼控制台」快捷方式（不带黑窗口）
"""
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BREEZE = "http://127.0.0.1:8137"
GH_ACCELERATORS = ["https://gh.dpik.top/", "https://gh-proxy.com/", "https://ghfast.top/", ""]
MEME_PACKS = ["anka-afk/blue-fat-fish-meme-pack@v1.0.0", "anka-afk/deepseek-fanart-meme-pack@v1.0.0"]
DESIGN_TEXT = "哼，你终于想起来找我啦？我还以为你把我忘了呢。今天有没有给我带好吃的呀？"
# (id, 列表里的名字, 声音描述, 念的台词；None 就用 DESIGN_TEXT)
VOICE_PRESETS = [
    ("dafeiyu", "大肥鱼（软萌撒娇）", "一个憨萌软糯的小女生，声音甜甜的、有点奶，十几岁的感觉，嘴馋爱撒娇，带一点小傲娇", None),
    ("yuanqi", "元气少女", "十六七岁的元气少女，声音清亮有活力，说话轻快，带着笑意", None),
    ("yujie", "温柔御姐", "二十多岁的成熟女性，声音温柔、略低，知性优雅，说话从容", None),
    ("lanyang", "慵懒迷糊", "刚睡醒的少女，声音软软的、懒洋洋，有点鼻音，说话慢悠悠", None),
    ("aojiao", "傲娇大小姐", "十几岁的傲娇大小姐，声音清脆，语气高傲又带点害羞", None),
    ("jiazi", "夹子音甜妹（B站）", "说话嗲嗲的夹子音少女，声音又细又甜，尾音上扬，撒娇感很强", None),
    ("taiwan", "台湾腔甜妹（B站）", "台湾腔的甜美女生，说话软软的，语气词很多，尾音轻柔上扬",
     "厚，你终于想到要来找我了喔？我还以为你把我忘记了耶，今天有没有带好吃的给我啦？"),
    ("dongbei", "东北大妞（抖音）", "东北口音的豪爽女生，大大咧咧，说话带浓浓的东北腔，很有喜感",
     "哎呀妈呀，你可算想起来找我了！我还寻思你把我忘了呢，今儿有没有给我整点好吃的？"),
    ("cixing", "磁性女声（抖音）", "低沉磁性的成熟女声，声音略带沙哑，很有质感，像深夜电台主播", None),
    ("jieshuo", "解说小哥（抖音）", "二十多岁的年轻男生，声音清亮，带点调侃，语速偏快，像短视频影视解说",
     "注意看，这个女孩叫大肥鱼，她终于等到主人来找她了，但她不知道的是，主人今天什么吃的都没带。"),
    ("qinggan", "情感男声（抖音）", "温柔深情的青年男声，声音低沉温暖，语速舒缓，像在讲情感故事",
     "有些人，等着等着就来了。你终于想起来找我啦，我还以为，你早就把我忘了呢。"),
    ("zhengtai", "正太音（B站）", "十岁左右的小男孩，声音清脆，奶声奶气，说话天真可爱", None),
]


def load(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def save(path, data, backup=True):
    if backup and os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    # 先写到临时文件再换上去。直接打开原文件会先把内容清空，写到一半失败就把配置写坏
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit("先把 config.example.json 复制成 config.json，填好 bot_qq。")
    try:
        cfg = load(CONFIG_PATH)
    except (OSError, ValueError) as e:
        sys.exit(f"config.json 读不了：{e}")
    if not isinstance(cfg, dict):
        sys.exit("config.json 必须是 JSON 对象。")
    bot = cfg.get("bot_qq")
    if bot is None or not str(bot).strip():
        sys.exit("config.json 里的 bot_qq（机器人小号）还没填。")
    cfg["bot_qq"] = str(bot).strip()
    return cfg


def path_of(cfg, key, default):
    return os.path.join(ROOT, os.path.expanduser(cfg.get(key) or default))


def cmd_config_path(cfg):
    return os.path.join(path_of(cfg, "astrbot_dir", "astrbot"), "data", "cmd_config.json")


def link(cfg):
    bot = str(cfg["bot_qq"]).strip()
    napcat = path_of(cfg, "napcat_dir", "napcat")
    ob_path = os.path.join(napcat, "config", f"onebot11_{bot}.json")
    if not os.path.exists(ob_path):
        sys.exit(f"没找到 {ob_path}。先用 NapCat 扫码登录一次小号，它才会生成这个文件。")
    cp = cmd_config_path(cfg)
    webui = os.path.join(napcat, "config", "webui.json")
    try:
        c = load(cp)
        ob = load(ob_path)
        w = load(webui) if os.path.exists(webui) else None
    except (OSError, ValueError) as e:
        sys.exit(f"配置读不了，两边都还没改：{e}")
    if not isinstance(c, dict) or not isinstance(ob, dict):
        sys.exit("AstrBot 或 NapCat 的配置不是 JSON 对象，两边都还没改。")
    ws_token, http_token = secrets.token_hex(16), secrets.token_hex(16)

    dashboard = c.get("dashboard")
    if not isinstance(dashboard, dict):
        dashboard = {}
    dashboard["host"] = "127.0.0.1"
    c["dashboard"] = dashboard
    platforms = c.get("platform")
    if not isinstance(platforms, list):
        platforms = []
    c["platform"] = [p for p in platforms if isinstance(p, dict) and p.get("type") != "aiocqhttp"] + [{
        "id": "qq", "type": "aiocqhttp", "enable": True,
        "ws_reverse_host": "127.0.0.1", "ws_reverse_port": 6199, "ws_reverse_token": ws_token}]

    net = ob.get("network")
    if not isinstance(net, dict):
        net = {}
    ob["network"] = net
    net["websocketClients"] = [{
        "name": "astrbot", "enable": True, "url": "ws://127.0.0.1:6199/ws", "messagePostFormat": "array",
        "reportSelfMessage": False, "reconnectInterval": 5000, "token": ws_token, "debug": False, "heartInterval": 5000}]
    net["httpServers"] = [{
        "name": "local-http", "enable": True, "host": "127.0.0.1", "port": 3000, "enableCors": False,
        "enableWebsocket": False, "messagePostFormat": "array", "token": http_token, "debug": False}]

    # 全部读完再写，避免只改了一边、密钥对不上
    save(cp, c)
    save(ob_path, ob)
    if isinstance(w, dict):
        w["host"] = "127.0.0.1"
        save(webui, w)

    cfg["napcat_http_token"] = http_token
    save(CONFIG_PATH, cfg)
    print("已连通：AstrBot 在 127.0.0.1:6199 等 NapCat 连过来，NapCat 的 HTTP 接口在 127.0.0.1:3000。")
    print("两边的密钥已写好，HTTP 接口密钥也存进了 config.json。重启 AstrBot 和 NapCat 后生效。")


def tts(cfg):
    cp = cmd_config_path(cfg)
    c = load(cp)
    pid = "大肥鱼语音-Breeze"
    raw_provs = c.get("provider")
    if not isinstance(raw_provs, list):
        raw_provs = []
    provs = [p for p in raw_provs if isinstance(p, dict) and p.get("id") != pid]
    for p in provs:
        if p.get("type") == "edge_tts":
            p["enable"] = False
    provs.append({"id": pid, "type": "openai_tts_api", "provider": "openai", "provider_type": "text_to_speech",
                  "enable": True, "api_key": "unused", "api_base": "http://127.0.0.1:8138/v1", "model": "tts-1",
                  "openai-tts-voice": "dafeiyu", "timeout": 180, "proxy": ""})
    c["provider"] = provs
    t = c.get("provider_tts_settings")
    if not isinstance(t, dict):
        t = {}
    c["provider_tts_settings"] = t
    try:
        old_prob = float(t.get("trigger_probability", 0.33))
    except (TypeError, ValueError):
        old_prob = 0.33
    # 群聊、私聊的概率交给 voice_ratio 插件掷，AstrBot 全局那一道放到 100%
    t.update({"enable": True, "provider_id": pid, "trigger_probability": 1.0})
    save(cp, c)

    astrbot = path_of(cfg, "astrbot_dir", "astrbot")
    name = "astrbot_plugin_dafeiyu_voice_ratio"
    shutil.copytree(os.path.join(ROOT, "plugins", name), os.path.join(astrbot, "data", "plugins", name),
                    dirs_exist_ok=True)
    vr_path = os.path.join(astrbot, "data", "config", f"{name}_config.json")
    os.makedirs(os.path.dirname(vr_path), exist_ok=True)
    if not os.path.exists(vr_path):
        prob = old_prob if old_prob < 1 else 0.33
        save(vr_path, {"group_probability": prob, "private_probability": prob}, backup=False)
    print("已装上插件 astrbot_plugin_dafeiyu_voice_ratio：群聊、私聊的语音概率分开设。")
    vs_path = os.path.join(ROOT, "voice-settings.json")
    if not os.path.exists(vs_path):
        save(vs_path, {"voice": "dafeiyu", "speed": 1.2, "labels": {}}, backup=False)
    print("语音已接到辅助服务（127.0.0.1:8138），语速和声音在控制台的「声音」页调。重启 AstrBot 后生效。")


def group(cfg, gid):
    gid = str(gid).strip()
    if not gid.isdigit():
        sys.exit("群号只能是数字")
    cp = cmd_config_path(cfg)
    c = load(cp)
    ps = c.get("platform_settings")
    if not isinstance(ps, dict):
        ps = {}
    c["platform_settings"] = ps
    wl = ps.get("id_whitelist")
    if not isinstance(wl, list):
        wl = []
    token = f"qq:GroupMessage:{gid}"
    if token not in wl:
        wl.append(token)
    ps["id_whitelist"] = wl
    save(cp, c)
    gp = os.path.join(path_of(cfg, "astrbot_dir", "astrbot"), "data", "config",
                      "astrbot_plugin_group_chat_plus_config.json")
    if os.path.exists(gp):
        g = load(gp)
        if not isinstance(g, dict):
            g = {}
        raw_groups = g.get("enabled_groups") or []
        if not isinstance(raw_groups, list):
            raw_groups = []
        groups = []
        for item in raw_groups:
            text = str(item).strip()
            if text and text not in groups:
                groups.append(text)
        if gid not in groups:
            groups.append(gid)
        g["enabled_groups"] = groups
        try:
            initial = float(g.get("initial_probability", 0))
        except (TypeError, ValueError):
            initial = 0.0
        g["initial_probability"] = initial if initial >= 0.05 else 0.1
        try:
            after = float(g.get("after_reply_probability", 0.6))
        except (TypeError, ValueError):
            after = 0.6
        g["after_reply_probability"] = min(after, 0.6)
        save(gp, g)
    else:
        print("（没装群聊插件 astrbot_plugin_group_chat_plus，只加了白名单：她只在被 @ 或叫名字时回）")
    print(f"群 {gid} 已开放。重启 AstrBot 后生效，插嘴频率在控制台的「群聊和语音」页调。")


def memes(cfg):
    out_dir = os.path.join(ROOT, "memes")
    os.makedirs(out_dir, exist_ok=True)
    opener = urllib.request.build_opener()  # 走系统代理，下得快
    for spec in MEME_PACKS:
        repo, ref = spec.split("@")
        target = os.path.join(out_dir, f"{repo.split('/')[1]}-{ref}.zip")
        if os.path.exists(target):
            print(f"{os.path.basename(target)} 已有，跳过")
            continue
        url = f"https://github.com/{repo}/archive/{ref}.zip"
        part = target + ".part"
        for acc in GH_ACCELERATORS:
            try:
                print(f"下载 {repo}（{acc or '直连 GitHub'}）…", flush=True)
                with opener.open(acc + url, timeout=60) as r, open(part, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                with open(part, "rb") as f:
                    magic = f.read(2)
                # 加速站有时会返回一段网页，不能把它当成表情包留下
                if magic == b"PK" and os.path.getsize(part) >= 4:
                    os.replace(part, target)
                    print(f"  好了：{os.path.getsize(target) // (1 << 20)} MB")
                    break
                print("  失败：下载内容不是压缩包")
            except Exception as e:
                print(f"  失败：{e}")
            finally:
                if os.path.exists(part):
                    os.remove(part)
        else:
            print(f"{repo} 没下下来，过一阵再试")


def voices(cfg):
    vs_path = os.path.join(ROOT, "voice-settings.json")
    vs = load(vs_path) if os.path.exists(vs_path) else {"voice": "dafeiyu", "speed": 1.2}
    if not isinstance(vs, dict):
        vs = {"voice": "dafeiyu", "speed": 1.2}
    labels = vs.get("labels")
    if not isinstance(labels, dict):
        labels = {}
    vs["labels"] = labels
    tmp = os.path.join(ROOT, "voice-candidates")
    os.makedirs(tmp, exist_ok=True)
    try:
        with NO_PROXY.open(BREEZE + "/v1/voices", timeout=5) as r:
            listing = json.load(r)
        have = {v["id"] for v in listing if isinstance(v, dict) and v.get("saved")}
    except Exception:
        sys.exit("连不上 Breeze（127.0.0.1:8137），先在控制台里启动它。")
    for vid, label, desc, text in VOICE_PRESETS:
        if vid in have:
            labels.setdefault(vid, label)
            print(f"{label}：已有，跳过")
            continue
        text = text or DESIGN_TEXT
        chosen = None
        for _ in range(4):
            body = json.dumps({"input": text, "instructions": desc, "cfg_scale": 4,
                               "seed": random.randint(1, 99999), "response_format": "pcm"}).encode()
            req = urllib.request.Request(BREEZE + "/v1/audio/speech", body, {"Content-Type": "application/json"})
            try:
                with NO_PROXY.open(req, timeout=600) as r:
                    pcm = r.read()
            except urllib.error.URLError as e:
                print(f"  失败：{e}")
                continue
            # 正常大约每秒 3~8 个字；空音频、长静音或念崩就换个种子
            seconds = len(pcm) / 48000 if pcm else 0
            rate = len(text) / seconds if seconds else 0
            if 3.0 <= rate <= 8.0:
                chosen = pcm
                break
        if chosen is None:
            print(f"{label}：生成结果长度不正常，跳过")
            continue
        path = os.path.join(tmp, f"preset-{vid}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(chosen)
        b = uuid.uuid4().hex
        parts = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
                         for k, v in (("ref_text", text), ("name", vid), ("description", desc)))
        head = (f"--{b}\r\nContent-Disposition: form-data; name=\"ref_audio\"; filename=\"ref.wav\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode()
        with open(path, "rb") as fh:
            audio = fh.read()
        data = parts + head + audio + f"\r\n--{b}--\r\n".encode()
        try:
            with NO_PROXY.open(urllib.request.Request(
                    BREEZE + "/v1/voices", data,
                    {"Content-Type": f"multipart/form-data; boundary={b}"}), timeout=120) as r:
                r.read()
        except urllib.error.URLError as e:
            print(f"{label}：保存失败：{e}")
            continue
        labels.setdefault(vid, label)
        print(f"{label}：做好了")
    save(vs_path, vs, backup=False)
    print("在控制台的「声音」页能试听和切换。不满意的可以删掉，在那一页按描述重做。")


def shortcut(cfg):
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    icon = path_of(cfg, "icon", "dafeiyu.ico")
    ps = ("$d=[Environment]::GetFolderPath('Desktop');"
          "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\大肥鱼控制台.lnk\");"
          "$s.TargetPath=$args[0];$s.Arguments=$args[1];$s.WorkingDirectory=$args[2];"
          "if(Test-Path $args[3]){$s.IconLocation=$args[3]};$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps, pythonw,
                    f'"{os.path.join(ROOT, "dafeiyu_console.pyw")}"', ROOT, icon], check=True)
    print("桌面上放好了「大肥鱼控制台」。")


def main():
    cmds = {"link": link, "tts": tts, "memes": memes, "voices": voices, "shortcut": shortcut}
    if len(sys.argv) < 2 or sys.argv[1] not in (*cmds, "group"):
        sys.exit(__doc__)
    cfg = config()
    cmd = sys.argv[1]
    if cmd == "group":
        if len(sys.argv) < 3:
            sys.exit("用法：python tools\\setup.py group 群号")
        group(cfg, sys.argv[2])
    else:
        cmds[cmd](cfg)


if __name__ == "__main__":
    main()

"""大肥鱼控制台的配置工具。把部署时要手改的几处配置做成命令，改之前都会备份成 .bak。

用法（在安装目录下运行，先停掉 AstrBot，改完再用控制台启动）：
  python tools\\setup.py link            连通 NapCat 和 AstrBot：反向 WebSocket + 本机 HTTP 接口，都只听 127.0.0.1、都带随机密钥
  python tools\\setup.py tts             让 AstrBot 的语音走 Breeze（经辅助服务调语速、换声音），关掉 Edge 语音
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
    return json.load(open(path, encoding="utf-8-sig"))


def save(path, data, backup=True):
    if backup and os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit("先把 config.example.json 复制成 config.json，填好 bot_qq。")
    cfg = load(CONFIG_PATH)
    if not str(cfg.get("bot_qq", "")).strip():
        sys.exit("config.json 里的 bot_qq（机器人小号）还没填。")
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
    ws_token, http_token = secrets.token_hex(16), secrets.token_hex(16)

    cp = cmd_config_path(cfg)
    c = load(cp)
    c.setdefault("dashboard", {})["host"] = "127.0.0.1"
    c["platform"] = [p for p in c.get("platform", []) if p.get("type") != "aiocqhttp"] + [{
        "id": "qq", "type": "aiocqhttp", "enable": True,
        "ws_reverse_host": "127.0.0.1", "ws_reverse_port": 6199, "ws_reverse_token": ws_token}]
    save(cp, c)

    ob = load(ob_path)
    net = ob.setdefault("network", {})
    net["websocketClients"] = [{
        "name": "astrbot", "enable": True, "url": "ws://127.0.0.1:6199/ws", "messagePostFormat": "array",
        "reportSelfMessage": False, "reconnectInterval": 5000, "token": ws_token, "debug": False, "heartInterval": 5000}]
    net["httpServers"] = [{
        "name": "local-http", "enable": True, "host": "127.0.0.1", "port": 3000, "enableCors": False,
        "enableWebsocket": False, "messagePostFormat": "array", "token": http_token, "debug": False}]
    save(ob_path, ob)

    webui = os.path.join(napcat, "config", "webui.json")
    if os.path.exists(webui):
        w = load(webui)
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
    provs = [p for p in c.get("provider", []) if p.get("id") != pid]
    for p in provs:
        if p.get("type") == "edge_tts":
            p["enable"] = False
    provs.append({"id": pid, "type": "openai_tts_api", "provider": "openai", "provider_type": "text_to_speech",
                  "enable": True, "api_key": "unused", "api_base": "http://127.0.0.1:8138/v1", "model": "tts-1",
                  "openai-tts-voice": "dafeiyu", "timeout": 180, "proxy": ""})
    c["provider"] = provs
    t = c.setdefault("provider_tts_settings", {})
    t.update({"enable": True, "provider_id": pid})
    t.setdefault("trigger_probability", 0.33)
    save(cp, c)
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
    ps = c.setdefault("platform_settings", {})
    wl = ps.setdefault("id_whitelist", [])
    if f"qq:GroupMessage:{gid}" not in wl:
        wl.append(f"qq:GroupMessage:{gid}")
    save(cp, c)
    gp = os.path.join(path_of(cfg, "astrbot_dir", "astrbot"), "data", "config",
                      "astrbot_plugin_group_chat_plus_config.json")
    if os.path.exists(gp):
        g = load(gp)
        groups = [str(x) for x in g.get("enabled_groups") or []]
        if gid not in groups:
            groups.append(gid)
        g["enabled_groups"] = groups
        g["initial_probability"] = g.get("initial_probability") if g.get("initial_probability", 0) >= 0.05 else 0.1
        g["after_reply_probability"] = min(float(g.get("after_reply_probability", 0.6)), 0.6)
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
        for acc in GH_ACCELERATORS:
            try:
                print(f"下载 {repo}（{acc or '直连 GitHub'}）…", flush=True)
                with opener.open(acc + url, timeout=60) as r, open(target + ".part", "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                os.replace(target + ".part", target)
                print(f"  好了：{os.path.getsize(target) // (1 << 20)} MB")
                break
            except Exception as e:
                print(f"  失败：{e}")
        else:
            print(f"{repo} 没下下来，过一阵再试")


def voices(cfg):
    vs_path = os.path.join(ROOT, "voice-settings.json")
    vs = load(vs_path) if os.path.exists(vs_path) else {"voice": "dafeiyu", "speed": 1.2}
    labels = vs.setdefault("labels", {})
    tmp = os.path.join(ROOT, "voice-candidates")
    os.makedirs(tmp, exist_ok=True)
    try:
        have = {v["id"] for v in json.load(NO_PROXY.open(BREEZE + "/v1/voices", timeout=5)) if v.get("saved")}
    except Exception:
        sys.exit("连不上 Breeze（127.0.0.1:8137），先在控制台里启动它。")
    for vid, label, desc, text in VOICE_PRESETS:
        labels.setdefault(vid, label)
        if vid in have:
            print(f"{label}：已有，跳过")
            continue
        text = text or DESIGN_TEXT
        for _ in range(4):
            body = json.dumps({"input": text, "instructions": desc, "cfg_scale": 4,
                               "seed": random.randint(1, 99999), "response_format": "pcm"}).encode()
            req = urllib.request.Request(BREEZE + "/v1/audio/speech", body, {"Content-Type": "application/json"})
            pcm = NO_PROXY.open(req, timeout=600).read()
            # 正常大约每秒 3~8 个字；离谱的（长静音、念崩）换个种子重来
            if 3.0 <= len(text) / (len(pcm) / 48000) <= 8.0:
                break
        path = os.path.join(tmp, f"preset-{vid}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        b = uuid.uuid4().hex
        parts = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
                         for k, v in (("ref_text", text), ("name", vid), ("description", desc)))
        head = (f"--{b}\r\nContent-Disposition: form-data; name=\"ref_audio\"; filename=\"ref.wav\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode()
        data = parts + head + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
        NO_PROXY.open(urllib.request.Request(BREEZE + "/v1/voices", data,
                                             {"Content-Type": f"multipart/form-data; boundary={b}"}), timeout=120)
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

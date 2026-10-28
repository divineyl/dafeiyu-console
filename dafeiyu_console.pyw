"""大肥鱼控制台：一个窗口管住 Breeze 语音、辅助服务、AstrBot、NapCat 四个程序。

程序都在后台无窗口运行，输出写进安装目录下的 logs；关掉控制台它们照常跑。
路径、机器人小号、NapCat 接口密钥都写在同目录的 config.json（照 config.example.json 填）。
"""
import json
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import uuid
import wave
import webbrowser
import winsound
from tkinter import messagebox, ttk

import psutil

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DEFAULTS = {
    "bot_qq": "",
    "napcat_http_token": "",
    "qq_exe": r"C:\Program Files\Tencent\QQNT\QQ.exe",
    "astrbot_exe": r"~\.local\bin\astrbot.exe",
    "astrbot_dir": "astrbot",
    "napcat_dir": "napcat",
    "breeze_dir": "breeze-tts",
    "breeze_model": "breeze-tts-2-q8_0.gguf",
    "icon": "dafeiyu.ico",
}


def load_config():
    try:
        cfg = {**DEFAULTS, **json.load(open(CONFIG_PATH, encoding="utf-8-sig"))}
    except FileNotFoundError:
        cfg = dict(DEFAULTS)
    for key in ("qq_exe", "astrbot_exe", "astrbot_dir", "napcat_dir", "breeze_dir", "icon"):
        # 相对路径按安装目录算
        cfg[key] = os.path.join(ROOT, os.path.expanduser(cfg[key]))
    cfg["bot_qq"] = str(cfg["bot_qq"]).strip()
    return cfg


CFG = load_config()
LOGS = os.path.join(ROOT, "logs")
BREEZE = CFG["breeze_dir"]
NAPCAT = CFG["napcat_dir"]
QQ_EXE = CFG["qq_exe"]
BOT_QQ = CFG["bot_qq"]
ASTRBOT_EXE = CFG["astrbot_exe"]
ASTRBOT_DIR = CFG["astrbot_dir"]
PYTHON = os.path.join(os.path.dirname(sys.executable), "python.exe")
CMD_CONFIG = os.path.join(ASTRBOT_DIR, r"data\cmd_config.json")
GCP_CONFIG = os.path.join(ASTRBOT_DIR, r"data\config\astrbot_plugin_group_chat_plus_config.json")
QR_PNG = os.path.join(NAPCAT, r"cache\qrcode.png")
ICON = CFG["icon"]
VOICE_SETTINGS = os.path.join(ROOT, "voice-settings.json")
VOICE_TMP = os.path.join(ROOT, "voice-candidates")
SAMPLE_TEXT = "在呢在呢，这么晚还不睡，是不是又在偷偷吃夜宵？"
DESIGN_TEXT = "哼，你终于想起来找我啦？我还以为你把我忘了呢。今天有没有给我带好吃的呀？"
STARTUP_LNK = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup\大肥鱼控制台.lnk")
NO_WINDOW = 0x08000000
NEW_GROUP = 0x00000200
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def napcat_token():
    return CFG["napcat_http_token"]


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url, timeout=2):
    try:
        with NO_PROXY.open(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def cmdline(p):
    try:
        return " ".join(p.cmdline())
    except (psutil.Error, OSError):
        return ""


def runs_file(p, filename):
    """进程本身是 filename，或是参数里带着 filename 的 python。只看参数的文件名，免得误伤命令里提到它的别的程序。"""
    try:
        name = (p.name() or "").lower()
        args = p.cmdline()
    except (psutil.Error, OSError):
        return False
    if name == filename:
        return True
    return name.startswith(("python", "astrbot")) and any(os.path.basename(a).lower() == filename for a in args[1:])


def kill_tree(proc):
    try:
        procs = proc.children(recursive=True) + [proc]
    except psutil.Error:
        return
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=5)


class Service:
    def __init__(self, key, name, desc):
        self.key, self.name, self.desc = key, name, desc

    @property
    def log(self):
        return os.path.join(LOGS, f"{self.key}.log")

    def spawn(self, args, cwd, env=None):
        os.makedirs(LOGS, exist_ok=True)
        with open(self.log, "wb") as out:
            subprocess.Popen(args, cwd=cwd, env={**os.environ, **(env or {})}, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW | NEW_GROUP)

    def stop(self):
        for p in self.processes():
            kill_tree(p)


class Breeze(Service):
    def processes(self):
        return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "breeze-server.exe"]

    def running(self):
        return http_ok("http://127.0.0.1:8137/v1/voices")

    def start(self):
        exe = next((os.path.join(d, f) for d, _, fs in os.walk(os.path.join(BREEZE, "bin")) for f in fs
                    if f.lower() == "breeze-server.exe"), None)
        self.spawn([exe, os.path.join(BREEZE, "models", CFG["breeze_model"]), "--host", "127.0.0.1",
                    "--port", "8137", "--voices-dir", os.path.join(BREEZE, "voices"), "--ws-port", "-1", "--webui"],
                   BREEZE)


class Helper(Service):
    def processes(self):
        return [p for p in psutil.process_iter() if runs_file(p, "helper.py") or runs_file(p, "meme-server.py")]

    def running(self):
        return port_open(18765) and port_open(8138)

    def start(self):
        self.spawn([PYTHON, "-u", os.path.join(ROOT, "helper.py")], ROOT)


class AstrBot(Service):
    def processes(self):
        out = []
        for p in psutil.process_iter():
            try:
                args = [a.lower() for a in p.cmdline()]
            except (psutil.Error, OSError):
                continue
            # uv 的 astrbot.exe 启动器自己的命令行是「astrbot run」，它拉起的 python 带着 astrbot.exe 的完整路径
            if args[-1:] == ["run"] and (runs_file(p, "astrbot.exe") or args[0] == "astrbot"):
                out.append(p)
        return out

    def running(self):
        return port_open(6185)

    def start(self):
        self.spawn([ASTRBOT_EXE, "run"], ASTRBOT_DIR,
                   {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"})


class NapCat(Service):
    def processes(self):
        out = []
        for p in psutil.process_iter(["name"]):
            name = (p.info["name"] or "").lower()
            line = cmdline(p)
            # 只认 NapCat 拉起的那个 QQ（带 -q 小号），不碰你自己平时开的 QQ
            if name == "napcatwinbootmain.exe" or (name == "qq.exe" and f"-q {BOT_QQ}" in line
                                                  and "--type=" not in line):
                out.append(p)
        return out

    def running(self):
        return bool(self.processes())

    def connected(self):
        try:
            return any(c.raddr and c.raddr.port == 6199 and c.status == psutil.CONN_ESTABLISHED
                       for c in psutil.net_connections("tcp"))
        except psutil.Error:
            return False

    def login_info(self):
        tok = napcat_token()
        try:
            req = urllib.request.Request("http://127.0.0.1:3000/get_login_info", headers={"Authorization": f"Bearer {tok}"})
            with NO_PROXY.open(req, timeout=2) as r:
                return json.load(r).get("data") or {}
        except Exception:
            return {}

    def start(self):
        env = {"NAPCAT_PATCH_PACKAGE": os.path.join(NAPCAT, "qqnt.json"),
               "NAPCAT_LOAD_PATH": os.path.join(NAPCAT, "loadNapCat.js"),
               "NAPCAT_INJECT_PATH": os.path.join(NAPCAT, "NapCatWinBootHook.dll"),
               "NAPCAT_LAUNCHER_PATH": os.path.join(NAPCAT, "NapCatWinBootMain.exe"),
               "NAPCAT_MAIN_PATH": os.path.join(NAPCAT, "napcat.mjs")}
        self.spawn([env["NAPCAT_LAUNCHER_PATH"], QQ_EXE, env["NAPCAT_INJECT_PATH"], "-q", BOT_QQ], NAPCAT, env)


SERVICES = [
    Breeze("breeze", "Breeze 语音", "在显卡上合成她的声音"),
    Helper("helper", "辅助服务", "表情包下载源 + 语速和换声音"),
    AstrBot("astrbot", "AstrBot", "她的大脑：人格、插件、DeepSeek"),
    NapCat("napcat", "NapCat / QQ", "替她登录 QQ 小号收发消息"),
]
BY_KEY = {s.key: s for s in SERVICES}


def load_json(path):
    return json.load(open(path, encoding="utf-8-sig"))


def save_json(path, data):
    tmp = path + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class App:
    def __init__(self, root, autostart):
        self.root = root
        self.busy = False
        self.status = {}
        root.title("大肥鱼控制台")
        root.geometry("660x700")
        root.minsize(560, 560)
        try:
            root.iconbitmap(ICON)
        except tk.TclError:
            pass
        style = ttk.Style()
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Big.TButton", font=("Microsoft YaHei UI", 11), padding=6)

        head = ttk.Frame(root, padding=(16, 12, 16, 4))
        head.pack(fill="x")
        ttk.Label(head, text="🐟 大肥鱼控制台", style="Title.TLabel").pack(side="left")
        self.summary = ttk.Label(head, text="检查中…")
        self.summary.pack(side="right")

        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=12, pady=8)
        tabs.add(self.build_main(tabs), text="  运行  ")
        tabs.add(self.build_settings(tabs), text="  群聊和语音  ")
        tabs.add(self.build_voice(tabs), text="  声音  ")
        tabs.bind("<<NotebookTabChanged>>",
                  lambda e: self.refresh_voices() if tabs.index("current") == 2 else None)
        tabs.add(self.build_logs(tabs), text="  日志  ")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll()
        self.refresh_log()
        if autostart:
            root.iconify()
            self.run_bg(self.start_all)

    # ---------- 运行页 ----------
    def build_main(self, parent):
        f = ttk.Frame(parent, padding=14)
        self.qq_label = ttk.Label(f, text="QQ：—", font=("Microsoft YaHei UI", 11))
        self.qq_label.pack(anchor="w", pady=(0, 10))
        grid = ttk.Frame(f)
        grid.pack(fill="x")
        self.dots, self.states = {}, {}
        for i, s in enumerate(SERVICES):
            dot = tk.Label(grid, text="●", fg="#999", font=("Segoe UI", 14))
            dot.grid(row=i, column=0, padx=(0, 8), pady=4)
            ttk.Label(grid, text=s.name, width=13).grid(row=i, column=1, sticky="w")
            st = ttk.Label(grid, text="—", width=10)
            st.grid(row=i, column=2, sticky="w")
            ttk.Label(grid, text=s.desc, foreground="#777").grid(row=i, column=3, sticky="w", padx=(4, 8))
            ttk.Button(grid, text="重启", width=6,
                       command=lambda s=s: self.run_bg(lambda: self.restart_one(s))).grid(row=i, column=4)
            self.dots[s.key], self.states[s.key] = dot, st
        grid.columnconfigure(3, weight=1)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=16)
        ttk.Button(btns, text="▶ 一键启动", style="Big.TButton",
                   command=lambda: self.run_bg(self.start_all)).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(btns, text="■ 全部停止", style="Big.TButton",
                   command=lambda: self.run_bg(self.stop_all)).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(btns, text="⟳ 全部重启", style="Big.TButton",
                   command=lambda: self.run_bg(lambda: (self.stop_all(), self.start_all()))).pack(
            side="left", expand=True, fill="x", padx=4)

        more = ttk.Frame(f)
        more.pack(fill="x")
        ttk.Button(more, text="打开 AstrBot 后台", command=lambda: webbrowser.open("http://localhost:6185")).pack(
            side="left", padx=4)
        ttk.Button(more, text="打开安装文件夹", command=lambda: os.startfile(ROOT)).pack(side="left", padx=4)
        ttk.Button(more, text="显示登录二维码", command=self.show_qr).pack(side="left", padx=4)

        self.autostart_var = tk.BooleanVar(value=os.path.exists(STARTUP_LNK))
        ttk.Checkbutton(f, text="开机自动启动大肥鱼（控制台最小化在任务栏）", variable=self.autostart_var,
                        command=self.toggle_autostart).pack(anchor="w", pady=(16, 4))
        self.msg = ttk.Label(f, text="", foreground="#1a6")
        self.msg.pack(anchor="w", pady=(8, 0))
        ttk.Label(f, text="关掉这个窗口不会让大肥鱼下线，她会继续在后台运行。", foreground="#777").pack(
            side="bottom", anchor="w")
        return f

    # ---------- 设置页 ----------
    def build_settings(self, parent):
        f = ttk.Frame(parent, padding=14)
        try:
            g, c = load_json(GCP_CONFIG), load_json(CMD_CONFIG)
        except (OSError, ValueError):
            g, c = {}, {}
        self.vars = {
            "initial": tk.IntVar(value=round(float(g.get("initial_probability", 0.1)) * 100)),
            "after": tk.IntVar(value=round(float(g.get("after_reply_probability", 0.6)) * 100)),
            "duration": tk.IntVar(value=int(g.get("probability_duration", 120))),
            "tts": tk.IntVar(value=round(float(c.get("provider_tts_settings", {}).get("trigger_probability", 0.33)) * 100)),
        }
        rows = [
            ("initial", "潜水时插嘴的概率", 0, 50, "%", "没人喊她时，群友每说一句，她有这么大概率考虑插一句"),
            ("after", "说完话后接话的概率", 0, 100, "%", "她刚说过话后的一段时间里，更容易继续接话"),
            ("duration", "接话状态持续多久", 30, 600, "秒", "过了这段时间就回到潜水状态"),
            ("tts", "回复用语音的概率", 0, 100, "%", "每条回复有这么大概率同时发语音条"),
        ]
        for key, title, lo, hi, unit, hint in rows:
            box = ttk.Frame(f)
            box.pack(fill="x", pady=8)
            top = ttk.Frame(box)
            top.pack(fill="x")
            ttk.Label(top, text=title, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
            val = ttk.Label(top, width=8, anchor="e")
            val.pack(side="right")
            var = self.vars[key]
            fmt = (lambda v, var=var, val=val, unit=unit: val.config(text=f"{int(float(v))} {unit}"))
            ttk.Scale(box, from_=lo, to=hi, variable=var, command=fmt).pack(fill="x")
            fmt(var.get())
            ttk.Label(box, text=hint, foreground="#777").pack(anchor="w")
        ttk.Label(f, text="有人 @她、叫「大肥鱼」或回复她时一定会回，不受上面的概率影响。\n"
                          "群里每条消息她都会看，最近 50 条会带进她的记忆上下文。",
                  foreground="#555").pack(anchor="w", pady=(10, 6))
        ttk.Button(f, text="保存并应用（会重启一下 AstrBot，约 20 秒）", style="Big.TButton",
                   command=lambda: self.run_bg(self.apply_settings)).pack(fill="x", pady=8)
        return f

    # ---------- 声音页 ----------
    def build_voice(self, parent):
        f = ttk.Frame(parent, padding=14)
        vs = self.voice_settings()
        self.voice_ids = []

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="说话速度", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self.speed_label = ttk.Label(top, width=8, anchor="e")
        self.speed_label.pack(side="right")
        self.speed = tk.DoubleVar(value=float(vs.get("speed", 1.0)))
        scale = ttk.Scale(f, from_=0.8, to=2.0, variable=self.speed,
                          command=lambda v: self.speed_label.config(text=f"{float(v):.2f} 倍"))
        scale.pack(fill="x")
        scale.bind("<ButtonRelease-1>", lambda e: self.save_voice_settings(speed=round(self.speed.get(), 2)))
        self.speed_label.config(text=f"{self.speed.get():.2f} 倍")
        ttk.Label(f, text="松开滑块马上生效，不用重启。只变快慢，不变音调。", foreground="#777").pack(anchor="w")

        ttk.Label(f, text="她的声音", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(14, 4))
        row = ttk.Frame(f)
        row.pack(fill="both", expand=True)
        self.voice_list = tk.Listbox(row, height=6, font=("Microsoft YaHei UI", 10), activestyle="none",
                                     exportselection=False)
        self.voice_list.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(row)
        side.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(side, text="设为她的声音", command=self.use_voice).pack(fill="x", pady=2)
        ttk.Button(side, text="▶ 试听", command=self.preview_voice).pack(fill="x", pady=2)
        ttk.Button(side, text="删除", command=self.delete_voice).pack(fill="x", pady=2)
        ttk.Button(side, text="刷新", command=self.refresh_voices).pack(fill="x", pady=2)

        new = ttk.LabelFrame(f, text=" 做一个新声音 ", padding=10)
        new.pack(fill="x", pady=(12, 0))
        r1 = ttk.Frame(new)
        r1.pack(fill="x")
        ttk.Label(r1, text="名字").pack(side="left")
        self.new_name = ttk.Entry(r1, width=14)
        self.new_name.pack(side="left", padx=(6, 12))
        ttk.Label(r1, text="声音描述").pack(side="left")
        self.new_desc = ttk.Entry(r1)
        self.new_desc.insert(0, "二十出头的女生，声音清亮，说话干脆利落")
        self.new_desc.pack(side="left", fill="x", expand=True, padx=(6, 0))
        r2 = ttk.Frame(new)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Button(r2, text="生成 3 个候选", command=lambda: self.run_bg(self.make_candidates)).pack(side="left")
        self.cand_frame = ttk.Frame(r2)
        self.cand_frame.pack(side="left", padx=10)
        self.refresh_voices()
        return f

    def voice_settings(self):
        try:
            return load_json(VOICE_SETTINGS)
        except (OSError, ValueError):
            return {"voice": "dafeiyu", "speed": 1.0, "labels": {}}

    def save_voice_settings(self, **changes):
        vs = self.voice_settings()
        vs.update(changes)
        save_json(VOICE_SETTINGS, vs)
        if "speed" in changes:
            self.say(f"语速已改成 {changes['speed']:.2f} 倍，下一条语音就生效")

    def breeze(self, method, path, body=None, ctype="application/json", timeout=600):
        req = urllib.request.Request("http://127.0.0.1:8137" + path, body, method=method)
        if body is not None:
            req.add_header("Content-Type", ctype)
        with NO_PROXY.open(req, timeout=timeout) as r:
            return r.read()

    def refresh_voices(self):
        def work():
            try:
                ids = [v["id"] for v in json.loads(self.breeze("GET", "/v1/voices", timeout=5)) if v.get("saved")]
            except Exception:
                ids = None
            self.root.after(0, lambda: self.show_voices(ids))
        threading.Thread(target=work, daemon=True).start()

    def show_voices(self, ids):
        self.voice_list.delete(0, "end")
        self.voice_ids = ids or []
        if ids is None:
            self.voice_list.insert("end", "（Breeze 语音没在运行，先到「运行」页启动）")
            return
        vs = self.voice_settings()
        # 按加进来的先后排（labels 的顺序），没起名的放最后
        order = list(vs.get("labels", {}))
        self.voice_ids.sort(key=lambda v: order.index(v) if v in order else len(order))
        for vid in self.voice_ids:
            mark = "  ✔ 正在用" if vid == vs.get("voice") else ""
            self.voice_list.insert("end", f"{vs.get('labels', {}).get(vid, vid)}{mark}")
            if mark:
                self.voice_list.selection_set("end")

    def selected_voice(self):
        sel = self.voice_list.curselection()
        if not sel or sel[0] >= len(self.voice_ids):
            self.say("先在列表里点选一个声音", "#c80")
            return None
        return self.voice_ids[sel[0]]

    def play(self, path):
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def speak_to_file(self, voice, out, text=SAMPLE_TEXT):
        body = json.dumps({"model": "tts-1", "voice": voice, "keep_voice": True, "input": text,
                           "response_format": "wav", "speed": round(self.speed.get(), 2)}).encode()
        req = urllib.request.Request("http://127.0.0.1:8138/v1/audio/speech", body, {"Content-Type": "application/json"})
        with NO_PROXY.open(req, timeout=600) as r:
            open(out, "wb").write(r.read())

    def use_voice(self):
        vid = self.selected_voice()
        if vid:
            self.save_voice_settings(voice=vid)
            self.refresh_voices()
            self.say(f"已换成「{self.voice_settings().get('labels', {}).get(vid, vid)}」，下一条语音就生效")

    def preview_voice(self):
        vid = self.selected_voice()
        if not vid:
            return

        def work():
            self.say("正在合成试听…", "#555")
            os.makedirs(VOICE_TMP, exist_ok=True)
            out = os.path.join(VOICE_TMP, "preview.wav")
            self.speak_to_file(vid, out)
            self.play(out)
            self.say("")
        self.run_bg(work)

    def delete_voice(self):
        vid = self.selected_voice()
        if not vid:
            return
        if vid == self.voice_settings().get("voice"):
            messagebox.showinfo("删除声音", "这个声音正在用，先换成别的再删。")
            return
        label = self.voice_settings().get("labels", {}).get(vid, vid)
        if not messagebox.askyesno("删除声音", f"确定删除「{label}」吗？删了就找不回来了。"):
            return
        try:
            self.breeze("DELETE", f"/v1/voices/{vid}", timeout=10)
        except urllib.error.HTTPError as e:
            self.say(f"删除失败（{e.code}）", "#c33")
            return
        vs = self.voice_settings()
        vs.get("labels", {}).pop(vid, None)
        save_json(VOICE_SETTINGS, vs)
        self.refresh_voices()
        self.say(f"已删除「{label}」")

    def make_candidates(self):
        desc = self.new_desc.get().strip()
        if not desc:
            self.say("先写一句声音描述", "#c80")
            return
        os.makedirs(VOICE_TMP, exist_ok=True)
        self.root.after(0, lambda: [w.destroy() for w in self.cand_frame.winfo_children()])
        files = []
        for i in range(1, 4):
            self.say(f"正在生成候选 {i}/3…", "#555")
            body = json.dumps({"input": DESIGN_TEXT, "instructions": desc, "cfg_scale": 4,
                               "seed": random.randint(1, 99999), "response_format": "pcm"}).encode()
            pcm = self.breeze("POST", "/v1/audio/speech", body)
            out = os.path.join(VOICE_TMP, f"new-{i}.wav")
            with wave.open(out, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
            files.append(out)

        def show():
            for i, path in enumerate(files, 1):
                box = ttk.Frame(self.cand_frame)
                box.pack(side="left", padx=4)
                ttk.Button(box, text=f"▶ {i}", width=4, command=lambda p=path: self.play(p)).pack(side="left")
                ttk.Button(box, text="用这个", width=6,
                           command=lambda p=path: self.run_bg(lambda: self.save_candidate(p, desc))).pack(side="left")
        self.root.after(0, show)
        self.say("3 个候选好了，点 ▶ 听，喜欢哪个就点它旁边的「用这个」")

    def save_candidate(self, path, desc):
        name = self.new_name.get().strip() or desc[:8]
        vid = "v" + time.strftime("%m%d%H%M%S")
        b = uuid.uuid4().hex
        parts = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
                         for k, v in (("ref_text", DESIGN_TEXT), ("name", vid), ("description", desc)))
        head = (f"--{b}\r\nContent-Disposition: form-data; name=\"ref_audio\"; filename=\"ref.wav\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode()
        body = parts + head + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
        self.breeze("POST", "/v1/voices", body, f"multipart/form-data; boundary={b}", timeout=120)
        vs = self.voice_settings()
        vs.setdefault("labels", {})[vid] = name
        save_json(VOICE_SETTINGS, vs)
        self.root.after(0, self.refresh_voices)
        self.say(f"存好了「{name}」，在列表里选中它点「设为她的声音」就能用")

    # ---------- 日志页 ----------
    def build_logs(self, parent):
        f = ttk.Frame(parent, padding=10)
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.log_choice = tk.StringVar(value="astrbot")
        for s in SERVICES:
            ttk.Radiobutton(top, text=s.name, value=s.key, variable=self.log_choice,
                            command=self.refresh_log).pack(side="left", padx=4)
        self.log_text = tk.Text(f, wrap="none", font=("Consolas", 9), bg="#1e1e1e", fg="#ddd")
        self.log_text.pack(fill="both", expand=True, pady=(8, 0))
        return f

    def refresh_log(self):
        s = BY_KEY[self.log_choice.get()]
        try:
            with open(s.log, "rb") as fh:
                fh.seek(max(0, os.path.getsize(s.log) - 60000))
                text = ANSI.sub("", fh.read().decode("utf-8", "replace"))
        except OSError:
            text = "（还没有日志，用控制台启动后才会有）"
        at_bottom = self.log_text.yview()[1] > 0.98
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", text)
        if at_bottom:
            self.log_text.see("end")
        self.root.after(3000, self.refresh_log)

    # ---------- 动作 ----------
    def say(self, text, color="#1a6"):
        self.root.after(0, lambda: self.msg.config(text=text, foreground=color))

    def run_bg(self, fn):
        if self.busy:
            self.say("上一个操作还没做完，稍等一下", "#c80")
            return
        self.busy = True

        def work():
            try:
                fn()
            except Exception as e:
                self.say(f"出错了：{e}", "#c33")
            finally:
                self.busy = False
        threading.Thread(target=work, daemon=True).start()

    def wait_for(self, check, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if check():
                return True
            time.sleep(1)
        return False

    def start_one(self, s):
        if s.running():
            return
        self.say(f"正在启动 {s.name}…", "#555")
        s.start()
        if isinstance(s, NapCat):
            if not self.wait_for(s.connected, 60):
                self.say("NapCat 没能自动登录，可能要重新扫码，点「显示登录二维码」", "#c80")
                self.root.after(0, self.show_qr)
        else:
            self.wait_for(s.running, 120)

    def start_all(self):
        for s in SERVICES:
            self.start_one(s)
        self.say("大肥鱼已上线 ✔" if BY_KEY["napcat"].connected() else "启动完成，但 QQ 还没连上，看看日志",
                 "#1a6" if BY_KEY["napcat"].connected() else "#c80")

    def stop_all(self):
        for s in reversed(SERVICES):
            self.say(f"正在停止 {s.name}…", "#555")
            s.stop()
        self.say("已全部停止，大肥鱼下线了", "#555")

    def restart_one(self, s):
        s.stop()
        time.sleep(1)
        self.start_one(s)
        self.say(f"{s.name} 已重启")

    def apply_settings(self):
        v = {k: var.get() for k, var in self.vars.items()}
        astrbot = BY_KEY["astrbot"]
        self.say("正在保存设置，AstrBot 会重启一下…", "#555")
        astrbot.stop()
        g = load_json(GCP_CONFIG)
        g["initial_probability"] = round(v["initial"] / 100, 2)
        g["after_reply_probability"] = round(v["after"] / 100, 2)
        g["probability_duration"] = int(v["duration"])
        save_json(GCP_CONFIG, g)
        c = load_json(CMD_CONFIG)
        c.setdefault("provider_tts_settings", {})["trigger_probability"] = round(v["tts"] / 100, 2)
        save_json(CMD_CONFIG, c)
        self.start_one(astrbot)
        ok = self.wait_for(BY_KEY["napcat"].connected, 40)
        self.say("设置已生效 ✔" if ok else "设置已保存，但 QQ 还没重新连上，看看日志", "#1a6" if ok else "#c80")

    def toggle_autostart(self):
        if self.autostart_var.get():
            ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($args[0]);"
                  "$s.TargetPath=$args[1];$s.Arguments=$args[2];$s.WorkingDirectory=$args[3];"
                  "$s.IconLocation=$args[4];$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps, STARTUP_LNK, sys.executable,
                            f'"{os.path.abspath(__file__)}" --autostart', os.path.dirname(__file__), ICON],
                           creationflags=NO_WINDOW)
            self.say("已设置开机自动启动")
        else:
            try:
                os.remove(STARTUP_LNK)
            except OSError:
                pass
            self.say("已取消开机自动启动", "#555")

    def show_qr(self):
        win = tk.Toplevel(self.root)
        win.title("用小号手机 QQ 扫码")
        lbl = ttk.Label(win, text="二维码还没生成，等几秒…", padding=12)
        lbl.pack()
        ttk.Label(win, text=f"用小号（{BOT_QQ}）的手机 QQ 扫码并确认，登录后这个窗口会自己关掉",
                  padding=(12, 0, 12, 12)).pack()
        state = {"mtime": 0}

        def tick():
            if not win.winfo_exists():
                return
            if BY_KEY["napcat"].connected():
                win.destroy()
                return
            try:
                m = os.path.getmtime(QR_PNG)
                if m != state["mtime"]:
                    state["mtime"] = m
                    img = tk.PhotoImage(file=QR_PNG)
                    lbl.config(image=img, text="")
                    lbl.image = img
            except (OSError, tk.TclError):
                pass
            win.after(2000, tick)
        tick()

    # ---------- 状态轮询 ----------
    def poll(self):
        def work():
            st = {s.key: s.running() for s in SERVICES}
            nap = BY_KEY["napcat"]
            st["connected"] = nap.connected()
            st["login"] = nap.login_info() if st["connected"] else {}
            self.root.after(0, lambda: self.show_status(st))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(3000, self.poll)

    def show_status(self, st):
        for s in SERVICES:
            up = st[s.key]
            if s.key == "napcat" and up and not st["connected"]:
                color, text = "#e90", "登录中"
            else:
                color, text = ("#2b2", "运行中") if up else ("#c33", "已停止")
            self.dots[s.key].config(fg=color)
            self.states[s.key].config(text=text)
        login = st.get("login") or {}
        if st["connected"] and login:
            self.qq_label.config(text=f"QQ：{login.get('nickname')}（{login.get('user_id')}）在线 · 群聊和私聊都在听")
        else:
            self.qq_label.config(text="QQ：未连接")
        all_up = all(st[s.key] for s in SERVICES) and st["connected"]
        none_up = not any(st[s.key] for s in SERVICES)
        self.summary.config(text="🟢 大肥鱼在线" if all_up else ("⚪ 已下线" if none_up else "🟠 部分运行"))

    def on_close(self):
        ans = messagebox.askyesnocancel("关闭控制台", "关掉控制台后大肥鱼会继续在后台运行。\n\n要不要同时让她下线？\n"
                                        "「是」= 停止大肥鱼并关闭\n「否」= 只关控制台，她继续在线")
        if ans is None:
            return
        if ans:
            self.stop_all()
        self.root.destroy()


def main():
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 18799))
    except OSError:
        tk.Tk().withdraw()
        messagebox.showinfo("大肥鱼控制台", "控制台已经开着了，看看任务栏。")
        return
    if not BOT_QQ:
        tk.Tk().withdraw()
        messagebox.showwarning("大肥鱼控制台", "还没配置：把 config.example.json 复制成 config.json，"
                                               "至少填上 bot_qq（机器人小号）再打开。")
        return
    root = tk.Tk()
    App(root, "--autostart" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()

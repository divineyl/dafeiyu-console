"""导入控制台之前先准备 Windows 专用模块。这个文件必须最先被测试引用。"""
import os
import sys
import types

os.environ.setdefault("APPDATA", os.path.join("/tmp", "dafeiyu-appdata"))
os.makedirs(os.environ["APPDATA"], exist_ok=True)

if "winsound" not in sys.modules:
    winsound = types.ModuleType("winsound")
    winsound.SND_FILENAME = 1
    winsound.SND_ASYNC = 2
    winsound.calls = []

    def play_sound(path, flags):
        winsound.calls.append((path, flags))

    winsound.PlaySound = play_sound
    sys.modules["winsound"] = winsound

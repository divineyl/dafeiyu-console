# 🐟 大肥鱼控制台 dafeiyu-console

把「大肥鱼」（DeepSeek 蓝鲸的社区二创形象）养进 QQ 之后，用一个窗口管住她：一键上线 / 下线、调群聊插嘴频率、调说话语速、在十几种声音之间切换，再也不用开一堆黑窗口。

适用于 **Windows + AstrBot + NapCat + DeepSeek + Breeze TTS** 这一套部署（也就是 [dafeiyu-config-pack](https://github.com/zexuanw958-svg/dafeiyu-config-pack) 那条路线）。

## 功能

**控制台（`dafeiyu_console.pyw`）**
- **运行**：Breeze 语音、辅助服务、AstrBot、NapCat 四个程序的状态灯，一键启动 / 停止 / 重启，全部后台无窗口运行；关掉控制台她照常在线
- QQ 掉线要扫码时自动弹出二维码；可勾选开机自启
- **群聊和语音**：滑块调「潜水时插嘴的概率」「说完话后接话的概率」「接话持续多久」「回复用语音的概率」
- **声音**：语速 0.8～2.0 倍（只变快慢不变调，松手即生效）；声音列表试听、切换、删除；写一句描述就能生成 3 个新声音候选
- **日志**：四个程序的输出都能在窗口里看

**辅助服务（`helper.py`）**
- `127.0.0.1:8138`：AstrBot ↔ Breeze 之间的语音中转，按设置换声音、用 ffmpeg `atempo` 变速
- `127.0.0.1:18765`：本机表情包源。表情包一套就有几百 MB，AstrBot 下载时不走系统代理，可能要下几个小时；先用 `setup.py memes` 下到本地，导入时几秒装完

**配置工具（`tools/`）**
- `setup.py link`：连通 NapCat 和 AstrBot（反向 WebSocket + 本机 HTTP 接口），全部只听 127.0.0.1、带随机密钥，NapCat 管理页也收回到本机
- `setup.py tts`：AstrBot 的语音改走 Breeze（经辅助服务）
- `setup.py group 群号`：让她在某个群潜水聊天
- `setup.py memes` / `setup.py voices` / `setup.py shortcut`：下表情包、做 12 个预设声音、放桌面快捷方式
- `import_pack.py`：跑配置包的导入工具，除后台密码外的问题都按 `config.json` 自动回答，表情包从本机取

## 架构

```
QQ 小号 ⇄ NapCat ──反向 WS :6199──▶ AstrBot ──▶ DeepSeek API
                └─ HTTP :3000（控制台查在线状态）   │
                                                   └─ 语音 ─▶ 辅助服务 :8138 ─▶ Breeze TTS :8137（本机显卡）
                                    表情包插件 ─▶ 辅助服务 :18765（本机表情包）
```

## 安装

需要：Windows 10/11、Python 3.12（自带 tkinter）、已装好的 QQ NT、一个 **QQ 小号**、DeepSeek API Key。想用 Breeze 本机语音还要一块 3 GB 以上显存的显卡。

1. **把仓库克隆到一个不含中文和空格的目录**，比如 `E:\dafeiyu`。这个目录就是安装目录，AstrBot、NapCat、Breeze 等都装在它下面（`.gitignore` 会把它们挡在仓库外）。
   ```
   git clone https://github.com/divineyl/dafeiyu-console E:\dafeiyu
   cd /d E:\dafeiyu
   pip install -r requirements.txt
   copy config.example.json config.json
   ```
   打开 `config.json`，填上 `bot_qq`（小号）和 `owner_qq`（你的大号）。

2. **装 AstrBot**（需要 [uv](https://docs.astral.sh/uv/)）：
   ```
   uv tool install astrbot --python 3.12
   mkdir astrbot && cd astrbot && astrbot init -y && astrbot run
   ```
   第一次运行会在终端打印后台的初始账号密码，记下来后 Ctrl+C 停掉。

3. **装 NapCat**：从 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 `NapCat.Shell.zip` 解压到 `napcat\`，运行 `napcat\launcher-user.bat`，用**小号**扫码登录一次后关掉。

4. **连通两边**：`python tools\setup.py link`

5. **装 Breeze 语音**（可选）：把程序和模型放进 `breeze-tts\`（目录结构 `breeze-tts\bin\...\breeze-server.exe`、`breeze-tts\models\breeze-tts-2-q8_0.gguf`），可以用配置包里的「装Breeze语音.bat」，装到这个目录即可。

6. **启动**：双击 `dafeiyu_console.pyw`（或 `python tools\setup.py shortcut` 放个桌面快捷方式），点「一键启动」。然后浏览器打开 `http://localhost:6185` 登录 AstrBot 后台，在「服务提供商」里添加 DeepSeek 并填你的 API Key。

7. **导入大肥鱼人格、插件和表情包**：
   ```
   git clone https://github.com/zexuanw958-svg/dafeiyu-config-pack
   python tools\setup.py memes
   python tools\import_pack.py
   ```

8. **语音、声音和群聊**（改配置前先在控制台点「全部停止」或单独停掉 AstrBot，改完再启动）：
   ```
   python tools\setup.py tts
   python tools\setup.py voices
   python tools\setup.py group 你的群号
   ```

之后日常只需要控制台。用大号私聊小号说句「在吗」试试，改过人格后先发 `/reset`。

## 注意

- **封号风险**：NapCat 走的是非官方的个人号协议，腾讯风控可能冻结账号。**只用小号**，别让她刷屏，群里主动冒泡也建议先别开。
- **别把端口暴露到网上**：本项目所有服务都只监听 127.0.0.1 并带密钥，别自己改成 0.0.0.0。
- **Breeze 模型只许研究和非商业使用**，做出来的声音别拿去商用。预设声音都是按文字描述生成的，不是克隆某个真人；自己克隆声音时请只用你有权使用的录音。
- 本项目与 DeepSeek、腾讯 QQ 无关，大肥鱼形象、表情包和配置包的版权归各自作者。

## 致谢

[AstrBot](https://github.com/AstrBotDevs/AstrBot) · [NapCatQQ](https://github.com/NapNeko/NapCatQQ) · [dafeiyu-config-pack](https://github.com/zexuanw958-svg/dafeiyu-config-pack) · [Breeze-TTS-2.cpp](https://github.com/HoppouAI/Breeze-TTS-2.cpp) · [蓝色大肥鱼表情包](https://github.com/anka-afk/blue-fat-fish-meme-pack) · [DeepSeek 二创表情包](https://github.com/anka-afk/deepseek-fanart-meme-pack) · 踩坑经验参考 [dafeiyu-build-log](https://github.com/kibsmdadah-lgtm/dafeiyu-build-log)

## 许可

MIT

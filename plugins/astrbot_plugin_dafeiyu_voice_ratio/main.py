"""群聊、私聊分开设回复语音的概率。

AstrBot 自己只有一个全局的「语音触发概率」。这个插件在消息发出前按会话类型掷一次骰子，
没掷中就给这条消息打上「这次不发语音」的标记；配合把全局概率设成 1，就等于群聊、私聊各用各的概率。

AstrBot 没有现成的「单条消息跳过语音」开关，所以这里包了一层 SessionServiceManager.should_process_tts_request，
看到标记就返回 False，没标记照原样走（包括 /tts 关掉某个会话的语音）。插件停用或卸载时会还原。
"""
import random

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.session_llm_manager import SessionServiceManager

SKIP_KEY = "dafeiyu_skip_tts"
ORIG_ATTR = "_dafeiyu_voice_ratio_orig"


def _install_patch():
    if hasattr(SessionServiceManager, ORIG_ATTR):
        return
    orig = SessionServiceManager.__dict__["should_process_tts_request"]
    original_fn = SessionServiceManager.should_process_tts_request

    async def should_process_tts_request(event):
        if event.get_extra(SKIP_KEY):
            return False
        return await original_fn(event)

    setattr(SessionServiceManager, ORIG_ATTR, orig)
    SessionServiceManager.should_process_tts_request = staticmethod(should_process_tts_request)


def _remove_patch():
    orig = SessionServiceManager.__dict__.get(ORIG_ATTR)
    if orig is not None:
        SessionServiceManager.should_process_tts_request = orig
        delattr(SessionServiceManager, ORIG_ATTR)


def _prob(value, default):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


class DafeiyuVoiceRatio(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        _install_patch()
        logger.info(f"[dafeiyu_voice_ratio] 群聊 {self.group_prob:.0%}、私聊 {self.private_prob:.0%} 的回复带语音")

    @property
    def group_prob(self):
        return _prob(self.conf.get("group_probability"), 0.2)

    @property
    def private_prob(self):
        return _prob(self.conf.get("private_probability"), 0.4)

    @filter.on_decorating_result(priority=100)
    async def roll(self, event: AstrMessageEvent):
        p = self.private_prob if event.is_private_chat() else self.group_prob
        if random.random() >= p:
            event.set_extra(SKIP_KEY, True)

    async def terminate(self):
        _remove_patch()

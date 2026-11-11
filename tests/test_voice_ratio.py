"""群聊 / 私聊语音概率插件。AstrBot 不在测试环境里，这里放一套假的接口。"""
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import patch


def install_astrbot_fakes():
    logger = types.SimpleNamespace(messages=[])
    logger.info = logger.messages.append

    astrbot = types.ModuleType("astrbot")
    astrbot.logger = logger
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    config_pkg = types.ModuleType("astrbot.core.config")
    config_mod = types.ModuleType("astrbot.core.config.astrbot_config")
    star_pkg = types.ModuleType("astrbot.core.star")
    session_mod = types.ModuleType("astrbot.core.star.session_llm_manager")

    class filter:  # noqa: N801 - 跟 AstrBot 的写法保持一致
        @staticmethod
        def on_decorating_result(priority=0):
            def deco(fn):
                fn.priority = priority
                return fn
            return deco

    class Context:
        pass

    class Star:
        def __init__(self, context):
            self.context = context

    class AstrBotConfig(dict):
        pass

    class SessionServiceManager:
        calls = 0
        result = True

        @staticmethod
        async def should_process_tts_request(event):
            SessionServiceManager.calls += 1
            return SessionServiceManager.result

    event_mod.AstrMessageEvent = object
    event_mod.filter = filter
    star_mod.Context = Context
    star_mod.Star = Star
    config_mod.AstrBotConfig = AstrBotConfig
    session_mod.SessionServiceManager = SessionServiceManager
    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event_mod,
        "astrbot.api.star": star_mod,
        "astrbot.core": core,
        "astrbot.core.config": config_pkg,
        "astrbot.core.config.astrbot_config": config_mod,
        "astrbot.core.star": star_pkg,
        "astrbot.core.star.session_llm_manager": session_mod,
    }.items():
        sys.modules[name] = module
    return logger, SessionServiceManager


def load_plugin():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plugins", "astrbot_plugin_dafeiyu_voice_ratio", "main.py",
    )
    spec = importlib.util.spec_from_file_location("dafeiyu_voice_ratio_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOGGER, MANAGER = install_astrbot_fakes()
plugin = load_plugin()


class Event:
    def __init__(self, private):
        self.private = private
        self.extra = {}

    def is_private_chat(self):
        return self.private

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_extra(self, key):
        return self.extra.get(key)


class ProbabilityTests(unittest.TestCase):
    def test_values_are_clamped_and_bad_input_uses_the_default(self):
        cases = [
            (None, 0.2, 0.2),
            ("", 0.4, 0.4),
            ("nope", 0.2, 0.2),
            (float("nan"), 0.2, 0.2),
            (float("inf"), 0.4, 0.4),
            (float("-inf"), 0.2, 0.2),
            (-0.2, 0.2, 0.0),
            (1.8, 0.4, 1.0),
            ("0.25", 0.2, 0.25),
            (0, 0.4, 0.0),
            (1, 0.2, 1.0),
        ]
        for value, default, expected in cases:
            self.assertEqual(plugin._prob(value, default), expected)

    def test_properties_follow_config_changes(self):
        star = plugin.DafeiyuVoiceRatio(object(), {"group_probability": 0.15, "private_probability": "0.7"})
        self.assertEqual(star.group_prob, 0.15)
        self.assertEqual(star.private_prob, 0.7)
        star.conf["group_probability"] = "bad"
        star.conf["private_probability"] = 5
        self.assertEqual(star.group_prob, 0.2)
        self.assertEqual(star.private_prob, 1.0)
        self.assertTrue(any("15%" in line and "70%" in line for line in LOGGER.messages))
        self.assertEqual(star.roll.priority, 100)


class RollAndPatchTests(unittest.TestCase):
    def setUp(self):
        MANAGER.calls = 0
        MANAGER.result = True
        plugin._remove_patch()
        self.star = plugin.DafeiyuVoiceRatio(object(), {
            "group_probability": 0.3,
            "private_probability": 0.8,
        })

    def tearDown(self):
        plugin._remove_patch()

    def test_private_and_group_use_different_probabilities(self):
        async def roll(private, number):
            event = Event(private)
            with patch.object(plugin.random, "random", return_value=number):
                await self.star.roll(event)
            return event.extra.get(plugin.SKIP_KEY, False)

        self.assertTrue(self.loop(roll(True, 0.8)))
        self.assertFalse(self.loop(roll(True, 0.79)))
        self.assertTrue(self.loop(roll(False, 0.3)))
        self.assertFalse(self.loop(roll(False, 0.0)))

    def test_zero_and_one_are_exact(self):
        self.star.conf["group_probability"] = 0
        self.star.conf["private_probability"] = 1
        group = Event(False)
        private = Event(True)
        self.loop(self.star.roll(group))
        self.loop(self.star.roll(private))
        self.assertTrue(group.extra.get(plugin.SKIP_KEY))
        self.assertNotIn(plugin.SKIP_KEY, private.extra)

    def test_patch_skips_marked_events_and_otherwise_calls_original(self):
        skipped = Event(False)
        skipped.set_extra(plugin.SKIP_KEY, True)
        plain = Event(True)
        MANAGER.result = True
        self.assertFalse(self.loop(MANAGER.should_process_tts_request(skipped)))
        self.assertEqual(MANAGER.calls, 0)
        self.assertTrue(self.loop(MANAGER.should_process_tts_request(plain)))
        self.assertEqual(MANAGER.calls, 1)
        MANAGER.result = False
        self.assertFalse(self.loop(MANAGER.should_process_tts_request(Event(True))))

    def test_reload_does_not_wrap_twice_and_terminate_restores(self):
        original = MANAGER.__dict__[plugin.ORIG_ATTR]
        plugin.DafeiyuVoiceRatio(object(), {})
        self.loop(MANAGER.should_process_tts_request(Event(True)))
        self.assertEqual(MANAGER.calls, 1)
        self.loop(self.star.terminate())
        self.loop(self.star.terminate())
        self.assertNotIn(plugin.ORIG_ATTR, MANAGER.__dict__)
        self.assertIs(MANAGER.__dict__["should_process_tts_request"], original)
        MANAGER.calls = 0
        self.assertTrue(self.loop(MANAGER.should_process_tts_request(Event(True))))
        self.assertEqual(MANAGER.calls, 1)

    def loop(self, awaitable):
        import asyncio
        return asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()

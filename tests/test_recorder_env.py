"""build_recorder_env: the env handed to the detached recorder.

Covers the confirmed Gherkin scenarios plus the standard battery categories
that apply to a pure dict->dict function (input anomalies, idempotence,
round-trip, invariants, environment-dependent values).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "relay", "scripts"))

from relay_common import build_recorder_env

KEY = "ANTHROPIC_API_KEY"
KEEP = "RELAY_KEEP_API_KEY"


class TestDefaultClearsKey(unittest.TestCase):
    def test_key_is_removed_by_default(self):
        env = build_recorder_env({KEY: "sk-ant-xxx"})
        self.assertNotIn(KEY, env)

    def test_key_is_kept_when_opted_out(self):
        env = build_recorder_env({KEY: "sk-ant-xxx", KEEP: "1"})
        self.assertEqual(env[KEY], "sk-ant-xxx")

    def test_missing_key_does_not_raise(self):
        env = build_recorder_env({})
        self.assertNotIn(KEY, env)

    def test_missing_key_with_opt_out_does_not_raise(self):
        env = build_recorder_env({KEEP: "1"})
        self.assertNotIn(KEY, env)


class TestOptOutValueIsStrict(unittest.TestCase):
    """Only the exact string "1" opts out, matching is_disabled/is_reentry."""

    def test_values_other_than_one_still_clear(self):
        for value in ("true", "TRUE", "yes", "0", "", " ", "1 ", " 1", "01", "11"):
            with self.subTest(value=value):
                env = build_recorder_env({KEY: "sk-ant-xxx", KEEP: value})
                self.assertNotIn(KEY, env)


class TestLegacyVariableIgnored(unittest.TestCase):
    def test_clear_api_key_does_not_change_the_result(self):
        env = build_recorder_env({KEY: "sk-ant-xxx", "RELAY_CLEAR_API_KEY": "1"})
        self.assertNotIn(KEY, env)

    def test_clear_api_key_cannot_override_the_opt_out(self):
        env = build_recorder_env(
            {KEY: "sk-ant-xxx", "RELAY_CLEAR_API_KEY": "1", KEEP: "1"})
        self.assertEqual(env[KEY], "sk-ant-xxx")


class TestReentryGuard(unittest.TestCase):
    """Dropping this makes the recorder's own session re-fire SessionEnd."""

    def test_guard_is_always_set(self):
        for base in ({}, {KEY: "sk-ant-xxx"}, {KEEP: "1"}, {"RELAY_HOOK_ACTIVE": "0"}):
            with self.subTest(base=base):
                self.assertEqual(build_recorder_env(base)["RELAY_HOOK_ACTIVE"], "1")


class TestPurity(unittest.TestCase):
    def test_caller_env_is_not_mutated(self):
        base = {KEY: "sk-ant-xxx", "PATH": "/usr/bin"}
        build_recorder_env(base)
        self.assertEqual(base, {KEY: "sk-ant-xxx", "PATH": "/usr/bin"})

    def test_os_environ_is_not_mutated(self):
        os.environ["RELAY_TEST_CANARY"] = "kept"
        try:
            build_recorder_env(os.environ)
            self.assertEqual(os.environ["RELAY_TEST_CANARY"], "kept")
        finally:
            del os.environ["RELAY_TEST_CANARY"]


class TestUnrelatedSettingsPassThrough(unittest.TestCase):
    def test_other_relay_settings_survive(self):
        base = {"RELAY_SCOPE": "C:\\claude-projects", "RELAY_MODEL": "claude-haiku-4-5",
                "RELAY_MIN_USER_MSGS": "3", "RELAY_DISABLED": "1"}
        env = build_recorder_env(base)
        for k, v in base.items():
            self.assertEqual(env[k], v)

    def test_other_anthropic_variables_survive(self):
        base = {KEY: "sk-ant-xxx", "ANTHROPIC_AUTH_TOKEN": "tok",
                "CLAUDE_CODE_USE_BEDROCK": "1"}
        env = build_recorder_env(base)
        self.assertNotIn(KEY, env)
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "tok")
        self.assertEqual(env["CLAUDE_CODE_USE_BEDROCK"], "1")


class TestIdempotence(unittest.TestCase):
    def test_applying_twice_matches_applying_once(self):
        for base in ({KEY: "sk-ant-xxx"}, {KEY: "sk-ant-xxx", KEEP: "1"}, {}):
            with self.subTest(base=base):
                once = build_recorder_env(base)
                self.assertEqual(build_recorder_env(once), once)


class TestEnvironmentDependentValues(unittest.TestCase):
    def test_non_ascii_values_survive(self):
        base = {"RELAY_SCOPE": "C:\\プロジェクト", "GREETING": "こんにちは 🐈"}
        env = build_recorder_env(base)
        self.assertEqual(env["RELAY_SCOPE"], "C:\\プロジェクト")
        self.assertEqual(env["GREETING"], "こんにちは 🐈")

    def test_empty_key_value_is_still_cleared(self):
        self.assertNotIn(KEY, build_recorder_env({KEY: ""}))

    def test_empty_key_value_is_kept_when_opted_out(self):
        self.assertEqual(build_recorder_env({KEY: "", KEEP: "1"})[KEY], "")


if __name__ == "__main__":
    unittest.main()

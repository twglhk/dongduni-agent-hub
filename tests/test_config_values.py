import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import config as config_mod
from agent_hub.config import ConfigError
from tests.helpers import make_home


def load_with(**extra):
    cfg = make_home(**extra)
    return cfg


class TypedValuesTest(unittest.TestCase):
    def test_a_word_for_a_number_is_a_config_error(self) -> None:
        for key in ("max_lanes", "max_instances", "light_lanes", "memory_limit_mb", "scratch_grace",
                    "branch_sync_seconds", "opencode_port"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_with(**{key: "two"})

    def test_a_list_for_a_number_is_a_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            load_with(max_lanes=[2])

    def test_numbers_in_strings_still_load(self) -> None:
        self.assertEqual(load_with(max_lanes="3").max_lanes, 3)

    def test_a_string_is_not_a_boolean(self) -> None:
        for key in ("leftover_sweep", "branch_sync", "branch_sync_push", "worktree", "require_model_lines",
                    "require_line"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_with(**{key: "false"})

    def test_allowed_hosts_are_bare_lower_case_names(self) -> None:
        self.assertEqual(make_home(web={"allowed_hosts": ["Box.Local", "box.local", "a-b.ts.net"]}).allowed_hosts,
                         ("box.local", "a-b.ts.net"))
        self.assertEqual(make_home().allowed_hosts, ())
        for bad in ("box.local:8787", "http://box.local", "box/local", "", 5, "-x"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                make_home(web={"allowed_hosts": [bad]})
        with self.assertRaises(ConfigError):
            make_home(web={"allowed_hosts": "box.local"})

    def test_a_lane_worktree_string_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            make_home(lanes={"hub": {"runtime": "claude"}, "docs": {"runtime": "codex", "worktree": "no"}})

    def test_real_booleans_load(self) -> None:
        cfg = load_with(branch_sync=True, branch_sync_push=False)
        self.assertTrue(cfg.branch_sync)
        self.assertFalse(cfg.branch_sync_push)


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        patch = mock.patch.dict(os.environ, {"AGENT_HUB_HOME": str(self.dir)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_broken_registry_is_kept_and_not_overwritten(self) -> None:
        path = config_mod.registry_path()
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            config_mod.register(Path(tempfile.mkdtemp()))
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")
        self.assertEqual(path.with_name(path.name + ".bak").read_text(encoding="utf-8"), "{not json")

    def test_register_writes_through_a_temp_file(self) -> None:
        home = Path(tempfile.mkdtemp())
        slug = config_mod.register(home)
        path = config_mod.registry_path()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["workspaces"][slug]["home"], str(home.resolve()))
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

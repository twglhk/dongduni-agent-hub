"""Machine card: readings graded against this machine's own cores and RAM; unknown is None, never 0."""

import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from agent_hub import machine
from agent_hub.engine import Engine
from agent_hub.store import Store
from agent_hub.web import make_handler
from tests.helpers import Clock, FakeRuntime, make_home

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               65536.
Pages active:                            900000.
Pages inactive:                          131072.
Pages speculative:                        65536.
"""
MEMINFO = """MemTotal:       16384000 kB
MemFree:          512000 kB
MemAvailable:    1638400 kB
SwapTotal:       4096000 kB
SwapFree:        2048000 kB
"""


class ParseTest(unittest.TestCase):
    def test_vm_stat_free_inactive_speculative(self) -> None:
        self.assertAlmostEqual(machine.parse_vm_stat(VM_STAT), 262144 * 16384 / 1024 ** 3)
        self.assertIsNone(machine.parse_vm_stat("garbage"))

    def test_swapusage_and_meminfo(self) -> None:
        self.assertAlmostEqual(machine.parse_swapusage("total = 2048.00M  used = 1024.00M  free = 1024.00M"), 1.0)
        self.assertIsNone(machine.parse_swapusage(""))
        mi = machine.parse_meminfo(MEMINFO)
        self.assertAlmostEqual(mi["MemTotal"], 16384000 / 1024 ** 2)
        self.assertAlmostEqual(mi["SwapTotal"] - mi["SwapFree"], 2048000 / 1024 ** 2)

    def test_proc_stat_and_ps(self) -> None:
        self.assertEqual(machine.parse_proc_stat("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1"), (200, 1000))
        self.assertIsNone(machine.parse_proc_stat("intr 1"))
        self.assertEqual(machine.cpu_from_ps("50.0\n150.0\n", 4), 50.0)
        self.assertIsNone(machine.cpu_from_ps("", 4))


class GradeTest(unittest.TestCase):
    def test_cut_points_follow_this_machines_cores_and_ram(self) -> None:
        g = machine.grade({"cores": 8, "load": 8.0, "ram_gb": 32, "free_gb": 8, "swap_gb": 0.5, "cpu": 30})
        self.assertEqual((g["load_lv"], g["cpu_lv"], g["mem_lv"], g["swap_lv"], g["lv"]), ("g", "g", "g", "g", "g"))
        # the same load on a 2-core machine is red: 4 per core
        self.assertEqual(machine.grade({"cores": 2, "load": 8.0})["load_lv"], "r")
        self.assertEqual(machine.grade({"cores": 4, "load": 10.0})["load_lv"], "a")
        # 4 GB free is plenty of a 16 GB machine's 25 %, tight on a 64 GB one (6 %)
        self.assertEqual(machine.grade({"ram_gb": 16, "free_gb": 4})["mem_lv"], "g")
        self.assertEqual(machine.grade({"ram_gb": 64, "free_gb": 4})["mem_lv"], "r")
        self.assertEqual(machine.grade({"ram_gb": 64, "free_gb": 8})["mem_lv"], "a")
        self.assertEqual(machine.grade({"ram_gb": 16, "swap_gb": 6})["swap_lv"], "r")
        self.assertEqual(machine.grade({"cpu": 90})["cpu_lv"], "r")
        self.assertEqual(machine.grade({"cpu": 70, "ram_gb": 16, "free_gb": 8})["lv"], "a")  # worst row wins

    def test_kernel_pressure_wins_over_the_free_share(self) -> None:
        self.assertEqual(machine.grade({"ram_gb": 16, "free_gb": 8, "pressure": "r"})["mem_lv"], "r")

    def test_unknown_is_none_never_zero(self) -> None:
        g = machine.grade({"cores": 4})
        for k in ("load", "cpu", "free_gb", "swap_gb", "ram_gb", "load_lv", "cpu_lv", "mem_lv", "swap_lv", "lv"):
            self.assertIsNone(g[k], k)
        self.assertEqual(machine.read_raw("plan9")["cpu"], None)

    def test_this_machine_reads_without_errors(self) -> None:
        g = machine.grade(machine.read_raw())
        self.assertGreaterEqual(g["cores"], 1)
        for k in ("load", "cpu", "free_gb", "swap_gb"):
            self.assertTrue(g[k] is None or g[k] >= 0, k)
        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            self.assertIsNotNone(g["ram_gb"])
            self.assertIsNotNone(g["load"])

    def test_a_reader_that_breaks_gives_unknown(self) -> None:
        def boom() -> dict:
            raise OSError("no")
        self.assertIsNone(machine.Machine(read=boom).view()["lv"])

    def test_cached_for_a_few_seconds(self) -> None:
        n = {"v": 0}
        clock = Clock(0.0)

        def read() -> dict:
            n["v"] += 1
            return {"cores": 1}
        m = machine.Machine(read=read, clock=clock)
        m.view(), m.view()
        clock.tick(machine.TTL + 1)
        m.view()
        self.assertEqual(n["v"], 2)


class WaitsTest(unittest.TestCase):
    def test_the_engine_says_why_queued_tasks_wait(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}, "web": {"runtime": "claude"}},
                        max_lanes=1)
        s = Store(cfg.db, clock=Clock())
        e = Engine(cfg, s, FakeRuntime(s), lambda n: None, clock=Clock(), log=lambda _: None)
        a = s.add_task("docs", "a", "b")
        b = s.add_task("web", "b", "b")
        c = s.add_task("docs", "c", "b")
        e.tick()
        waits = machine.read_waits(cfg.state_dir)
        self.assertNotIn(str(a), waits)  # started
        self.assertIn("started another task", waits[str(c)])
        e.tick()
        waits = machine.read_waits(cfg.state_dir)
        self.assertIn("slot", waits[str(b)])
        self.assertIn("busy with another task", waits[str(c)])

    def test_api_machine(self) -> None:
        cfg = make_home()
        machine.write_waits(cfg.state_dir, {5: "every slot is busy"})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, Store(cfg.db), lambda _: None))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/machine", timeout=10) as r:
                body = json.loads(r.read())
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertGreaterEqual(body["machine"]["cores"], 1)
        self.assertIn("lv", body["machine"])
        self.assertEqual(body["waits"], {"5": "every slot is busy"})
        self.assertIsNone(body["usage"])  # the Claude percent is off unless tower.json turns it on


class BoardTest(unittest.TestCase):
    def test_board_fetches_the_machine_and_labels_unknown(self) -> None:
        html = (machine.Path(machine.__file__).parent / "board" / "index.html").read_text(encoding="utf-8")
        self.assertIn("api/machine", html)
        self.assertEqual(html.count("    mc: { title:"), 5)  # every language


if __name__ == "__main__":
    unittest.main()

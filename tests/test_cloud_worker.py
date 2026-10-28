"""Cloud worker task flow against an in-memory fake of Azure Blob Storage (no network)."""
import io
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

try:
    from azure.core.exceptions import ResourceExistsError
except ImportError:  # make the worker importable without the Azure SDK
    ResourceExistsError = type("ResourceExistsError", (Exception,), {})
    azure = types.ModuleType("azure");core = types.ModuleType("azure.core");exc = types.ModuleType("azure.core.exceptions")
    exc.ResourceExistsError = ResourceExistsError;core.exceptions = exc;azure.core = core
    sys.modules.update({"azure": azure, "azure.core": core, "azure.core.exceptions": exc})


class Blob:
    def __init__(self, store, c, n):self.s, self.c, self.n = store, c, n
    def exists(self):return (self.c, self.n) in self.s.data
    def upload_blob(self, data, overwrite=False):
        if not overwrite and self.exists():raise ResourceExistsError()
        self.s.data[(self.c, self.n)] = data.read() if hasattr(data, "read") else (data.encode() if isinstance(data, str) else data)
    def download_blob(self):
        data = self.s.data[(self.c, self.n)]
        return types.SimpleNamespace(readinto=lambda h: h.write(data))
    def get_blob_properties(self):return types.SimpleNamespace(last_modified=datetime.now(timezone.utc))


class Container:
    def __init__(self, s, c):self.s, self.c = s, c
    def list_blobs(self, name_starts_with=""):
        return [types.SimpleNamespace(name=n) for (c, n) in sorted(self.s.data) if c == self.c and n.startswith(name_starts_with)]
    def download_blob(self, n):return Blob(self.s, self.c, n).download_blob()
    def upload_blob(self, n, data, overwrite=False):Blob(self.s, self.c, n).upload_blob(data, overwrite)


class FakeService:
    def __init__(self):self.data = {}
    def get_blob_client(self, c, n):return Blob(self, c, n)
    def get_container_client(self, c):return Container(self, c)
    def create_container(self, c):pass


class WorkerTests(unittest.TestCase):
    def test_simulate_analyse_build_and_claims(self):
        import team_player.cloud.worker as w
        from team_player.simulator.season import fixtures
        fake = FakeService()
        fx = dict(fixtures("2019-20")[0])
        with patch.object(w, "service", lambda: fake), patch.dict("os.environ", {"TEAM_PLAYER_WORKERS": "1"}):
            import team_player.simulator.season as season
            real = season.write_fixture
            with patch.object(season, "write_fixture", lambda f, out: real(f, out, duration=60)):
                self.assertEqual(w.simulate_task(fx), 1)
                self.assertEqual(w.simulate_task(fx), 0)                        # output exists: skipped
            names = [n for (c, n) in fake.data if c == "tracking"]
            self.assertEqual(names, [f"history/2019-20/mw01_{fx['home']}-{fx['away']}.jsonl.gz"])
            self.assertTrue(any(c == "ground-truth" for (c, n) in fake.data))   # private record kept apart
            self.assertEqual(w.analyse_task(names[0]), 1)
            self.assertTrue(any(c == "analysis" and n.startswith("matches/2019-20/") for (c, n) in fake.data))
            # Malformed current-season reference data must be excluded even without --seasons.
            fake.data[("analysis", f"matches/{w.CURRENT_SEASON}/demo.json.gz")] = b"must never be opened as history"
            w.build_store_remote()
            self.assertIn(("analysis", "history.db"), fake.data)
            self.assertFalse(w.claim(fake, f"simulate/{fx['fixture_id']}"))     # someone already owns it

    def test_current_season_never_goes_to_history(self):
        import team_player.cloud.worker as w
        with patch.object(sys, "argv", ["worker", "analyse", "--seasons", "current"]), patch.object(w, "ensure_containers", lambda: None):
            with self.assertRaises(SystemExit):
                w.main()


if __name__ == "__main__":
    unittest.main()

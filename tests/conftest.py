"""Suite-wide guard: no test may reach the real scraper, GoHighLevel, state/seen.sqlite or state/runs/.

A test that needs either patches it itself (its monkeypatch overrides this one)."""
import pytest

from leadbot.bot import cli


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        pytest.fail("test reached the real scraper or GoHighLevel")
    monkeypatch.setattr(cli, "_real_deps", refuse)
    monkeypatch.setattr(cli, "client_from_env", refuse)
    monkeypatch.setattr(cli, "SEEN_PATH", str(tmp_path / "guard-seen.sqlite"))
    monkeypatch.setattr(cli, "RUNS_DIR", str(tmp_path / "guard-runs"))

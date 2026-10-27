from pathlib import Path

from conftest import FakeClock

from envcite_jobkit import Heartbeat
from envcite_jobkit.heartbeat import main


def test_beat_creates_and_updates_mtime(clock: FakeClock, tmp_path: Path) -> None:
    hb = Heartbeat(tmp_path / "hb", clock=clock)
    assert hb.age() is None
    hb.beat()
    assert hb.path.stat().st_mtime == clock.now
    clock.advance(5)
    assert hb.age() == 5
    hb.beat()
    assert hb.age() == 0


def test_min_interval_throttles(clock: FakeClock, tmp_path: Path) -> None:
    hb = Heartbeat(tmp_path / "hb", min_interval=10, clock=clock)
    hb.beat()
    first = hb.path.stat().st_mtime
    clock.advance(5)
    hb.beat()
    assert hb.path.stat().st_mtime == first
    clock.advance(5)
    hb.beat()
    assert hb.path.stat().st_mtime == clock.now


def test_check_command(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    assert main([str(path), "60"]) == 0  # 還沒有檔案視為健康
    path.touch()
    assert main([str(path), "60"]) == 0
    assert main([str(path), "-1"]) == 1  # 超過期限
    assert main([]) == 2

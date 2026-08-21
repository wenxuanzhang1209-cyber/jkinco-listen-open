"""孤儿文件清理是磁盘被占满的唯一防线,必须真的清得掉、且不会被一个坏文件卡住。

覆盖率显示 sweep_stale_files 只有 6/10 行被覆盖 —— 未覆盖的正是真正执行删除的
那几行。也就是说测试调用过它,但「孤儿文件会被删掉」这件事从未被验证过。

这条链路的代价是实打实的:单个上传上限 500MB,进程崩溃/任务丢弃/下载中断都会
留下孤儿文件,而磁盘写满会直接拖垮 SQLite 写入。生产主机实际到过 95%。

顺带钉住一个读代码时发现的问题:except OSError 包住的是整个内层循环,
任何一个文件 stat 失败(并发删除、权限)都会让循环整体中断,它后面的文件
再也不会被检查 —— 而且 pass 掉不留痕,排查时看不到任何迹象。
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-sweep-"))

import pytest

import backend.main as main

DAY = 24 * 60 * 60


@pytest.fixture
def sweep_dirs(monkeypatch, tmp_path):
    upload = tmp_path / "uploads"
    export = tmp_path / "exports"
    upload.mkdir()
    export.mkdir()
    monkeypatch.setattr(main, "UPLOAD_DIR", upload)
    monkeypatch.setattr(main.core, "EXPORT_DIR", export)
    return upload, export


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_stale_files_are_deleted_and_fresh_ones_kept(sweep_dirs):
    upload, export = sweep_dirs
    stale_audio = upload / "orphan.webm"
    stale_export = export / "orphan.docx"
    fresh = upload / "in-flight.webm"
    for p in (stale_audio, stale_export, fresh):
        p.write_bytes(b"x" * 32)
    _age(stale_audio, 3 * DAY)
    _age(stale_export, 3 * DAY)

    main.sweep_stale_files()

    assert not stale_audio.exists(), "过期的孤儿音频没被清掉 —— 磁盘会被慢慢吃满"
    assert not stale_export.exists(), "过期的导出文件没被清掉"
    assert fresh.exists(), "把还在用的新文件删了 —— 会毁掉正在处理的任务"


def test_one_unreadable_file_does_not_abort_the_whole_sweep(sweep_dirs, monkeypatch):
    """一个坏文件不能让它后面的文件全部逃过清理。"""
    upload, _ = sweep_dirs
    names = ["a.webm", "b.webm", "c.webm"]
    for name in names:
        p = upload / name
        p.write_bytes(b"x" * 32)
        _age(p, 3 * DAY)

    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self.name == "b.webm":
            raise OSError(5, "模拟读取失败")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    main.sweep_stale_files()
    monkeypatch.undo()

    survivors = sorted(p.name for p in upload.iterdir())
    assert survivors == ["b.webm"], (
        f"一个文件出错就中断了整轮清理，残留 {survivors};"
        "坏文件本身可以留下,但它后面的必须照常清理"
    )


def test_sweep_survives_a_missing_directory(sweep_dirs, monkeypatch):
    """目录不存在时不能抛异常 —— 它跑在后台线程里,抛出去就再也不清理了。"""
    upload, _ = sweep_dirs
    monkeypatch.setattr(main, "UPLOAD_DIR", upload / "does-not-exist")
    main.sweep_stale_files()

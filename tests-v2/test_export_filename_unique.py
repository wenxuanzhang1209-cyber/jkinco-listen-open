"""导出文件名必须并发唯一。

导出目录 /tmp/jkinco_exports 是全体用户共用的。原先文件名只含「秒 + 毫秒」,
而生成文件名本身只花微秒 —— 实测 200 次并发只得到 5 个不同名字、碰撞 195 次。
撞名意味着后写的覆盖先写的,先导出的那位下载到的是别人的会议纪要,
属于跨用户数据泄露。
"""
import concurrent.futures
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jkinco_export import export_filename


def test_filenames_are_unique_under_concurrency():
    count = 500
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        names = list(pool.map(lambda _: export_filename("docx"), range(count)))
    duplicates = count - len(set(names))
    assert duplicates == 0, f"{count} 次并发出现 {duplicates} 次撞名,会导致用户拿到他人纪要"


def test_filenames_are_unique_in_a_tight_loop():
    """串行快速调用同样不能撞 —— 这正是原实现失败的场景。"""
    names = [export_filename("pdf") for _ in range(300)]
    assert len(set(names)) == 300


def test_filename_stays_inside_the_export_directory():
    """文件名不得含路径分隔符,避免写到导出目录之外。"""
    from jkinco_export import EXPORT_DIR

    path = Path(export_filename("docx"))
    assert path.parent.resolve() == EXPORT_DIR.resolve()
    assert "/" not in path.name and "\\" not in path.name


def test_filename_keeps_a_readable_timestamp():
    """随机串是为了唯一,不能把可读的时间信息也弄丢。"""
    name = Path(export_filename("docx")).name
    assert re.search(r"\d{8}_\d{6}", name), f"文件名里没有可读时间:{name}"

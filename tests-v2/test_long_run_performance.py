"""长期运行才会暴露的性能退化。

两条都不是「跑得慢一点」,而是「用得越久越慢/越占内存」——上线当天量不出来,
几个月后才显形,而那时已经找不到是哪次改动引入的。

一、转写关联参会人。拼接转写要按 livekit_identity 找显示名,而参会记录是每次
加入新增一条(重连也算)。没有对应索引时,每一句转写都要把该会议的全部参会
记录扫一遍,代价是「本次句数 × 历史参会记录数」。重复会议长期复用同一行记录,
两边都在累积 —— 实测一年 52 周后由 0.8ms 涨到 28.4ms,加索引后是 4.8ms。

二、归档记录查询。归档只增不减且每条带全文转写,readlines() 会把整个文件读进
内存 —— 实测 2 万条(120MB)时单次查询峰值多占 103MB。改流式扫描后是 0。
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

import backend.meetings as meeting_service
import jkinco_history as history


def _seed_recurring(weeks: int, people: int = 8, lines: int = 200) -> tuple[str, float]:
    meeting_id = uuid.uuid4().hex
    now = time.time()
    with meeting_service.db() as connection:
        connection.execute(
            """INSERT INTO meetings(id, meeting_code, room_name, title, creator_username,
                                    host_username, status, created_at, updated_at, recurrence)
               VALUES(?,?,?,?,?,?,'active',?,?,'weekly')""",
            (meeting_id, uuid.uuid4().hex[:11], f"room-{meeting_id[:8]}", "周会", "u", "u", now, now),
        )
    last_base = now
    for week in range(1, weeks + 1):
        last_base = now + week * 7 * 86400
        with meeting_service.db() as connection:
            connection.executemany(
                "INSERT INTO meeting_participants VALUES (?,?,?,?,?,?,?,NULL,?,'left')",
                [(uuid.uuid4().hex, meeting_id, f"u{i}", f"用户{i}", "member",
                  f"u{i}-{week:03d}", last_base, last_base) for i in range(people)],
            )
            connection.executemany(
                "INSERT INTO meeting_transcript_segments VALUES (?,?,?,?,?,?,?,1,'t',?,?)",
                [(uuid.uuid4().hex, meeting_id, f"u{i % people}-{week:03d}", i, i * 1000,
                  i * 1000 + 900, f"第{week}周第{i}句", uuid.uuid4().hex, last_base + i)
                 for i in range(lines)],
            )
    return meeting_id, last_base


def test_transcript_join_uses_the_identity_index():
    """判据用查询计划而不是耗时:耗时在不同机器上抖动,索引有没有被选中是确定的。"""
    with meeting_service.db() as connection:
        query = (
            "SELECT COALESCE(p.display_name, s.participant_identity) AS speaker_name, s.text"
            + meeting_service._TRANSCRIPT_JOIN
            + "WHERE s.meeting_id=? AND s.is_final=1 AND s.created_at>=?"
              " ORDER BY s.created_at, s.start_time_ms"
        )
        plan = " | ".join(str(row[-1]) for row in connection.execute("EXPLAIN QUERY PLAN " + query, ("x", 0)))
    assert "idx_participants_identity" in plan, (
        f"参会人关联没走 identity 索引,每句转写都会全扫参会记录:{plan}"
    )


def test_reading_one_occurrence_does_not_scale_with_history():
    """只取本周的转写,代价不该随「这个会已经开了多少周」增长。"""
    short_id, short_base = _seed_recurring(weeks=2)
    long_id, long_base = _seed_recurring(weeks=30)

    def elapsed(meeting_id: str, floor: float) -> float:
        """取多次里的最小值,不取均值。

        这两次测量都在亚毫秒级,而机器上任何别的负载都会让某一次变慢 ——
        实测同一段代码 max/min 可达 8.6 倍。均值会被单个离群值拉高,于是这条
        用例在全量跑(CPU 有竞争)时随机失败,单独跑又通过。
        噪声只会让耗时变长,所以最小值才是「这段代码最快能多快」的稳健估计。
        """
        samples: list[float] = []
        for _ in range(12):
            with meeting_service.db() as connection:
                start = time.perf_counter()
                text = meeting_service._final_transcript_text(connection, meeting_id, floor)
                samples.append(time.perf_counter() - start)
            assert text.count("\n") + 1 == 200, "取到的不是单次会议的内容"
        return min(samples)

    ratio = elapsed(long_id, long_base) / max(elapsed(short_id, short_base), 1e-6)
    # 数据量差 15 倍。留足余量,只要不是线性放大就算通过 —— 这条测的是「有没有
    # 退化成全扫」,不是精确的性能数字。索引正常时实测比值约 0.9。
    assert ratio < 6, f"读取代价随历史线性上涨(慢了 {ratio:.1f} 倍),索引可能失效了"


def test_archive_lookup_is_streamed(tmp_path, monkeypatch):
    """归档只增不减,查询不能把整个文件读进内存。"""
    archive = tmp_path / "archive.jsonl"
    monkeypatch.setattr(history, "HISTORY_ARCHIVE_FILE", archive)
    bulky = "讨论了排期与预算。" * 200
    target = uuid.uuid4().hex
    with open(archive, "w", encoding="utf-8") as handle:
        for index in range(600):
            record_id = target if index == 0 else uuid.uuid4().hex
            handle.write(json.dumps({"id": record_id, "title": f"会议{index}",
                                     "transcript": bulky}, ensure_ascii=False) + "\n")

    import tracemalloc

    tracemalloc.start()
    found = history.find_archived_record(target)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert found and found["id"] == target
    file_size = archive.stat().st_size
    assert peak < file_size / 2, (
        f"查询峰值内存 {peak/1024:.0f}KB 接近文件体积 {file_size/1024:.0f}KB —— 仍是整份读入"
    )


def test_archive_lookup_survives_a_truncated_tail(tmp_path, monkeypatch):
    """追加写 + 断电会留下半行,不能因此让整条查询失败。"""
    archive = tmp_path / "archive.jsonl"
    monkeypatch.setattr(history, "HISTORY_ARCHIVE_FILE", archive)
    target = uuid.uuid4().hex
    with open(archive, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": target, "title": "好记录"}, ensure_ascii=False) + "\n")
        handle.write('{"id": "half", "titl')
    assert history.find_archived_record(target)["title"] == "好记录"


def test_participant_list_is_scoped_to_the_current_occurrence():
    """参会名单只能是本次的,口径与转写一致。

    每次加入都新增一条参会记录(重连也算),而重复会议长期复用同一行记录。
    不按本次过滤的话,一年后 8 个人会被返回成 416 条、响应从 3KB 涨到 128KB ——
    而这个接口是会议室里每 2.5-8 秒轮询一次的,名单里还全是重复项。
    """
    from fastapi.testclient import TestClient

    from backend.main import app

    admin, _, password = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": admin, "password": password})
        meeting = client.post("/api/meetings", json={"title": "常驻周会"}).json()
        client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "主持人"})

        now = time.time()
        with meeting_service.db() as connection:
            # 往前塞 30 周的历史参会记录
            connection.executemany(
                "INSERT INTO meeting_participants VALUES (?,?,?,?,?,?,?,?,?,'left')",
                [(uuid.uuid4().hex, meeting["id"], f"u{i}", f"用户{i}", "member",
                  f"u{i}-{week:03d}", now - (30 - week) * 7 * 86400,
                  now - (30 - week) * 7 * 86400 + 3600, now - (30 - week) * 7 * 86400 + 3600)
                 for week in range(30) for i in range(8)],
            )
        listed = client.get(f"/api/meetings/{meeting['id']}").json()["participants"]

    assert len(listed) <= 4, f"名单混进了历史参会记录:{len(listed)} 条"
    assert any(item["display_name"] == "主持人" for item in listed), "把当前在场的人滤掉了"


def test_participant_list_keeps_people_who_left_this_occurrence():
    """本次离开的人仍要留在名单里 —— 那是「谁参加过这场会」。"""
    from fastapi.testclient import TestClient

    from backend.main import app

    admin, _, password = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")
    with TestClient(app) as host:
        host.post("/api/auth/login", json={"username": admin, "password": password})
        meeting = host.post("/api/meetings", json={"title": "离开也要留痕"}).json()
        host.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "主持人"})
        host.post(f"/api/meetings/{meeting['id']}/leave", json={})
        listed = host.get(f"/api/meetings/{meeting['id']}").json()["participants"]

    assert any(item["display_name"] == "主持人" for item in listed), "本次离开的人被滤掉了"

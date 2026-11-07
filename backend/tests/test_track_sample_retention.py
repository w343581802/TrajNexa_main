"""逐帧样本保留策略的单测（B8-SA5 数据量控制）。

要点：`dry_run` 只报告不删；`apply` 只删更早任务的样本，最近 N 个任务原样保留，
其它案件的样本不受影响；`keep_runs < 1` 直接拒绝（防止误删全案样本）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import m6_track_sample_retention as retention
from app.models import Case, TrackSample, TrackingRun

BASE = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)


def seed(db, case_id: str, runs: int, samples_per_run: int, *, start_hour: int = 0):
    db.add(Case(id=case_id, code=f"CODE-{case_id}", name=f"案件 {case_id}"))
    created: list[TrackingRun] = []
    for index in range(runs):
        run = TrackingRun(
            id=f"{case_id}-run-{index}",
            case_id=case_id,
            observation_id=f"{case_id}-obs-{index}",
            status="done",
            created_at=BASE + timedelta(hours=start_hour + index),
        )
        db.add(run)
        created.append(run)
        for frame in range(samples_per_run):
            db.add(TrackSample(
                id=f"{run.id}-s{frame}",
                run_id=run.id,
                case_id=case_id,
                camera_label="1-LT-测试镜头",
                track_id="t1",
                frame_id=frame,
                timestamp_seconds=float(frame),
                bbox_json=[1.0, 2.0, 3.0, 4.0],
                frame_width=1920,
                frame_height=1080,
            ))
    db.commit()
    return created


def sample_total(db, case_id: str) -> int:
    return int(db.scalar(
        select(func.count(TrackSample.id)).where(TrackSample.case_id == case_id)
    ) or 0)


def sample_run_ids(db, case_id: str) -> set[str]:
    return {str(row) for row in db.scalars(
        select(TrackSample.run_id).where(TrackSample.case_id == case_id)
    ).all()}


def test_dry_run_reports_without_deleting(env):
    _client, Session, _tmp = env
    with Session() as db:
        seed(db, "case-a", 3, 2)
        report = retention.prune(db, case_id="case-a", keep_runs=2)
        assert report["applied"] is False
        assert report["keep_runs"] == 2
        assert report["total_dropped_samples"] == 2
        row = report["cases"][0]
        assert row == {"case_id": "case-a", "kept_runs": 2, "dropped_runs": 1, "dropped_samples": 2}
        # 报告完不能动数据
        assert sample_total(db, "case-a") == 6


def test_prune_removes_only_older_run_samples(env):
    _client, Session, _tmp = env
    with Session() as db:
        seed(db, "case-a", 3, 2)
        report = retention.prune(db, case_id="case-a", keep_runs=2, dry_run=False)
        assert report["applied"] is True
        assert report["deleted_samples"] == 2
        assert sample_total(db, "case-a") == 4
        # 只删第 0 个任务的样本，最近两个任务保持完整
        assert sample_run_ids(db, "case-a") == {"case-a-run-1", "case-a-run-2"}
        # 任务本身不能删：证据链在 runs / candidates 上
        assert db.scalar(select(func.count(TrackingRun.id)).where(
            TrackingRun.case_id == "case-a")) == 3


def test_prune_is_per_case(env):
    _client, Session, _tmp = env
    with Session() as db:
        seed(db, "case-a", 3, 2)
        seed(db, "case-b", 2, 2, start_hour=10)
        retention.prune(db, case_id="case-a", keep_runs=1, dry_run=False)
        assert sample_total(db, "case-a") == 2
        # 另一个案件完全不参与
        assert sample_total(db, "case-b") == 4


def test_plan_covers_every_case_when_scope_omitted(env):
    _client, Session, _tmp = env
    with Session() as db:
        seed(db, "case-a", 2, 1)
        seed(db, "case-b", 2, 1, start_hour=10)
        report = retention.plan(db, keep_runs=1)
        assert {row["case_id"] for row in report["cases"]} == {"case-a", "case-b"}
        assert report["total_dropped_samples"] == 2


def test_keep_runs_must_be_positive(env):
    _client, Session, _tmp = env
    with Session() as db:
        seed(db, "case-a", 1, 1)
        with pytest.raises(ValueError, match="keep_runs_must_be_positive"):
            retention.plan(db, keep_runs=0)
        with pytest.raises(ValueError, match="keep_runs_must_be_positive"):
            retention.prune(db, keep_runs=-1)


def test_nothing_to_drop_reports_zero(env):
    _client, Session, _tmp = env
    with Session() as db:
        seed(db, "case-a", 1, 2)
        report = retention.prune(db, case_id="case-a", keep_runs=5, dry_run=False)
        assert report["total_dropped_samples"] == 0
        assert report["applied"] is False
        assert sample_total(db, "case-a") == 2
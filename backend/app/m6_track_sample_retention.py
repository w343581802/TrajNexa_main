"""逐帧样本的保留策略（B8-SA5 数据量控制）。

`track_samples` 只服务一件事：读取轨迹时按当前 Map 参数现算地面坐标。
所以样本是「可重算的中间量」，不是证据本身——证据在候选、关键帧与窗口记录里。
据此定保留规则：

- 每个案件保留最近 `keep_runs` 个追踪任务的逐帧样本（默认 20），更早任务的样本删除；
- 只删 `track_samples`，不动 `tracking_runs` / `tracking_candidates` / `tracking_windows`；
- 默认 `dry_run=True`，必须先看到将删多少行再落库，避免误删。

刻意不做的事：不按天数过期（案件可能长期停摆，按天数会把「唯一一次真跑」删掉）、
不在 worker 热路径里自动删（跑批中途删样本会让刚算出的轨迹读不到）。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import TrackSample, TrackingRun

DEFAULT_KEEP_RUNS = 20


def _keep_run_ids(db: Session, case_id: str, keep_runs: int) -> list[str]:
    """按创建时间倒序取该案件最近 N 个任务 id。"""
    rows = db.scalars(
        select(TrackingRun.id)
        .where(TrackingRun.case_id == case_id)
        .order_by(TrackingRun.created_at.desc())
        .limit(keep_runs)
    ).all()
    return [str(row) for row in rows]


def _drop_run_ids(db: Session, case_id: str, keep_runs: int) -> list[str]:
    """该案件里不在最近 N 个之内的任务 id。"""
    keep = set(_keep_run_ids(db, case_id, keep_runs))
    rows = db.scalars(select(TrackingRun.id).where(TrackingRun.case_id == case_id)).all()
    return [str(row) for row in rows if str(row) not in keep]


def plan(db: Session, *, case_id: str | None = None, keep_runs: int = DEFAULT_KEEP_RUNS) -> dict:
    """算出每个案件会删多少行（不写库）。"""
    if keep_runs < 1:
        raise ValueError('keep_runs_must_be_positive')
    cases = [case_id] if case_id else [
        str(row) for row in db.scalars(select(TrackingRun.case_id).distinct()).all()
    ]
    rows: list[dict] = []
    total = 0
    for current in cases:
        dropped = _drop_run_ids(db, current, keep_runs)
        samples = 0
        if dropped:
            samples = int(db.scalar(
                select(func.count(TrackSample.id)).where(TrackSample.run_id.in_(dropped))
            ) or 0)
        rows.append({
            'case_id': current,
            'kept_runs': len(_keep_run_ids(db, current, keep_runs)),
            'dropped_runs': len(dropped),
            'dropped_samples': samples,
        })
        total += samples
    return {
        'keep_runs': keep_runs,
        'cases': rows,
        'total_dropped_samples': total,
        'note': '只删逐帧样本；追踪任务、候选与窗口记录保留',
    }


def prune(db: Session, *, case_id: str | None = None, keep_runs: int = DEFAULT_KEEP_RUNS,
          dry_run: bool = True) -> dict:
    """按保留策略清理逐帧样本；`dry_run` 为真时只报告不删除。"""
    report = plan(db, case_id=case_id, keep_runs=keep_runs)
    if dry_run or report['total_dropped_samples'] == 0:
        report['applied'] = False
        return report
    deleted = 0
    for row in report['cases']:
        if row['dropped_samples'] == 0:
            continue
        dropped = _drop_run_ids(db, row['case_id'], keep_runs)
        if not dropped:
            continue
        result = db.execute(delete(TrackSample).where(TrackSample.run_id.in_(dropped)))
        deleted += int(result.rowcount or 0)
    db.commit()
    report['applied'] = True
    report['deleted_samples'] = deleted
    return report
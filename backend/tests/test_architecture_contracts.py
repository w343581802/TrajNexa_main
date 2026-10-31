"""架构契约守卫：把 design/26 里最容易复发的几条契约变成可执行检查。

这些检查都是源码静态检查，不连数据库、不起服务；目的不是覆盖功能，
而是在有人「另起一套」时立刻失败，把讨论拉回 design/26。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"


def _repo_root() -> Path | None:
    """仓库根：容器里后端挂在 /srv，宿主机在 TrajNexa_main/，向上找 design 目录。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "design").is_dir() and (parent / "backend").is_dir():
            return parent
    return None


REPO = _repo_root()


def _sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(APP.glob("*.py"))}


def _definitions(name: str) -> list[str]:
    pattern = re.compile(rf"^def {re.escape(name)}\b", re.MULTILINE)
    return [filename for filename, text in _sources().items() if pattern.search(text)]


@pytest.mark.parametrize("name, owner", [
    ("grab_evidence_frame", "overlay_identity.py"),
    ("frame_is_blank", "overlay_ocr.py"),
    ("frame_is_broken", "overlay_ocr.py"),
    ("match_camera", "recordings.py"),
    ("identify_video", "overlay_identity.py"),
])
def test_single_implementation(name: str, owner: str) -> None:
    """C1 / C3 / C8：取帧、坏帧判定、名称匹配各自只有一个实现。"""
    found = _definitions(name)
    assert found == [owner], f"{name} 应只定义在 {owner}，现在出现在 {found}；见 design/26 契约 C1/C3/C8"


def test_read_frame_only_used_by_frame_pipeline() -> None:
    """C1：读帧只能经 overlay_ocr 或 overlay_identity，其他模块不得直连。"""
    allowed = {"overlay_ocr.py", "overlay_identity.py"}
    offenders = [
        filename
        for filename, text in _sources().items()
        if filename not in allowed and re.search(r"\bread_frame\(", text)
    ]
    assert offenders == [], f"{offenders} 直接调用了 read_frame；见 design/26 契约 C1，走 grab_evidence_frame"


def test_recordings_module_never_mutates_video_files() -> None:
    """C7：录像资源只读，扫录像的模块里不允许出现删除/移动/改名。"""
    text = (APP / "recordings.py").read_text(encoding="utf-8")
    forbidden = [".unlink(", "os.remove", "shutil.move", ".rename("]
    hits = [token for token in forbidden if token in text]
    assert hits == [], f"recordings.py 出现 {hits}；见 design/26 契约 C7，录像资源是只读输入"


def test_only_one_clip_review_modal() -> None:
    """C9：核对识别名称的弹窗只能有一个。"""
    if REPO is None:
        pytest.skip("不在仓库目录里运行")
    modals = REPO / "frontend" / "src" / "components" / "modals"
    if not modals.is_dir():
        pytest.skip("前端目录不存在")
    found = sorted(path.name for path in modals.glob("RecordingClip*Modal.vue"))
    assert found == ["RecordingClipNameModal.vue"], f"发现 {found}；见 design/26 契约 C9，只保留 RecordingClipNameModal.vue"


def test_contract_document_stays_in_repo() -> None:
    """契约文档被删掉就失去约束力，这里守着它存在且编号完整。"""
    if REPO is None:
        pytest.skip("不在仓库目录里运行")
    doc = REPO / "design" / "26_能力地图与复用契约.md"
    assert doc.is_file(), "design/26 能力地图与复用契约.md 不见了"
    text = doc.read_text(encoding="utf-8")
    missing = [f"C{index}" for index in range(1, 13) if f"### C{index}" not in text]
    assert missing == [], f"design/26 缺少契约 {missing}"

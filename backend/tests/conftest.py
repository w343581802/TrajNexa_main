from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.db import Base, get_db
from app.main import app
from app.config import settings

@pytest.fixture(autouse=True)
def _hermetic_input_roots(monkeypatch):
    """测试不依赖部署挂载点：允许根留空 + 默认根不指向真实目录。

    `media_inputs.allowed_roots()` 在没显式配置时会退回到「本机真实存在的挂载点」
    （容器里有 /data/video-library），测试里必须清掉，否则用例会受运行环境影响。
    """
    monkeypatch.setattr(settings, 'video_naming_roots', '')
    monkeypatch.setattr(settings, 'media_input_roots', '')
    monkeypatch.setattr(settings, 'media_input_root', None)
    # 指向一个不存在的目录，让 allowed_roots() 的兜底为空（否则容器里的
    # /data/video-library 会被当成允许根，临时目录的用例全被拒）
    monkeypatch.setattr(settings, 'video_naming_root', Path('/nonexistent-trajnexa-library'))


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    db_url = 'sqlite:///' + str(tmp_path / 'test.db')
    engine = create_engine(db_url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'storage')
    def override_db():
        with Session() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, Session, tmp_path
    app.dependency_overrides.clear()
    engine.dispose()

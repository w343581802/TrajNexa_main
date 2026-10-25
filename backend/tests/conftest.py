from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.db import Base, get_db
from app.main import app
from app.config import settings

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

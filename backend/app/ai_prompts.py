"""Persisted per-object-class vision prompts.

Resolution order: per-request override > stored setting > built-in default.
Prompts are suggestions for the VLM only; they never relax the 'unverified'
contract of model output.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import m4_vision
from .models import AiPromptSetting

CLASSES = ('person', 'vehicle', 'other')
MAX_LENGTH = 2400


def builtin(object_class: str) -> str:
    return m4_vision.effective_feature_prompt(object_class)


def stored(db: Session, object_class: str) -> str | None:
    if object_class not in CLASSES:
        return None
    row = db.scalar(select(AiPromptSetting).where(AiPromptSetting.key == object_class))
    return row.prompt if row is not None else None


def effective(db: Session, object_class: str, override: str | None = None) -> str:
    if isinstance(override, str) and override.strip():
        return override.strip()
    return stored(db, object_class) or builtin(object_class)


def save(db: Session, object_class: str, prompt: str) -> None:
    row = db.get(AiPromptSetting, object_class)
    if row is None:
        db.add(AiPromptSetting(key=object_class, prompt=prompt))
    else:
        row.prompt = prompt
    db.commit()


def reset(db: Session, object_class: str) -> None:
    row = db.get(AiPromptSetting, object_class)
    if row is not None:
        db.delete(row)
        db.commit()

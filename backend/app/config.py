from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./trajnexa_dev.sqlite3'
    storage_root: Path = Path('./storage')
    max_upload_bytes: int = 268435456
    worker_poll_seconds: float = 2
    ocr_enabled: bool = False  # requires optional PaddleOCR CPU runtime
    m4a_enabled: bool = False  # explicit opt-in until access-control deployment
    name_roi_enabled: bool = True  # two extra bottom-right OCR passes when enabled
    map_db_readonly_url: str = ''  # optional SELECT-only connection to Map PostgreSQL
    # 录像资源：主系统只「读」录像目录，扫描结果单独存表，不改动任何录像文件。
    recordings_root: Path = Path('/data/videos')
    recordings_scan_interval_seconds: int = 30  # 开启自动扫描后的增量扫描间隔
    recordings_scan_max_files: int = 2000  # 单次扫描的文件上限，防止误选整个磁盘
    # 视频命名工具：前端只传本地路径 / 目录，后端直接读文件并原地改名。
    # VIDEO_NAMING_ROOTS 用 os.pathsep 分隔；留空表示本地 demo 只校验路径存在。
    video_naming_roots: str = ''
    video_naming_max_files: int = 200
    # 可写挂载点：容器内看到的录像库；相对路径按它解析
    video_naming_root: Path = Path('/data/video-library')
    # 宿主机路径 → 容器路径，多个用 `;` 分隔；默认把 G:\测试数据\视频 映射到可写挂载
    video_naming_path_map: str = r'G:\测试数据\视频=/data/video-library'
    # 通用本地媒体输入（录像资源 / 视频命名 / 添加要素共用同一套「前端传路径、后端读本地文件」口径）。
    # 留空时自动沿用上面的 VIDEO_NAMING_*，已有部署不需要改配置。
    media_input_root: Path | None = None
    media_input_roots: str = ''
    media_input_path_map: str = ''

settings = Settings()

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./trajnexa_dev.sqlite3'
    storage_root: Path = Path('./storage')
    max_upload_bytes: int = 268435456
    worker_poll_seconds: float = 2
    ocr_enabled: bool = False  # requires optional PaddleOCR CPU runtime
    # Demo 阶段默认开启（design/26 约束 0）：不设授权闸门，先让能力在界面上看得见。
    # 要关闭仍可用 M4A_ENABLED=false / M4B_ENABLED=false 覆盖。
    m4a_enabled: bool = True
    m4c_enabled: bool = False  # opt-in read-only sampled frames of registered clips
    m4b_enabled: bool = True
    m4b_model_path: Path = Path('/models/dfine.onnx')
    m4b_model_sha256: str = ''  # 配了权重就必须配 SHA-256，防止权重被静默替换
    # M4d 本地视觉模型（Qwen3-VL via Ollama）：与 D-FINE 并列的第二个识别引擎。
    # 容器内要用 host.docker.internal 才能回到宿主机上的 ollama serve。
    vision_enabled: bool = True
    vision_base_url: str = 'http://host.docker.internal:11434'
    vision_model: str = 'qwen3-vl:2b-instruct'
    vision_timeout_seconds: int = 180
    name_roi_enabled: bool = True  # two extra bottom-right OCR passes when enabled
    map_db_readonly_url: str = ''  # optional SELECT-only connection to Map PostgreSQL
    # 录像资源：主系统只「读」录像目录，扫描结果单独存表，不改动任何录像文件。
    recordings_root: Path = Path('/data/videos')
    recordings_scan_interval_seconds: int = 30  # 开启自动扫描后的增量扫描间隔
    recordings_scan_max_files: int = 2000  # 单次扫描的文件上限，防止误选整个磁盘
    # 视频命名工具：前端只传本地路径 / 目录，后端直接读文件并原地改名。
    # VIDEO_NAMING_ROOTS 用分号分隔；**留空也会兜底**：按本机真实存在的可写挂载点
    # （容器内即 /data/video-library）限制可改名的范围——只有连这些挂载点都不存在
    # （本地直接跑、没有挂载）时才退回不限制。要彻底放开就显式写 `VIDEO_NAMING_ROOTS=/`。
    video_naming_roots: str = ''
    video_naming_max_files: int = 200
    # 可写挂载点：容器内看到的录像库；相对路径按它解析
    video_naming_root: Path = Path('/data/video-library')
    # 宿主机路径 → 容器路径，多个用 `;` 分隔；默认把 G:\测试数据\视频 映射到可写挂载
    video_naming_path_map: str = r'G:\测试数据\视频=/data/video-library'
    # 通用本地媒体输入（录像资源 / 视频命名 / 添加要素共用同一套「前端传路径、后端读本地文件」口径）。
    # 留空时自动沿用上面的 VIDEO_NAMING_*（含同一个兜底限制），已有部署不需要改配置。
    media_input_root: Path | None = None
    media_input_roots: str = ''
    media_input_path_map: str = ''

settings = Settings()

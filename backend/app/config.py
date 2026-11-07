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
    # 要关闭仍可用 M4A_ENABLED=false / M4C_ENABLED=true 覆盖。
    m4a_enabled: bool = True
    m4c_enabled: bool = False  # opt-in read-only sampled frames of registered clips
    # M4d 本地视觉模型（Qwen3-VL via Ollama）：与 D-FINE 并列的第二个识别引擎。
    # 容器内要用 host.docker.internal 才能回到宿主机上的 ollama serve。
    vision_enabled: bool = True
    vision_base_url: str = 'http://host.docker.internal:11434'
    vision_model: str = 'qwen3-vl:2b-instruct'
    # 4B 作为可选引擎（engine=qwen3vl4b），同一个 Ollama 上并存；
    # 6GB 显存放不下两个模型，切换时 Ollama 会自动换出，第一次切会慢几秒。
    vision_model_large: str = 'qwen3-vl:4b-instruct'
    # 关键特征（外观短语）优先用这个模型，不可用时回落到 vision_model。
    vision_feature_model: str = 'qwen3-vl:4b-instruct'
    vision_timeout_seconds: int = 180
    # 检测器目录：backend/config/detectors.json（引擎层自 Rebucca app/analysis/engines 移植）
    # Business workbench uses ONE server-chosen detector, not lab selection.
    # Empty = 配置里第一个已加载检测器。
    m5_workspace_detector_id: str = ''
    # M6 P1-A person-only similarity lab. Must point inside backend/models/.
    # No packaged weight; not a face/identity recognition service.
    m6_candidate_review: bool = True  # B8-C1: Qwen visual review of Top-K candidates (observe-only)
    m6_candidate_review_top_k: int = 3
    m6_person_reid_model_path: str = ''
    m6_person_reid_model_sha256: str = ''
    # Extra local Ollama vision models shown in the large-model menu.
    # JSON list: [{"id":"internvl","name":"InternVL","model":"local-ollama-tag"}]
    # No arbitrary endpoint or credentials accepted from the browser.
    m5_vision_models_json: str = ''
    name_roi_enabled: bool = True  # two extra bottom-right OCR passes when enabled
    map_db_readonly_url: str = ''  # optional SELECT-only connection to Map PostgreSQL
    # B8-S 自动标定：主系统只转发 Map 的作业/候选/审核接口，不复制候选逻辑。
    # 留空 = 未配置，相关路由按「功能未启用」返回 404。
    map_api_base_url: str = ''  # e.g. http://trajnexa-map-web:8000
    # Map 的 Django 只接受 ALLOWED_HOSTS 里的 Host 头（默认 localhost）。
    # 容器间用服务名访问时显式发这个 Host，避免被 400 Bad Request 拒掉。
    map_api_host_header: str = 'localhost'
    # Map 标定写接口的内部令牌（对应 Map 侧 CALIBRATION_ADMIN_TOKEN）。
    # 留空 = 不发头，Map 会以 403 拒绝写操作；只读接口不受影响。
    map_api_admin_token: str = ''
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
    # B8-S V1.2 §9.6 治理开关。实验图层在演示阶段默认开启（现有试点演示依赖它），
    # 生产部署必须显式置 false；自动标定任务与自动地理发布永远默认关。
    b8_experimental_geo_display: bool = True
    b8_auto_calibration_enabled: bool = False
    b8_auto_geo_publish_enabled: bool = False

settings = Settings()

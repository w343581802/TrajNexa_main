"""B8-S：主系统 → Map 自动标定接口的适配层。

职责只做「转发 + 错误归一」，不在主系统里复刻 Map 的候选逻辑，也不缓存候选：
Map 侧才是候选与审核留痕的真相源。默认关闭（``B8_AUTO_CALIBRATION_ENABLED``），
关闭时上层路由直接 404，不返回半套数据。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.config import settings

TIMEOUT_SECONDS = 15.0


class AutoCalibrationUnavailable(RuntimeError):
    """Map 未配置、不可达，或 Map 明确拒绝了请求。"""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def enabled() -> bool:
    return bool(settings.b8_auto_calibration_enabled and settings.map_api_base_url.strip())


def _base() -> str:
    base = settings.map_api_base_url.strip().rstrip("/")
    if not base:
        raise AutoCalibrationUnavailable("map_api_not_configured",
                                         "未配置 Map 接口地址（MAP_API_BASE_URL）")
    return base


def _http_json(method: str, url: str, payload: dict | None = None) -> dict:
    body = None
    # Map 的 Django 只允许 ``ALLOWED_HOSTS`` 里的 Host 头（默认 localhost）。容器之间
    # 用服务名访问时，请求的 Host 会变成服务名而被 400 拒绝；这里显式发 Host，
    # 让「地址」和「Host 头」分开配置，不改 Map 侧的白名单。
    headers = {"Accept": "application/json", "Host": settings.map_api_host_header}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        code = _error_code(raw) or f"map_api_http_{exc.code}"
        raise AutoCalibrationUnavailable(code, _error_message(raw) or code,
                                         status=exc.code) from exc
    except TimeoutError as exc:
        # Map 的 Django 冷启动可能超过超时；这是「暂时不可用」，不是 500。
        raise AutoCalibrationUnavailable(
            "map_api_timeout",
            f"Map 接口超时（{TIMEOUT_SECONDS:.0f}s），稍后重试") from exc
    except urllib.error.URLError as exc:
        raise AutoCalibrationUnavailable("map_api_unreachable",
                                         f"Map 接口不可达：{exc.reason}") from exc
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise AutoCalibrationUnavailable("map_api_bad_json",
                                         "Map 接口返回了非 JSON 内容") from exc


def _error_code(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(payload, dict):
        value = payload.get("error")
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and isinstance(value.get("code"), str):
            return value["code"]
    return ""


def _error_message(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(payload, dict):
        value = payload.get("error")
        if isinstance(value, dict) and isinstance(value.get("message"), str):
            return value["message"]
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return ""


class _Transport:
    """测试注入点：默认走 stdlib urllib，测试里换成本地字典。"""

    def __init__(self, call=_http_json):
        self.call = call

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            return self.call(method, f"{_base()}{path}", payload) if payload is not None \
                else self.call(method, f"{_base()}{path}")
        except TimeoutError as exc:
            # 注入的传输层也可能直接抛超时；统一成「暂时不可用」。
            raise AutoCalibrationUnavailable(
                "map_api_timeout",
                f"Map 接口超时（{TIMEOUT_SECONDS:.0f}s），稍后重试") from exc

    def get(self, path: str) -> dict:
        return self._call("GET", path)

    def post(self, path: str, payload: dict) -> dict:
        return self._call("POST", path, payload)


def transport(call=None) -> _Transport:
    return _Transport(call or _http_json)


def options(camera_uuid: str, *, client: _Transport | None = None) -> dict:
    client = client or transport()
    return client.get(f"/api/v1/spatial/cameras/{camera_uuid}/calibration-options/")


def request_job(camera_uuid: str, *, actor: str = "", method: str = "pose_parameters",
                client: _Transport | None = None) -> dict:
    client = client or transport()
    return client.post("/api/v1/spatial/auto-calibration-jobs/",
                       {"camera_uuid": camera_uuid, "actor": actor, "method": method})


def job(job_id: str, *, client: _Transport | None = None) -> dict:
    client = client or transport()
    return client.get(f"/api/v1/spatial/auto-calibration-jobs/{job_id}/")


def candidates(camera_uuid: str, *, review_status: str = "",
               client: _Transport | None = None) -> dict:
    client = client or transport()
    query = f"?review_status={review_status}" if review_status else ""
    return client.get(
        f"/api/v1/spatial/cameras/{camera_uuid}/auto-calibration-candidates/{query}")


def review(candidate_id: str, *, action: str, actor: str, note: str = "",
           holdout: dict | None = None, max_p90_m: float | None = None,
           client: _Transport | None = None) -> dict:
    client = client or transport()
    payload: dict = {"action": action, "actor": actor, "note": note}
    if holdout is not None:
        payload["holdout_residual_m"] = holdout
    if max_p90_m is not None:
        payload["max_holdout_p90_m"] = max_p90_m
    return client.post(
        f"/api/v1/spatial/auto-calibration-candidates/{candidate_id}/review/", payload)
"""B8-S 自动标定适配层：门禁、错误归一与转发。"""
from app import m6_auto_calibration_adapter as adapter
from app.config import settings


def _fake_transport(monkeypatch, responses):
    calls = []

    def call(method, url, payload=None):
        calls.append((method, url, payload))
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    return call, calls


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', False)
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000')
    assert adapter.enabled() is False


def test_disabled_without_base_url(monkeypatch):
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', True)
    monkeypatch.setattr(settings, 'map_api_base_url', '')
    assert adapter.enabled() is False


def test_options_forwards_camera_uuid(monkeypatch):
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000')
    call, calls = _fake_transport(monkeypatch, [{'ready': True, 'blockers': []}])
    client = adapter.transport(call)
    body = adapter.options('camera-1', client=client)
    assert body['ready'] is True
    assert calls == [('GET',
                      'http://map.test:8000/api/v1/spatial/cameras/camera-1/calibration-options/',
                      None)]


def test_review_sends_holdout_and_limit(monkeypatch):
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000/')
    call, calls = _fake_transport(monkeypatch, [{'candidate': {'review_status': 'approved'}}])
    adapter.review('cand-1', action='approve', actor='reviewer', note='ok',
                   holdout={'residual_m': {'p90': 1.2}}, max_p90_m=2.0,
                   client=adapter.transport(call))
    method, url, payload = calls[0]
    assert method == 'POST'
    assert url == ('http://map.test:8000/api/v1/spatial/auto-calibration-candidates/'
                   'cand-1/review/')
    assert payload == {'action': 'approve', 'actor': 'reviewer', 'note': 'ok',
                       'holdout_residual_m': {'residual_m': {'p90': 1.2}},
                       'max_holdout_p90_m': 2.0}


def test_http_error_is_normalised(monkeypatch):
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000')

    def boom(method, url, payload=None):
        raise adapter.AutoCalibrationUnavailable('holdout_residual_missing',
                                                 'missing', status=409)

    try:
        adapter.review('cand-1', action='approve', actor='r',
                       client=adapter.transport(boom))
    except adapter.AutoCalibrationUnavailable as exc:
        assert exc.code == 'holdout_residual_missing'
        assert exc.status == 409
    else:  # pragma: no cover
        raise AssertionError('expected AutoCalibrationUnavailable')


def test_api_is_hidden_when_disabled(env, monkeypatch):
    client, _Session, _tmp = env
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', False)
    response = client.get('/api/v1/spatial/auto-calibration/options/camera-1')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'auto_calibration_disabled'


def test_api_requires_map_base_url(env, monkeypatch):
    client, _Session, _tmp = env
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', True)
    monkeypatch.setattr(settings, 'map_api_base_url', '')
    response = client.get('/api/v1/spatial/auto-calibration/options/camera-1')
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'auto_calibration_unavailable'


def test_api_returns_409_when_candidate_has_no_holdout(env, monkeypatch):
    client, _Session, _tmp = env
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', True)
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000')

    def boom(method, url, payload=None):
        raise adapter.AutoCalibrationUnavailable('holdout_residual_missing',
                                                 'missing', status=409)

    monkeypatch.setattr(adapter, '_http_json', boom)
    response = client.post(
        '/api/v1/spatial/auto-calibration/candidates/cand-1/review',
        json={'action': 'approve', 'actor': 'reviewer'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'holdout_residual_missing'


def test_api_unreachable_map_is_503(env, monkeypatch):
    client, _Session, _tmp = env
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', True)
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000')

    def boom(method, url, payload=None):
        raise adapter.AutoCalibrationUnavailable('map_api_unreachable', 'down')

    monkeypatch.setattr(adapter, '_http_json', boom)
    response = client.get('/api/v1/spatial/auto-calibration/options/camera-1')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'map_api_unreachable'


def test_timeout_is_normalised_to_503(env, monkeypatch):
    client, _Session, _tmp = env
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', True)
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000')

    def slow(*_args, **_kwargs):
        raise TimeoutError('timed out')

    monkeypatch.setattr(adapter, '_http_json', slow)
    response = client.get('/api/v1/spatial/auto-calibration/options/camera-1')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'map_api_timeout'


def test_api_forwards_job_creation(env, monkeypatch):
    client, _Session, _tmp = env
    monkeypatch.setattr(settings, 'b8_auto_calibration_enabled', True)
    monkeypatch.setattr(settings, 'map_api_base_url', 'http://map.test:8000')
    seen = {}

    def fake(method, url, payload=None):
        seen['method'] = method
        seen['url'] = url
        seen['payload'] = payload
        return {'job': {'job_id': 'job-1', 'status': 'done', 'candidate_id': 'cand-1'}}

    monkeypatch.setattr(adapter, '_http_json', fake)
    response = client.post('/api/v1/spatial/auto-calibration/jobs',
                           json={'camera_uuid': 'camera-1', 'actor': 'tester'})
    assert response.status_code == 200
    assert response.json()['job']['candidate_id'] == 'cand-1'
    assert seen['payload'] == {'camera_uuid': 'camera-1', 'actor': 'tester',
                               'method': 'pose_parameters'}
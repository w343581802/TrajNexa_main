"""No real CCTV media: regression tests with synthetic OCR lines and filenames."""
import numpy as np

from app.overlay_ocr import _parse_name, frame_is_blank
from app.filename_hints import add_filename_hints


def test_unlabelled_bottom_device_code_is_unverified_hint():
    sample = '2-AB-示例路8号-固定'
    assert _parse_name([{'text': sample, 'region': 'bottom'}]) == (sample, 'structured_osd_candidate')
    assert _parse_name([{'text': sample, 'region': 'top'}])[0] is None
    assert _parse_name([{'text': '商店招牌', 'region': 'bottom'}])[0] is None


def test_export_filename_is_not_ocr():
    report = {'time': {'time_status': 'not_checked'}, 'camera': {'match_status': 'not_checked'}}
    add_filename_hints(report, '2-AB-示例路8号-向北_20261023145956至20261023153056_0522.jpg')
    assert report['time']['time_status'] == 'not_checked'
    assert report['time']['filename_time_window_hint']['start_local_unverified'] == '2026-10-23T14:59:56'
    assert report['camera']['filename_name_hint']['source'] == 'filename_unverified'


def test_bottom_plate_without_trailing_segment_is_name_candidate():
    """实测角标不一定有结尾段（YD-中仁路都市丽景正门1）：仍然要能认成名称候选。"""
    for sample in ('YD-中仁路都市丽景正门1', 'YD-桂康路一巷中仁路居民区19栋东',
                   'YD-桂康路与中仁路1', '3-DX-中仁路98号-球'):
        assert _parse_name([{'text': sample, 'region': 'bottom'}]) == (sample, 'structured_osd_candidate')


def test_garbled_scene_text_is_still_not_a_name():
    """整段糊掉的识别结果（实测 3-DX-电仁路孔明园E40东庭街北工）不能当名称。"""
    assert _parse_name([{'text': '3-DX-电仁路孔明园E40东庭街北工', 'region': 'bottom'}])[0] is None
    assert _parse_name([{'text': '广西', 'region': 'bottom'}])[0] is None
    assert _parse_name([{'text': '枪外治人脸', 'region': 'bottom'}])[0] is None


def test_ffmpeg_grey_placeholder_frame_is_blank():
    """ffmpeg 拿不到参考帧时输出 #808080 占位图（std≈1），必须判成坏帧而不是「已取到帧」。"""
    assert frame_is_blank(np.full((1800, 3200, 3), 128, dtype=np.uint8))
    assert frame_is_blank(None)
    assert frame_is_blank(np.zeros((0, 0, 3), dtype=np.uint8))
    assert not frame_is_blank(np.random.default_rng(0).integers(0, 255, (180, 320, 3), dtype=np.uint8))


def test_flat_placeholder_frame_with_std_above_threshold_is_blank():
    """实测 3040x2160 的灰占位图 std 恰好卡在 6.0：只看标准差会漏判。

    这类占位图的真正特征是「几乎所有像素压在同一个灰阶上」：实测 96% 的像素落在
    众数 ±10 内，真实监控画面最高只有 40%。所以判据要用集中度，标准差只作兜底。
    """
    import cv2

    rng = np.random.default_rng(7)
    gray = np.full((180, 320), 128, dtype=np.uint8)
    # 4% 的像素带真实纹理，把标准差抬到阈值之上，但整体仍然是一张灰板
    mask = rng.random((180, 320)) < 0.04
    gray[mask] = rng.integers(0, 256, mask.sum(), dtype=np.uint8)
    frame = np.dstack([gray, gray, gray])

    assert float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).std()) > 6.0
    assert frame_is_blank(frame)


def test_textured_frame_is_not_blank():
    """真实监控画面（有明显场景纹理）不能被误判成占位图。"""
    import cv2

    rng = np.random.default_rng(11)
    gray = rng.integers(0, 256, (180, 320), dtype=np.uint8)
    frame = np.dstack([gray, gray, gray])
    assert float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).std()) > 6.0
    assert not frame_is_blank(frame)

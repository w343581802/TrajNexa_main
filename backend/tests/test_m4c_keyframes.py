"""M4c keyframe sampling: source isolation, time provenance, no false tracklet."""
import numpy as np
import pytest

from app import m4_video
from app.config import settings
from app.models import RecordingClip


def clip(duration=120.0):
    return RecordingClip(id='clip-1', source_id='src-1', relpath='day/a.mp4',
        file_name='a.mp4', size_bytes=4, mtime_ns=900,
        duration_seconds=duration, start_local='2026-10-23T15:00:00',
        start_source='filename', camera_uuid=None, match_status='unscanned')


def test_offset_bounds_and_duplicate_rejection():
    assert m4_video.checked_offsets([0.5, 2, 8], clip()) == [.5, 2, 8]
    for offsets in ([], [1.0]*7, [2, 2], [-1], [130], [float('nan')]):
        with pytest.raises(m4_video.KeyframeError):
            m4_video.checked_offsets(offsets, clip())
    assert m4_video.checked_offset(60, clip(None)) == 60
    with pytest.raises(m4_video.KeyframeError):
        m4_video.checked_offset(61, clip(None))


def test_shared_grab_not_own_extractor(monkeypatch, tmp_path):
    calls=[]
    def fake_shared(path, *, offsets):
        calls.append((path, offsets))
        return np.full((160, 240, 3), 125, np.uint8)
    monkeypatch.setattr(m4_video.overlay_identity, 'grab_evidence_frame', fake_shared)
    path=tmp_path/'a.mp4'; path.write_bytes(b'vid1')
    stat=path.stat()
    row=clip();row.size_bytes=stat.st_size;row.mtime_ns=stat.st_mtime_ns
    frame=m4_video.fetch_frame(path,row,2.0)
    assert frame.shape==(160,240,3)
    assert calls==[(path,(2.0,))]


def test_unreadable_frame_is_not_a_negative_detection(monkeypatch, tmp_path):
    path=tmp_path/'a.mp4';path.write_bytes(b'vid1')
    st=path.stat()
    row=clip();row.size_bytes=st.st_size;row.mtime_ns=st.st_mtime_ns
    monkeypatch.setattr(m4_video.overlay_identity,'grab_evidence_frame',
                        lambda path,offsets:None)
    output=m4_video.preview_keyframes(clip=row,path=path,offsets=[.5])
    assert output['frames'][0]['status']=='frame_unavailable'
    assert output['frames'][0]['detections'] is None
    assert output['tracklet_status']=='not_established'
    assert output['camera_verified_for_observation'] is False
    assert output['absolute_frame_time_verified'] is False


def test_detector_candidates_kept_separate_by_offset(monkeypatch, tmp_path):
    path=tmp_path/'a.mp4';path.write_bytes(b'vid1')
    st=path.stat()
    row=clip();row.size_bytes=st.st_size;row.mtime_ns=st.st_mtime_ns
    monkeypatch.setattr(m4_video.overlay_identity,'grab_evidence_frame',
                        lambda path,offsets:np.full((90,120,3),96,np.uint8))
    calls=[]
    def detector(frame,offset):
        calls.append(offset)
        return {'model_family':'test-detector', 'model_sha256':'a'*64,
                'candidates':[{'bbox_normalized':[.1,.2,.8,.9],
                'object_class':'person','confidence':.9}]}
    output=m4_video.preview_keyframes(clip=row,path=path,offsets=[.5,1.5],
                                       include_detection=True,detector=detector)
    assert calls==[.5,1.5]
    assert len(output['frames'])==2
    assert output['frames'][0]['detections']['candidates'][0]['object_class']=='person'
    assert output['tracklet_status']=='not_established'


def test_clip_changed_since_scan_fails_closed(tmp_path):
    path=tmp_path/'a.mp4';path.write_bytes(b'changed')
    with pytest.raises(m4_video.KeyframeError,match='m4c_clip_changed_rescan_required'):
        m4_video.ensure_same_recorded_file(path,clip())


def test_quality_and_jpeg_preview():
    frame=np.full((120,200,3),150,np.uint8)
    q=m4_video.frame_quality(frame)
    assert q['heuristic_only'] is True and 'blur_or_low_texture' in q['warnings']
    assert m4_video.jpeg_preview(frame)[:3]==b'\xff\xd8\xff'

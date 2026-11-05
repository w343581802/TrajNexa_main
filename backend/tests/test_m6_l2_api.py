import pytest

from app import map_db_spatial


def test_invalid_calibration_id_is_rejected_before_query():
    with pytest.raises(map_db_spatial.SpatialReadError) as caught:
        map_db_spatial.read_ground_calibration(
            dsn='postgresql://reader@127.0.0.1:1/none', calibration_id='not-a-uuid')
    assert str(caught.value) == 'calibration_id_invalid'


def test_missing_dsn_is_reported_not_swallowed():
    with pytest.raises(map_db_spatial.SpatialReadError) as caught:
        map_db_spatial.read_ground_calibration(dsn='', calibration_id='00000000-0000-0000-0000-000000000000')
    assert str(caught.value) == 'map_spatial_db_not_configured'

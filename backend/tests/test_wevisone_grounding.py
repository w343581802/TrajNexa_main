"""Contract tests only: no GPU or model weights required."""
import pytest

from app.wevisone_grounding import (
    WeVisOneParseError, parse_referring_response, referring_prompt,
)


def test_official_referring_prompt():
    assert referring_prompt("浅蓝上衣的骑车人") == (
        "Locate the instances that match the following description: 浅蓝上衣的骑车人.")


def test_one_box_normalized_and_pixels():
    raw = "<ref>rider</ref><box><100><200><400><700></box>"
    result = parse_referring_response(raw, 1280, 720)
    assert result["status"] == "candidates_unverified"
    assert result["boxes"][0]["box_norm_xyxy"] == [0.1, 0.2, 0.4, 0.7]
    assert result["boxes"][0]["box_px_xyxy"] == [128, 144, 512, 504]
    assert result["boxes"][0]["score"] is None
    assert result["identity_established"] is False


def test_multiple_boxes_and_deduplication():
    raw = ("<ref>person</ref><box><10><20><30><40></box>"
           "<ref>person</ref><box><60><70><80><90></box>"
           "<box><10><20><30><40></box>")
    result = parse_referring_response(raw, 1000, 1000)
    assert len(result["boxes"]) == 2
    assert all(item["verified"] is False for item in result["boxes"])


def test_model_reported_none_is_not_ground_truth():
    result = parse_referring_response("None<|im_end|>", 100, 80)
    assert result["status"] == "model_reported_absent"
    assert result["boxes"] == []
    assert result["review_required"] is True


@pytest.mark.parametrize("response", ["", "I see nothing", "<box><100><200><300></box>",
                                      "<box><10><20><30><40>", "none and other text"])
def test_unparseable_response_fails_closed(response):
    with pytest.raises(WeVisOneParseError):
        parse_referring_response(response, 100, 100)


@pytest.mark.parametrize("response", ["<box><100><200><100><400></box>",
                                      "<box><100><200><1001><400></box>",
                                      "<box><500><20><300><100></box>"])
def test_invalid_box_geometry_rejected(response):
    with pytest.raises(WeVisOneParseError):
        parse_referring_response(response, 100, 100)


def test_segmentation_token_does_not_change_box_contract():
    result = parse_referring_response("<box><100><100><200><200><OBJ></box>", 100, 100)
    assert result["boxes"][0]["box_px_xyxy"] == [10, 10, 20, 20]


def test_box_count_limit():
    body = "".join(f"<box><{i}><0><{i+1}><10></box>" for i in range(40))
    with pytest.raises(WeVisOneParseError, match="too_many_boxes"):
        parse_referring_response(body, 1000, 1000)


def test_ref_plus_box_none_counts_as_model_reported_absence():
    parsed = parse_referring_response(
        "<ref>person riding an elephant</ref><box>none</box>", 640, 480)
    assert parsed["status"] == "model_reported_absent"
    assert parsed["boxes"] == []
    assert parsed["rejection_literal"] == "none"
    assert parsed["identity_established"] is False

"""Optional WeVisOne-VL referring-grounding output adapter.

This adapter is deliberately independent from the M6 identity/review pipeline.
The released WeVisOne-VL model emits <0>.. <1000> coordinate tokens inside
<box> tags, or None if it reports no match. Those tokens are NOT a
calibrated identity confidence score.
"""
from __future__ import annotations

import re

BOX_RE = re.compile(r"<box>(.*?)</box>", re.IGNORECASE | re.DOTALL)
COORD_RE = re.compile(r"<([0-9]{1,4})>")


class WeVisOneParseError(ValueError):
    """A model response did not satisfy the official box-token contract."""


def referring_prompt(query: str) -> str:
    """Equivalent to the released wevisone_vl.prompts.build_prompt('referring')."""
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise WeVisOneParseError("invalid_referring_query")
    text = query.strip()
    if text[-1] not in ".?!":
        text += "."
    return "Locate the instances that match the following description: " + text


def parse_referring_response(response: str, width: int, height: int,
                             max_boxes: int = 32) -> dict:
    """Parse normalized WeVisOne boxes; never invent a score or establish identity.

    A malformed response fails closed rather than silently treating it as
    model-reported absence. Pixel boxes are in the original image coordinates.
    """
    if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1:
        raise WeVisOneParseError("invalid_image_size")
    if not isinstance(response, str):
        raise WeVisOneParseError("invalid_model_output")
    text = response.split("<|im_end|>", 1)[0].split("<|endoftext|>", 1)[0].strip()
    if text.lower() == "none":
        return {"status": "model_reported_absent", "boxes": [],
                "identity_established": False, "review_required": True,
                "score": None, "basis": "wevisone_referring"}
    if not text:
        raise WeVisOneParseError("invalid_model_output")

    chunks = BOX_RE.findall(text)
    if not chunks:
        raise WeVisOneParseError("invalid_model_output")
    result, seen = [], set()
    for body in chunks:
        coords = [int(item) for item in COORD_RE.findall(body)]
        if len(coords) == 0 or len(coords) % 4:
            raise WeVisOneParseError("invalid_box_tokens")
        for pos in range(0, len(coords), 4):
            box = tuple(coords[pos:pos + 4])
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
                raise WeVisOneParseError("invalid_box_geometry")
            if box in seen:
                continue
            if len(result) >= max_boxes:
                raise WeVisOneParseError("too_many_boxes")
            seen.add(box)
            norm = [value / 1000.0 for value in box]
            pixels = [round(x1 * width / 1000), round(y1 * height / 1000),
                      round(x2 * width / 1000), round(y2 * height / 1000)]
            result.append({"box_norm_xyxy": norm, "box_px_xyxy": pixels,
                           "score": None, "verified": False})
    return {"status": "candidates_unverified", "boxes": result,
            "identity_established": False, "review_required": True,
            "score": None, "basis": "wevisone_referring"}

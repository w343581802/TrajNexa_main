"""B8-SA1 接地点候选：从目标框推地面接触位置，保留来源与质量。"""
from __future__ import annotations


class ContactUnavailable(ValueError):
    pass


def from_box(box, kind="person", *, frame_width=None, frame_height=None):
    """box=(x1,y1,x2,y2) 像素；返回接地点候选，不把它当作已测量真值。"""
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        raise ContactUnavailable("box_invalid") from None
    if x2 <= x1 or y2 <= y1:
        raise ContactUnavailable("box_invalid")
    if kind not in ("person", "bicycle", "motorcycle", "vehicle", "generic"):
        raise ContactUnavailable("kind_unsupported")
    contact = {"x": round((x1 + x2) / 2.0, 1), "y": round(y2, 1), "kind": kind}
    quality = "estimated"
    uncertainties = ["contact_from_box_bottom"]
    if frame_width is not None and frame_height is not None:
        if x1 <= 1 or x2 >= float(frame_width) - 1:
            quality = "cut_off"
            uncertainties.append("object_touches_frame_edge")
        if y2 >= float(frame_height) - 1:
            quality = "cut_off"
            uncertainties.append("contact_at_frame_bottom")
        if y2 <= float(frame_height) * 0.28:
            quality = "far_field"
            uncertainties.append("contact_near_horizon")
    if kind in ("bicycle", "motorcycle", "vehicle"):
        uncertainties.append("wheel_contact_preferred")
    return {"contact_pixel": [contact["x"], contact["y"]], "kind": kind,
            "quality": quality, "identity_status": "candidate_unverified",
            "uncertainties": uncertainties}

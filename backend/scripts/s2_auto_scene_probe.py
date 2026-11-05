
"""S2-B 传统几何探针：直线→消失点→地平线→焦距→地面单应（相机相对）。

只做候选估计与不确定度记录，不产生经纬度、不写库、不发布。
用法：python s2_auto_scene_probe.py frame.png --tilt 25 --height 4 --bearing 355
"""
from __future__ import annotations
import argparse, json, math
import cv2
import numpy as np


def detect_lines(gray, scale):
    edges = cv2.Canny(gray, 60, 160)
    raw = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70,
                          minLineLength=int(70 * scale), maxLineGap=int(12 * scale))
    lines = []
    if raw is not None:
        for x1, y1, x2, y2 in raw.reshape(-1, 4):
            dx, dy = x2 - x1, y2 - y1
            if abs(dy) < 0.35 * abs(dx):   # 排除接近水平的线
                continue
            if (y1 + y2) / 2 < gray.shape[0] * 0.35:   # 道路主要在画面下半部
                continue
            a, b, c = dy, -dx, dx * y1 - dy * x1
            norm = math.hypot(a, b)
            lines.append((a / norm, b / norm, c / norm))
    return lines


def _intersect(l1, l2):
    p = np.cross(np.array(l1), np.array(l2))
    if abs(p[2]) < 1e-9:
        return None
    return p[:2] / p[2]


def vanishing_point(lines, cy):
    """RANSAC 选道路方向消失点：必须在画面上方、支撑线最多。"""
    best, best_inliers = None, []
    n = len(lines)
    if n < 4:
        return None, []
    rng = np.random.default_rng(7)
    for _ in range(600):
        i, j = rng.integers(0, n, 2)
        if i == j:
            continue
        vp = _intersect(lines[i], lines[j])
        if vp is None or not (-6000 < vp[0] < 6000) or not (-6000 < vp[1] < cy):
            continue
        inliers = [k for k, (a, b, c) in enumerate(lines)
                   if abs(a * vp[0] + b * vp[1] + c) < 2.0]
        if len(inliers) > len(best_inliers):
            best, best_inliers = vp, inliers
    if best is None or len(best_inliers) < 4:
        return None, []
    A = np.array([[lines[k][0], lines[k][1], lines[k][2]] for k in best_inliers])
    _, _, vt = np.linalg.svd(A)
    vp = vt[-1][:2] / vt[-1][2]
    return vp, best_inliers


def homography(vp_y, f, cx, cy, tilt_deg, height_m, bearing_deg):
    t = math.radians(tilt_deg)
    K = np.array([[1 / f, 0, -cx / f], [0, 1 / f, -cy / f], [0, 0, 1.0]])
    M = np.array([[1, 0, 0],
                  [0, -math.cos(t), -math.sin(t)],
                  [0, -math.sin(t), math.cos(t)]])
    P = np.array([[1, 0, 0], [0, 0, 1], [0, -1 / height_m, 0]])
    H_cam = P @ M @ K
    b = math.radians(bearing_deg)
    R = np.array([[math.cos(b), math.sin(b), 0],
                  [-math.sin(b), math.cos(b), 0],
                  [0, 0, 1]])
    return R @ H_cam


def project(H, u, v):
    p = H @ np.array([u, v, 1.0])
    return p[0] / p[2], p[1] / p[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--tilt', type=float, default=25.0)
    ap.add_argument('--height', type=float, default=4.0)
    ap.add_argument('--bearing', type=float, default=355.0)
    ap.add_argument('--points', default='')
    args = ap.parse_args()
    img = cv2.imread(args.image)
    h, w = img.shape[:2]
    scale = 960.0 / w
    gray = cv2.cvtColor(cv2.resize(img, (960, int(h * scale))), cv2.COLOR_BGR2GRAY)
    lines = detect_lines(gray, 1.0)
    vp, inliers = vanishing_point(lines, gray.shape[0] / 2.0)
    out = {'image': args.image, 'size': [w, h], 'lines': len(lines),
           'vp_inliers': len(inliers), 'pose': {'tilt_deg': args.tilt,
           'height_m': args.height, 'bearing_deg': args.bearing}}
    if vp is None:
        out['status'] = 'no_vanishing_point'
        print(json.dumps(out, ensure_ascii=False, indent=2)); return
    vp_full = [round(float(vp[0]) / scale, 1), round(float(vp[1]) / scale, 1)]
    out['vanishing_point_px'] = vp_full
    horizon_gap = h / 2.0 - vp_full[1]
    out['horizon_gap_px'] = round(horizon_gap, 1)
    if horizon_gap <= 0:
        out['status'] = 'horizon_above_center_check_pose'
        print(json.dumps(out, ensure_ascii=False, indent=2)); return
    f = horizon_gap / math.tan(math.radians(args.tilt))
    out['focal_px'] = round(f, 1)
    out['vfov_deg'] = round(2 * math.degrees(math.atan(h / (2 * f))), 1)
    H = homography(vp_full[1], f, w / 2.0, h / 2.0, args.tilt, args.height, args.bearing)
    out['homography_3x3'] = [[round(float(v), 6) for v in row] for row in H]
    pts = [item.split(',') for item in args.points.split(';') if item.strip()]
    table = []
    for name, u, v in pts:
        x, y = project(H, float(u), float(v))
        table.append({'name': name, 'px': [float(u), float(v)], 'enu_rel_camera_m': [round(x, 1), round(y, 1)],
                      'range_m': round(math.hypot(x, y), 1)})
    out['points'] = table
    out['status'] = 'estimated'
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

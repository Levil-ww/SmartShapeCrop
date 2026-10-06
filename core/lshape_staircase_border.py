"""阶梯专用三层素材剖面：保留各轴的线宽及抗锯齿像素。"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .lshape_border_route import (
    _edge_profiles, _segment_profile, _is_anchor_seg,
    _is_weak_edge_transition, _color_dist,
)


def _profiles(src: Image.Image):
    a = np.asarray(src.convert('RGB'))
    short = min(src.size)
    if short < 40:
        return {}
    result = {}
    for edge, (smooth, std, raw) in _edge_profiles(a, max(8, int(short*.35))).items():
        segs = _segment_profile(smooth, std, raw)
        anchor = next((s for i, s in enumerate(segs)
                       if _is_anchor_seg(s) and not _is_weak_edge_transition(segs, i)), None)
        if anchor is None or anchor.d0 > 12:
            continue
        # 只接管薄描边 + 平整浅色边距 + 连续内框线；其他样式沿用旧路由。
        band = next((s for s in segs if anchor.d1 < s.d0 <= anchor.d1+12
                     and s.thickness >= 8 and s.std < 3
                     and min(s.color) >= 185), None)
        if band is None or anchor.thickness > band.thickness:
            continue
        start = band.d1+1
        limit = min(len(raw), start+max(64, min(int(short*.08), band.thickness)))
        end = start
        # 不能在第一个灰色过渡段收尾；一直读到整条框线返回浅色带。
        while end < limit and _color_dist(tuple(raw[end]), band.color) > 25:
            if std[end] >= 20:
                break
            end += 1
        if end == limit or end-start < 2 or std[start:end].max(initial=0) >= 20:
            continue
        line = raw[start:end]
        contrast = np.linalg.norm(line-np.asarray(band.color), axis=1)
        if contrast.max(initial=0) < 60:
            continue
        core = line[contrast >= contrast.max()*.8]
        color = tuple(np.rint(np.median(core, axis=0)).astype(int))
        if max(color) >= 185 and max(color)-min(color) < 35:
            continue
        result[edge] = (np.rint(raw[:end+2]).clip(0, 255).astype(np.uint8),
                        (0, band.d0, start, end), band.color, color)
    # 连续框线需要至少三边同色结构，避免把内容花纹当成边框。
    if len(result) < 3:
        return {}
    values = list(result.values())
    if any(_color_dist(v[2], values[0][2]) > 25
           or _color_dist(v[3], values[0][3]) > 40 for v in values):
        return {}
    return result


def _scaled_profile(profile, scale):
    raw, bounds, _, _ = profile
    scale = scale if scale > 0 else 1
    offsets = [0]
    for bound in bounds[1:]:
        offsets.append(max(offsets[-1]+1, round(bound*scale)))
    # 在同一全局像素网格上采样；逐层 resize 会改变细线的采样相位。
    positions = (np.arange(offsets[-1])+.5)/scale-.5
    sampled = np.stack([np.interp(positions, np.arange(len(raw)), raw[:, ch])
                        for ch in range(3)], axis=1)
    return np.rint(sampled).astype(np.uint8), np.asarray(offsets, dtype=np.float32)


def apply_staircase_material_profile(canvas, src, corner, sx, sy, rects, mask=None):
    """仅沿真实挖空轮廓补三层边框；未识别样式返回 False 交旧路径。"""
    profiles = _profiles(src)
    if not profiles:
        return False
    ex = 'left' if corner in ('tl', 'bl') else 'right'
    ey = 'bottom' if corner in ('bl', 'br') else 'top'
    px, bx = _scaled_profile(profiles.get(ex, profiles.get('right' if ex == 'left' else 'left')), sx)
    py, by = _scaled_profile(profiles.get(ey, profiles.get('top' if ey == 'bottom' else 'bottom')), sy)
    h, w = canvas.shape[:2]
    if mask is None:
        mask = np.zeros((h, w), bool)
        for x0, y0, x1, y1 in rects or []:
            mask[max(0, round(y0)):min(h, round(y1)),
                 max(0, round(x0)):min(w, round(x1))] = True
    else:
        mask = np.asarray(mask, dtype=bool)
    if mask.shape != (h, w) or not mask.any():
        return False
    import cv2
    ys, xs = np.where(mask)
    x0, x1 = max(0, int(xs.min())-len(px)), min(w, int(xs.max())+1+len(px))
    y0, y1 = max(0, int(ys.min())-len(py)), min(h, int(ys.max())+1+len(py))
    cut = mask[y0:y1, x0:x1]
    contours, _ = cv2.findContours(cut.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    yy = np.arange(cut.shape[0], dtype=np.float32)[:, None]
    xx = np.arange(cut.shape[1], dtype=np.float32)[None, :]
    best = np.full(cut.shape, np.inf, dtype=np.float32)
    colors = np.zeros((*cut.shape, 3), dtype=np.uint8)

    def progress(distance, bounds):
        # 第一保留像素距 cut 像素为1，对应素材剖面的第0像素。
        index = np.maximum(0, distance-1)
        return np.interp(index, bounds, (0, 1, 2, 3), right=4).astype(np.float32)

    for contour in contours:
        points = contour[:, 0]
        for p, q in zip(points, np.roll(points, -1, axis=0)):
            # 栅格凸角的1px对角段拆成两个端点，不能把其包围盒当成 cut。
            segments = [(p, p), (q, q)] if (p[0] != q[0] and p[1] != q[1]) else [(p, q)]
            for p, q in segments:
                dx = np.maximum(np.maximum(min(p[0], q[0])-xx, xx-max(p[0], q[0])), 0)
                dy = np.maximum(np.maximum(min(p[1], q[1])-yy, yy-max(p[1], q[1])), 0)
                gx, gy = progress(dx, bx), progress(dy, by)
                distance = np.maximum(gx, gy)
                update = (~cut) & (distance < best) & (distance < 3)
                if not update.any():
                    continue
                ix = np.minimum(np.maximum(dx-1, 0).astype(int), len(px)-1)
                iy = np.minimum(np.maximum(dy-1, 0).astype(int), len(py)-1)
                use_x = (gx > gy) | ((gx == gy) & (dx >= dy))
                sampled = np.where(use_x[..., None], px[ix], py[iy])
                colors[update] = sampled[update]
                best[update] = distance[update]
    # 每层只接到原框的对应层，避免内线延伸到画布外描边。
    allowed = np.full(cut.shape, 4, dtype=np.float32)
    for touched, distance, bounds in (
        (y0 == 0 and cut[0].any(), yy+y0+1, by),
        (y1 == h and cut[-1].any(), h-(yy+y0), by),
        (x0 == 0 and cut[:, 0].any(), xx+x0+1, bx),
        (x1 == w and cut[:, -1].any(), w-(xx+x0), bx),
    ):
        if touched:
            allowed = np.minimum(allowed, progress(distance, bounds))
    paint = (~cut) & (best < 3) & (np.floor(best) <= np.floor(allowed))
    canvas[y0:y1, x0:x1][paint] = colors[paint]
    return bool(paint.any())

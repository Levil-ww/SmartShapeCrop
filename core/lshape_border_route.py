"""
core/lshape_border_route.py
L 形挖角「描边 + 色带 + 细边框」结构重走线（Profile Route）。

背景
----
V13 路径（core/lshape_border.py::detect_border_v13 / patch_lshape_cut）只认
「最外黑描边 + 主色带」两层结构，且要求最外段是近黑色。对蔓生花 / 中古雨林
这类素材 —— 最外是米色边距（或白色边距），之后是细线、点状色带、细边框 ——
V13 检测返回 None，旧路径 detect_pool_material_borders 也会把与中心色接近的
层过滤掉，导致 L 形挖角完全没有边框补全（输出呈方形切口观感）。

本模块做的事（纯加性，不修改任何既有函数）：
  1. detect_border_profile(src_img)
     对素材四条边做"由外向内"的 1D 颜色剖面扫描（多条扫描线取均值以抹平
     点状花纹），分割出有序层结构，输出
     [(color, thickness_px), ...]（外→内，最多 3 层，含外边距层）。
     典型结构映射（真实素材校准，2026-09-08 三层封顶）：
       克罗印花 [黑描边, 棕色带(直通内部,限厚)]        → 2 层（路由让位 V13）
       蔓生花   [黑描边, 米色边距, 细线]               → 3 层（止于最外内框线）
       中古雨林 [黑描边, 白边距, 框线]                 → 3 层（同上）
       庄园秘境 [出血白边(锚点跳过), 粗黑带, 米底]     → 2 层
     点带 / 文字带及其内侧细线**不处理**（用户指定的层深边界）。
  2. patch_lshape_cut_layers(canvas, ...)
     patch_lshape_cut 的 N 层推广：沿两条切边在保留区一侧按层厚依次铺色，
     内凹角用 max(dx, dy) 几何分层，保证边框沿 L 形轮廓连续。
  3. apply_lshape_profile_path(...)
     厚度按 scale 换算到画布坐标系后调用 2，供
     lshape_border.apply_lshape_border_completion 的自动路由优先调用。

路由约定（见 apply_lshape_border_completion）：
  手动参数 → V13 路径（不变）
  自动：Profile 路径 → V13 路径 → 旧 detect_pool_material_borders 路径
  任一环节失败自动落到下一环节，行为向后兼容。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .geometry import RectShape

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 检测参数（经验值，集中在顶部便于调优）
# ---------------------------------------------------------------------------

_MAX_LAYERS = 3                 # 最多输出层数（描边 / 色带 / 最外内框线）
_ANCHOR_MIN_THICK = 2           # 锚点/线段最小厚度：滤掉 1px JPEG 灰过渡段
_SCAN_FRACS = (0.30, 0.50, 0.70)  # 每条边取 3 条扫描线（比例位置）
_SEG_SPLIT_TOL = 15             # 剖面相邻深度 L1 距离 > 该值 → 断段
_MERGE_L1_TOL = 60              # 相邻段中位色 L1 < 该值 → 合并（抗梯度坡道碎片）
_FIELD_COLOR_TOL = 25.0         # 段均值色与中心参考色 L2 < 该值 → 视为" field 色"
_FIELD_STD_TOL = 12.0           # 段内扫描线标准差 < 该值 → 视为平整（非花纹）
_LINE_STD_MAX = 20.0            # 线判定的 std 容差（比 field 判定宽：邻段平滑会
                                # 把花纹的列间差异扩散进线段，实测可达 ~17）
_MIN_DIM_PROFILE = 40           # 素材短边小于该值不做检测
_DEPTH_CAP_RATIO = 0.35         # 剖面最大扫描深度 = 短边 × 该比例
_STRUCT_WINDOW_RATIO = 0.15     # 边框结构窗口 = 短边 × 该比例（窗口外视为内部花纹）
_TOTAL_DEPTH_RATIO = 0.50       # 边距+各层总厚 ≤ 短边 × 该比例（粗黑带 60/300 仍可过）
_GIANT_BAND_RATIO = 0.06        # 巨型 field 平段限厚收录厚度 = 短边 × 该比例
_THICK_BLACK_MIN = 50           # 首层近黑且 ≥ 该厚度 → 交给 V13（已验证路径）
_BLACK_MAX_CHANNEL = 90         # 近黑判定：max(r,g,b) < 该值
_DARK_LINE_MAX_CHANNEL = 185    # 细线判定：低纹理段 max(rgb) < 该值视为"线"
                                # （真实素材细框线跨边漂移 168~179，需放宽）
_LINE_MAX_THICK = 32            # "线"的最大厚度（源像素）：真实素材细框线可达 24px


@dataclass
class _Seg:
    """剖面中的一个颜色段。"""
    d0: int                  # 起始深度（含）
    d1: int                  # 结束深度（含）
    color: tuple[int, int, int]
    std: float               # 段内像素在多条扫描线间的标准差（纹理性指标）

    @property
    def thickness(self) -> int:
        return self.d1 - self.d0 + 1


# ---------------------------------------------------------------------------
# 1. 剖面提取与分割
# ---------------------------------------------------------------------------

def _smooth_1d(a: np.ndarray, window: int = 5) -> np.ndarray:
    """反射填充的滑动平均（抹平点状带造成的剖面震荡，保序不保距 ±2px）。"""
    if a.shape[0] < window or window <= 1:
        return a
    pad = window // 2
    padded = np.pad(a, ((pad, pad),) + ((0, 0),) * (a.ndim - 1), mode='reflect')
    kernel = np.ones(window, dtype=np.float64)
    if a.ndim == 1:
        return np.convolve(padded, kernel, mode='valid') / window
    out = np.empty_like(a, dtype=np.float64)
    for ch in range(a.shape[1]):
        out[:, ch] = np.convolve(padded[:, ch], kernel, mode='valid') / window
    return out


def _edge_profiles(arr: np.ndarray, depth: int) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """四条边的"由外向内"剖面。

    Returns:
        {edge_name: (profile_smoothed, std_smoothed, profile_raw)}，其中
        profile_smoothed: (depth, 3) float64 —— 3 条扫描线逐深度均值 + 滑动平均
        std_smoothed:     (depth,)  float64 —— 逐深度扫描线间标准差（通道均值）+ 滑动平均
        profile_raw:      (depth, 3) float64 —— 未平滑均值（用于取段颜色中位数）
    """
    H, W = arr.shape[:2]
    cols = [min(W - 1, int(round(f * W))) for f in _SCAN_FRACS]
    rows = [min(H - 1, int(round(f * H))) for f in _SCAN_FRACS]

    def _pack(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        # samples: (depth, n_scan, 3)
        raw = samples.mean(axis=1)
        std = samples.std(axis=1).mean(axis=1)
        # 滑动平均抹平点状带震荡（否则点带会被切成多段）；
        # 段颜色另从 raw 取中位数，避免薄描边被平滑坡道拖浅
        return _smooth_1d(raw), _smooth_1d(std), raw

    d = min(depth, H, W)
    out: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    out['top'] = _pack(np.stack([arr[:d, c, :] for c in cols], axis=1).astype(np.float64))
    out['bottom'] = _pack(np.stack([arr[H - 1:H - 1 - d:-1, c, :] for c in cols], axis=1).astype(np.float64))
    out['left'] = _pack(np.stack([arr[r, :d, :] for r in rows], axis=1).astype(np.float64))
    out['right'] = _pack(np.stack([arr[r, W - 1:W - 1 - d:-1, :] for r in rows], axis=1).astype(np.float64))
    return out


def _segment_profile(profile: np.ndarray, std: np.ndarray,
                     raw: np.ndarray) -> list[_Seg]:
    """把 (depth,3) 剖面按颜色变化切段。

    段边界取自平滑剖面的相邻跳变；随后按**段中位色相似度**做栈式合并
    （L1 < _MERGE_L1_TOL 的相邻段合并）。中位色取自原始剖面：
    对"薄描边+平滑坡道"稳（均值会被坡道像素拖浅），且 1px 细线段
    不会被链式并入邻居而丢失。
    """
    n = profile.shape[0]
    cuts = [0]
    for i in range(1, n):
        if np.abs(profile[i] - profile[i - 1]).sum() > _SEG_SPLIT_TOL:
            cuts.append(i)
    cuts.append(n)

    ranges = [(cuts[i], cuts[i + 1] - 1) for i in range(len(cuts) - 1)]

    out: list[list] = []  # [d0, d1, median_color]
    for s, e in ranges:
        color = tuple(int(round(v)) for v in np.median(raw[s:e + 1], axis=0))
        if out and sum(abs(a - b) for a, b in zip(out[-1][2], color)) < _MERGE_L1_TOL:
            out[-1][1] = e  # 颜色相近 → 并入前段（保前段色，减少碎片）
        else:
            out.append([s, e, color])

    segs: list[_Seg] = []
    for s, e, color in out:
        seg_std = float(std[s:e + 1].mean())
        segs.append(_Seg(s, e, color, seg_std))
    return segs


# ---------------------------------------------------------------------------
# 2. 单条剖面的结构分类
# ---------------------------------------------------------------------------

def _color_dist(c1: tuple, c2: tuple) -> float:
    return float(np.linalg.norm(np.array(c1, dtype=np.float64)
                                - np.array(c2, dtype=np.float64)))


def _is_line_seg(s: _Seg) -> bool:
    """线段判定：低纹理 + 深色 + 薄（细描边 / 细框线）。

    最小厚度 = _ANCHOR_MIN_THICK 滤掉 1px JPEG 灰过渡段（典型颜色 ~116，
    在 _DARK_LINE_MAX_CHANNEL 之下，但不属于真实结构）。真实印刷素材框线
    最薄 ≥4px（线稿 5965x650 实测），可安全过滤。
    """
    return (s.std < _LINE_STD_MAX
            and max(s.color) < _DARK_LINE_MAX_CHANNEL
            and _ANCHOR_MIN_THICK <= s.thickness <= _LINE_MAX_THICK)


def _is_anchor_seg(s: _Seg) -> bool:
    """锚点（描边 / 粗黑带）判定：近黑 + 低纹理，不限厚度。

    为什么用 _BLACK_MAX_CHANNEL 而非 _DARK_LINE_MAX_CHANNEL：
    1px JPEG 灰过渡段（~116 max）应被排除（混入锚点会让四边层序错位、
    投票把 4 边平均成灰糊 —— 这正是 2026-09-08 蔓生花/中古雨林实跑"补了
    5 层但视觉无效果"的根因）。真实素材描边/边框均为近黑。
    不限厚度是因为庄园秘境素材黑带 117px（远大于 _LINE_MAX_THICK=32），
    仍要被锚点命中。
    """
    return (s.std < _LINE_STD_MAX
            and max(s.color) < _BLACK_MAX_CHANNEL
            and s.thickness >= _ANCHOR_MIN_THICK)


def _classify_profile(segs: list[_Seg], field_ref: tuple,
                      thick_cap: int, max_struct_depth: int,
                      giant_cap_t: int) -> list[tuple[tuple[int, int, int], int]] | None:
    """从外向内走段分类，输出「描边 + 色带 + 内框线」≤3 层结构。

    算法（对 蔓生花 / 中古雨林 / 庄园秘境 / 克罗印花 真实素材剖面校准）：
      1. 窗口收集：只收深度 < max_struct_depth 的段；巨段（厚度 > thick_cap）
         若为深色平段 → 色带直通内部（克罗印花棕带），限厚收录后收尾；
         否则（浅色巨段 = 内部底色/花纹）直接收尾。
      2. 锚点对齐：跳过最外「出血白边/裁切边」（真实素材四边常有 1~6px
         不对称白边，不跳过会使四边层序错位、投票均色成灰），锚点 =
         第一个「近黑 + 低纹理」段（描边或粗黑带，不限厚度）。
      3. 截断到第二条细线：描边(线1) + 色带 + 内框线(线2) —— 用户明确
         指定只补到最外那条内框线，点带 / 文字带及其内侧细线不处理。
         无第二条细线时保留 锚点 + 紧邻平整带（克罗式 2 层，交路由让位
         判定 profile_yields_to_v13）。
      4. 分组：锚点（粗黑带）独立成层，避免被紧随的米色/白色带并入拉灰
         变成均值色（真实剖面 117px 黑带 + 39px 米底 平均成 (97,95,89) 的
         灰糊 —— 用户 L 形挖角看到的就是这个）；细线独立成层；其余非线
         段合并为一个带层。

    返回层列表或 None（无锚点 / 总厚超限）。
    """
    # —— 1. 窗口收集 ——
    collected: list[_Seg] = []
    depth_acc = 0
    giant_tail = False
    for seg in segs:
        if depth_acc >= max_struct_depth:
            break
        if seg.thickness > thick_cap:
            if (collected and _color_dist(seg.color, field_ref) < _FIELD_COLOR_TOL
                    and seg.std < _FIELD_STD_TOL
                    and max(seg.color) < _DARK_LINE_MAX_CHANNEL):
                # 与中心同色的深色巨平段：色带直通内部（如克罗印花棕带），限厚收录；
                # 浅色巨段（花田/白底直通内部）不收录，按内部底色收尾
                collected.append(_Seg(seg.d0, seg.d0 + max(1, giant_cap_t) - 1,
                                      seg.color, seg.std))
                giant_tail = True
            break
        collected.append(seg)
        depth_acc += seg.thickness
    if not collected:
        return None

    # —— 2. 锚点对齐：跳过最外出血边/裁切边 ——
    anchor_idx = next(
        (i for i, s in enumerate(collected) if _is_anchor_seg(s)),
        -1,
    )
    if anchor_idx < 0:
        return None
    collected = collected[anchor_idx:]

    # —— 3. 截断到第二条细线（用户层深边界）——
    # 先在 line#2 之前的非线段中丢掉花纹段（std 高），只保留紧邻锚点
    # 的第一个平整带 —— 避免「左 2px 出血白边 + 中古雨林文字带」场景下
    # 文字带均值色污染中间层（修复 2026-09-08 left_bleed 测试所见）
    if not giant_tail:
        # 从 i=1 起扫描；遇到 _is_line_seg 时把 band_end 推到它的 idx（保留这条线本身）
        # 遇到 std >= _FIELD_STD_TOL 的非线段 → 截断（视为花纹）
        band_end = 1  # 至少保留锚点
        for i in range(1, len(collected)):
            s = collected[i]
            if _is_line_seg(s) or _is_anchor_seg(s):
                band_end = i + 1   # 保留该线段本身
                break
            if s.std >= _FIELD_STD_TOL:
                # 花纹段（文字带 / 点带 / 边缘羽化）：截断
                break
            band_end = i + 1
        collected = collected[:band_end]
        # 截断到第二条细线
        line_seen = 0
        cut_at = -1
        for i, s in enumerate(collected):
            if _is_line_seg(s):
                line_seen += 1
                if line_seen >= 2:
                    cut_at = i
                    break
        if cut_at >= 0:
            collected = collected[:cut_at + 1]
        else:
            # 无第二条细线：锚点 + 紧邻平整带（克罗式 描边+色带）。
            if len(collected) >= 2:
                collected = collected[:2]
            else:
                collected = collected[:1]

    # —— 4. 分组：锚点（粗黑带）/细线 独立成层；非线段合并带层 ——
    grouped: list[list[_Seg]] = []
    for s in collected:
        if _is_anchor_seg(s) and s.thickness > _LINE_MAX_THICK:
            # 粗黑带（庄园秘境 117px）独立成层，避免与紧随的米/白带
            # 平均成灰糊 —— 这是用户 L 形挖角"补了但视觉无效果"的根因
            grouped.append([s])
        elif _is_line_seg(s):
            grouped.append([s])          # 细线层（描边或细框线）
        elif grouped and not _is_anchor_seg(grouped[-1][0]) \
                and not _is_line_seg(grouped[-1][0]):
            grouped[-1].append(s)        # 并入当前带组
        else:
            grouped.append([s])          # 新带组

    layers: list[tuple[tuple[int, int, int], int]] = []
    for grp in grouped:
        merged = _merge_segs(grp)
        if merged:
            layers.append(merged)
    layers = layers[:_MAX_LAYERS]
    if not layers:
        return None

    total = sum(t for _, t in layers)
    if total <= 0 or total > thick_cap * 1.4:
        return None
    return layers


def profile_yields_to_v13(layers: list[tuple[tuple[int, int, int], int]]) -> bool:
    """Profile 让位判定：V13 已验证的「黑描边+主带」场景交还 V13。

    让位情形：
      - 首层近黑且 ≥_THICK_BLACK_MIN（厚黑描边）
      - 黑细描边 + 深色带（克罗印花类双层结构）

    路由层先调 V13，V13 命中则用 V13，V13 未命中则 Profile 接管
    （如庄园秘境粗黑带：V13 检不到"主色带"会返回 None）。
    """
    if not layers:
        return False
    first_color, first_t = layers[0]
    if max(first_color) >= _BLACK_MAX_CHANNEL:
        return False
    if first_t >= _THICK_BLACK_MIN:
        return True
    if len(layers) >= 2 and max(layers[1][0]) < _DARK_LINE_MAX_CHANNEL:
        return True
    return False


def _merge_segs(segs: list[_Seg]) -> tuple[tuple[int, int, int], int] | None:
    """把若干段按厚度加权合并为一层。"""
    if not segs:
        return None
    total_t = sum(s.thickness for s in segs)
    acc = np.zeros(3, dtype=np.float64)
    for s in segs:
        acc += np.array(s.color, dtype=np.float64) * s.thickness
    color = tuple(int(round(v)) for v in acc / total_t)
    return (color, total_t)


# ---------------------------------------------------------------------------
# 3. 四边一致性投票 → 最终层结构
# ---------------------------------------------------------------------------

def detect_border_profile(src_img: Image.Image) -> list[tuple[tuple[int, int, int], int]] | None:
    """检测素材的「边距 + 描边 + 色带 + 细框」剖面结构。

    Args:
        src_img: 原始素材图（建议未拉伸的原图）

    Returns:
        [(color, thickness_px), ...] 外→内（源图像素单位），或 None：
        - 素材过小 / 无锚点（缺近黑平整描边）/ 无法分割
        - 总厚超限
        - 四边结构层数不一致（<3 条边层层数一致，疑似花纹误检）

    注：让位给 V13 的逻辑（首层厚黑 / 黑细描边+深色带）由
    profile_yields_to_v13 在路由层执行，使 V13 未命中时 Profile 仍能
    接管（如庄园秘境粗黑带：V13 检不到主色带会返回 None）。
    """
    if src_img is None:
        return None
    arr = np.asarray(src_img.convert('RGB') if src_img.mode != 'RGB' else src_img)
    H, W = arr.shape[:2]
    min_dim = min(H, W)
    if min_dim < _MIN_DIM_PROFILE:
        return None

    depth = max(8, int(min_dim * _DEPTH_CAP_RATIO))
    thick_cap = max(60, int(min_dim * 0.25))
    max_struct_depth = max(120, int(min_dim * _STRUCT_WINDOW_RATIO))
    giant_cap_t = max(60, int(min_dim * _GIANT_BAND_RATIO))

    # 中心参考色（中段 40% 区域中位数，抗内部花纹干扰）
    r0, r1 = int(H * 0.3), max(int(H * 0.3) + 4, int(H * 0.7))
    c0, c1 = int(W * 0.3), max(int(W * 0.3) + 4, int(W * 0.7))
    field_ref = tuple(int(round(v)) for v in np.median(
        arr[r0:r1, c0:c1].reshape(-1, 3), axis=0))

    per_edge: dict[str, list | None] = {}
    for edge, (prof, std, raw) in _edge_profiles(arr, depth).items():
        segs = _segment_profile(prof, std, raw)
        per_edge[edge] = _classify_profile(segs, field_ref, thick_cap,
                                           max_struct_depth, giant_cap_t)

    results = [v for v in per_edge.values() if v is not None]
    if not results:
        logger.info("[LShapeRoute] 四边均未检出边框结构")
        return None

    # 层数投票，需 ≥3 条边一致
    votes: dict[int, int] = {}
    for r in results:
        votes[len(r)] = votes.get(len(r), 0) + 1
    best_count = max(votes, key=lambda k: votes[k])
    if votes[best_count] < 3:
        logger.info("[LShapeRoute] 层数投票不一致 %s（<3 边一致），放弃", votes)
        return None

    agreed = [r for r in results if len(r) == best_count]
    layers: list[tuple[tuple[int, int, int], int]] = []
    for li in range(best_count):
        color = tuple(int(round(np.mean([r[li][0][ch] for r in agreed])))
                      for ch in range(3))
        t = int(round(float(np.mean([r[li][1] for r in agreed]))))
        if t < 1:
            t = 1
        layers.append((color, t))

    total = sum(t for _, t in layers)
    if total > min_dim * _TOTAL_DEPTH_RATIO:
        logger.info("[LShapeRoute] 总厚度 %dpx > %.0f%%×%dpx，判定为图案误检",
                    total, _TOTAL_DEPTH_RATIO * 100, min_dim)
        return None
    logger.info("[LShapeRoute] 命中 %d 层结构（%d/4 边一致）: %s",
                len(layers), votes[best_count], layers)
    return layers


# ---------------------------------------------------------------------------
# 4. N 层切边补画（patch_lshape_cut 的推广）
# ---------------------------------------------------------------------------

def _fill_layers_vertical_horizontal(b: np.ndarray, xc: int, yc: int,
                                     layers: list[tuple[tuple[int, int, int], int]],
                                     offs: list[int]) -> None:
    """在"缺口贴右上角"的画布 b 上按层补齐切边边框。

    坐标语义与 lshape_border._fill_vertical_horizontal 完全一致：
      xc = 垂直切边 x（缺口左边界）；yc = 水平切边 y（缺口下边界）；
      产品位于 x<xc 与 y>yc。层 k 铺在深度 [offs[k], offs[k+1])，
      内凹角按 max(dx, dy) 几何分层。
    """
    H, W = b.shape[:2]
    T = offs[-1]

    # 垂直切边（保留区在左）：层 k 的 y 从自身深度 offs[k] 起，与素材顶边结构对齐
    for k, (color, _t) in enumerate(layers):
        x_lo, x_hi = max(0, xc - offs[k + 1]), min(W, xc - offs[k])
        y_lo, y_hi = min(offs[k], yc), min(yc, H)
        if x_hi > x_lo and y_hi > y_lo:
            b[y_lo:y_hi, x_lo:x_hi] = color

    # 水平切边（保留区在下）：层 k 的 x 到 W - offs[k] 止，避开右缘外层结构
    for k, (color, _t) in enumerate(layers):
        y_lo, y_hi = max(0, yc + offs[k]), min(H, yc + offs[k + 1])
        x_lo, x_hi = max(0, xc), max(0, min(W - offs[k], W))
        if x_hi > x_lo and y_hi > y_lo:
            b[y_lo:y_hi, x_lo:x_hi] = color

    # 内凹角 (xc, yc)：距角点几何 L 分层，各层沿角直角连续
    xs = np.arange(max(0, xc - T), xc)
    if xs.size == 0:
        return
    offs_arr = np.array(offs, dtype=np.int64)
    colors_arr = np.array([c for c, _t in layers], dtype=np.uint8)
    y_end = min(H, yc + T)
    for yy in range(max(0, yc), y_end):
        d = np.maximum(xc - xs, yy - yc)
        k = np.searchsorted(offs_arr[1:], d, side='left')
        k = np.clip(k, 0, len(layers) - 1)
        b[yy, xs] = colors_arr[k]


def patch_lshape_cut_layers(canvas: np.ndarray, corner: str,
                            x0: int, y0: int, cw: int, ch: int,
                            layers: list[tuple[tuple[int, int, int], int]],
                            ) -> np.ndarray:
    """在最终画布上沿缺口切边按层结构补边（不修改入参，返回新数组）。

    Args:
        canvas: (H, W, 3) uint8 最终渲染结果（缺口区已是洞色）
        corner: 'tl'|'tr'|'bl'|'br'
        x0, y0, cw, ch: 缺口矩形（画布像素，与渲染 mask 同源）
        layers: [(color, thickness_px), ...] 外→内（画布像素单位，厚度 ≥1）

    契约与 patch_lshape_cut 一致：翻转后缺口必须贴画布右上角，
    否则抛 ValueError。
    """
    a = np.asarray(canvas).copy()
    if a.dtype != np.uint8:
        a = a.astype(np.uint8)
    if not layers:
        raise ValueError('layers 为空')
    offs = [0]
    for _c, t in layers:
        t_i = max(1, int(round(t)))
        offs.append(offs[-1] + t_i)
    if offs[-1] <= 0:
        raise ValueError('层厚合计为 0')

    H, W = a.shape[:2]
    flipx = corner in ('tl', 'bl')
    flipy = corner in ('bl', 'br')
    b = a
    if flipx:
        b = np.fliplr(b)
    if flipy:
        b = np.flipud(b)
    nx0 = (W - x0 - cw) if flipx else x0
    ny0 = (H - y0 - ch) if flipy else y0
    nx1 = nx0 + cw
    ny1 = ny0 + ch
    if not (nx1 == b.shape[1] and ny0 == 0):
        raise ValueError('缺口矩形不在画布角落, 请检查 x0/y0/cw/ch 与翻转角的一致性')
    _fill_layers_vertical_horizontal(b, nx0, ny1, layers, offs)
    if flipy:
        b = np.flipud(b)
    if flipx:
        b = np.fliplr(b)
    return b


# ---------------------------------------------------------------------------
# 5. 渲染集成入口
# ---------------------------------------------------------------------------

def _apply_profile_path(*,
                        canvas_arr: np.ndarray,
                        outer_rect: RectShape,
                        cut_corner: str,
                        cut_w_px: float,
                        cut_h_px: float,
                        layers_src: list[tuple[tuple[int, int, int], int]],
                        scale_x: float,
                        scale_y: float,
                        ) -> bool:
    """Profile 路径：源图层结构 → 画布坐标 → patch_lshape_cut_layers。

    与 _apply_v13_path 相同的子图/贴角机制；任何几何异常返回 False，
    由调用方回退到 V13 / 旧路径。
    """
    if not layers_src:
        return False
    # 几何平均（sqrt(sx*sy)）对「adapt_pool_material 旋转校正」稳健：
    # 旋转后 scale_x/scale_y 互换但乘积不变，几何均值恒等于真实缩放
    # （stretch 填满模式）；普通情况 sx≈sy，几何均值 ≈ 算术均值。
    # 修复 2026-09-08：旧 (sx+sy)/2 在 ROTATE_270 素材下误差 100%+
    # （如庄园秘境 226px vs 素材 118px），几何均值偏差 < 1%。
    if scale_x > 0 and scale_y > 0:
        scale_avg = float(np.sqrt(scale_x * scale_y))
    else:
        scale_avg = 1.0
    scale_avg = max(scale_avg, 0.1)

    layers_canvas: list[tuple[tuple[int, int, int], int]] = []
    for color, t in layers_src:
        t_canvas = int(round(float(t) * scale_avg))
        if t_canvas < 1:
            t_canvas = 1
        layers_canvas.append((tuple(int(c) for c in color), t_canvas))

    H, W = canvas_arr.shape[:2]
    ox = max(0, int(round(outer_rect.x)))
    oy = max(0, int(round(outer_rect.y)))
    oright = min(W, int(round(outer_rect.right)))
    obottom = min(H, int(round(outer_rect.bottom)))
    ow_r, oh_r = oright - ox, obottom - oy
    if ow_r < 4 or oh_r < 4:
        logger.info("[LShapeRoute] outer_rect 子图过小，跳过")
        return False

    cw_r = int(round(min(float(cut_w_px), float(ow_r))))
    ch_r = int(round(min(float(cut_h_px), float(oh_r))))
    if cw_r < 1 or ch_r < 1:
        logger.info("[LShapeRoute] 挖角尺寸过小，跳过")
        return False
    if cut_corner == 'tl':
        bx0, by0 = 0, 0
    elif cut_corner == 'tr':
        bx0, by0 = ow_r - cw_r, 0
    elif cut_corner == 'bl':
        bx0, by0 = 0, oh_r - ch_r
    else:  # 'br'
        bx0, by0 = ow_r - cw_r, oh_r - ch_r

    sub = canvas_arr[oy:obottom, ox:oright, :]
    try:
        patched = patch_lshape_cut_layers(
            sub, cut_corner, bx0, by0, cw_r, ch_r, layers_canvas,
        )
    except ValueError as e:
        logger.info("[LShapeRoute] patch 跳过: %s", e)
        return False
    canvas_arr[oy:obottom, ox:oright, :] = patched
    logger.info(
        "[LShapeRoute] 补边完成: %d 层 (scale=%.2f×) %s",
        len(layers_canvas), scale_avg, layers_canvas,
    )
    return True

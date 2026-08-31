# 2026年08月29日 水池设计器 多洞识别 Phase A-E 消包络盒 与 OCR 加权众数投票

## 概述
8.29 T1首版多洞识别落地后，用户立刻实测反馈两大缺陷：
1. **只渲染1洞而非2洞**——两个真实内挖矩形被 OpenCV `findContours` 合并出的**包络凸包（hull）**误判为一个大矩形洞，真实的两个洞反而被当装饰框过滤；
2. **margin_top 误识 5.0 而非 11.5**——嵌套矩形内部识别出的单个 5.0 值（可能是装饰框的线宽/小字）被OCR给了更高confidence，压过了真实外框附近两次出现的 11.5。

共涉及9条子问题。核心修复：**sketch_parser_multihole.py 新增 Phase A-E 五段算法**消去包络盒，**OCR加权众数投票**取代单值max-confidence；配套渲染层、数据流做加法扩展；新增T80/T81/T90/T91四项用例，140+集成测试通过。

---

## 问题一：只渲染1洞而非2洞（09:25）

### 现象
- 输入：并排2个独立矩形内挖框（用户手绘：45×35.5 / 45×35.5，间距46cm，外框98×433）
- 实际输出：GUI仅显示1个大矩形洞（约 136×35.5，完全覆盖两洞+间距）

### 根因分析
OpenCV `findContours + approxPolyDP` 对"近距离并排矩形"会先检出 **外部凸包（convex hull，俗称包络盒）**，其面积为两真实矩形之和+中间空隙，显著大于任何真实单洞，被首版按"面积最大优先"直接命中。后续过滤阶段未区分"真矩形"与"假矩形（包络盒）"。

### 典型特征识别
包络盒 vs 真实矩形的可区分特征：
| 特征 | 真实矩形 | 包络盒（凸包） |
|---|---|---|
| 4角点到最近轮廓像素的平均距离 | 1~3px | 15~80px |
| 轮廓内部是否含≥1个子矩形 | 无或1个（内层装饰框） | 必有≥2个完整的独立矩形 |
| 矩形度（area / boundingRectArea） | ≥0.96 | 0.5~0.9，典型0.7 |

---

## 问题二：Phase A-E 五段消包络盒算法（09:25）

### 算法结构
```
Phase A 候选检测   → 放宽阈值检测所有矩形（含包络盒），输出List[Rect]
Phase B 几何包含去重 → 若 R_a 面积>R_b 且 R_b 95%像素落在 R_a 内 → 标记 R_a 为候选包络
Phase C 嵌套层级判定 → 构建父子树，检测矩形度<0.92且含≥2个子矩形 → 判定为包络盒
Phase D 包络盒抛弃   → 包络盒+其所有父链（若有）全部移出候选列表
Phase E 最终洞输出   → 按面积降序取前MAX_HOLES(8)个，矩形度≥0.96硬条件
```

### Phase B 几何包含判定公式
对任意矩形对 (A, B)：
```
intersection_area = overlap_rect(A, B).area
if intersection_area / B.area >= 0.95  AND  A.area > B.area:
    A.is_envelope_candidate = True
    B.parent = A
```

### Phase C 矩形度阈值
```
rectangularity = contourArea(approx) / (w * h)
if rectangularity < 0.92 and len(children) >= 2:
    mark As Envelope → discard in Phase D
```

### 验证
针对包络盒专门设计的合成草图：**2 并排 100×100 矩形，间距 20**
- 修复前：检出 1 个 (220×100, rect=0.909) → 被当唯一洞 ❌
- 修复后：Phase C 判包络盒，Phase D丢弃，最终出2个(100×100) ✅

---

## 问题三：margin_top 5.0 误识为 11.5（09:25）

### 现象
98×433外框，真实上边距=11.5cm（草图两个位置都标11.5）。程序最终 margin_top=5.0。

### 根因链
1. 嵌套的装饰性小矩形（线宽约5cm）在其内侧被OCR识别出数字"5.0"，位置恰好靠近上边缘；
2. 首版算法对 margin_top 取 `max(confidence)` 单一值，该"5.0"token因清晰度高获得 conf=92；
3. 真实位置两次识别出的 11.5 分别 conf=84 / 86，均低于92，被覆盖；
4. 无"多次出现"这一度量维度。

---

## 问题四：OCR加权众数投票机制（09:25）

### 新策略取代旧的 max-conf
每个字段独立维护 `Counter[vote_value] += confidence`：

```python
# 对每个边距字段（如 margin_top）收集所有候选 token
candidates = [ (11.5, 84), (11.5, 86), (5.0, 92) ]   # (value, confidence)

# 加权计数
weighted = defaultdict(float)
for val, conf in candidates:
    # 量化到0.5步长，避免 11.48 vs 11.52 分裂为两个值
    quantized = round(val * 2) / 2
    weighted[quantized] += conf

# 取加权众数
margin_top = max(weighted.items(), key=lambda x: x[1])[0]
# → weighted = {11.5: 170, 5.0: 92}  →  11.5 胜出 ✅
```

### 0.5cm量化步长
| 策略 | 对 (11.48, 11.52) 的分类结果 |
|---|---|
| 不量化（原值） | 分裂为 2 票 → 易被单一异常值压过 |
| 0.5cm 量化 | 合并为 11.5 → 合计 170 conf，正确胜出 |

### 防兜底
若众数权重占比 < 0.4（即高度分裂），退回几何推导值（outer_h - Σ其他边距 - 内框h）。

---

## 问题五：多洞渲染层配套（09:25）

### geometry.py（加法分支不改单洞）
- 新增 `_build_multi_hole_clip_path(holes)`：依次对每个洞做矩形路径并取差集
- 单洞路径保持原 `_build_single_hole_clip_path` 不动

### image_ops.py（加法实现）
- 新增 `build_multi_hole_mask(holes, pixel_scale)`：逐洞生成矩形mask后 `np.logical_or` 合并
- 新增分支仅在 `len(holes) >= 2` 时命中，否则走原函数

### property_panel_workers.py（数据流）
- PoolWorker.run() 中若检测到 `design.holes is not None and len(holes)>=2`，走多洞渲染流水线
- 保持原有 `if rect_hole: else:` 单洞分支不动

---

## 问题六：T80/T81/T90/T91 新增单元测试（09:25）

| 用例 | 场景 | 期望 |
|---|---|---|
| T80 | 合成双洞（并排 + 包络盒） | Phase D正确抛弃包络，输出两洞尺寸与坐标正确 |
| T81 | 合成三洞（品字 + 双重包络） | 正确抛弃2级包络，输出3洞 |
| T90 | margin_top 双 11.5 + 干扰 5.0 | 加权众数→11.5，非5.0 |
| T91 | 三边距冲突分裂场景（权重占比<0.4） | 正确回退几何推导 |

### 集成回归规模
140+ 集成测试 + 21新单测全部通过，单洞输出byte-identical。

---

## 核心修改文件清单

| 文件 | 修改类型 | 主要改动 |
|---|---|---|
| `core/pool_designer/sketch_parser_multihole.py` | 修改 | 新增 Phase A-E 五段类；重写OCR绑定为加权众数投票（0.5cm量化 + 0.4占比兜底） |
| `core/geometry.py` | 修改（加法） | `_build_multi_hole_clip_path` 新增函数，不碰单洞实现 |
| `core/image_ops.py` | 修改（加法） | `build_multi_hole_mask` 新增函数，逐洞mask取并集 |
| `gui/property_panel_workers.py` | 修改（加法） | PoolWorker新增多洞分支，保持原单洞else不动 |
| `tests/sketch/test_multi_hole_parser.py` | 修改 | 新增T80/T81/T90/T91四项用例 |

---

## 关键决策沉淀
1. **矩形度硬条件（≥0.96）是最后防线**：任何候选洞只要矩形度低于阈值就不输出，防止包络盒、残缺框、弯曲框进入下游。
2. **包络盒用"包含关系+子矩形数量"双判**，仅用面积/矩形度之一都不够稳健。
3. **OCR加权众数投票（0.5cm量化）取代单值max-conf**：真实世界中同一数值通常在草图多处出现，出现频次本身就是强信号；而孤立异常值（如装饰框内的小数字）即使conf高也难赢。
4. **渲染层/数据流永远加法分支**：`if len(holes)>=2` 永远后置判断，`else` 严格等价于8.29前代码。

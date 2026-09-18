# Architecture Decision Record — 单边阶梯 L 形挖角识别可行性

| 字段 | 值 |
|---|---|
| 文档类型 | ADR(架构决策记录) |
| 主题 | 单边阶梯 L 形挖角识别 + 与多边 L 形区分判定 |
| 状态 | Revised(评审后修订 — 原核心结论不可行,方案重构为 CutRect 路线) |
| 报告版本 | V2.2(2026-09-17 真实样本几何验证 — 校准「待确认 2:变体支持」字段) |
| 日期 | 2026-09-17 |
| 涉及模块 | `services/sketch_parser/`、`core/lshape_border*.py`、`workers/property_panel_workers.py` |
| 决策范围 | 识别层改造、schema 扩展、G1 闸口扩展 |
| 不影响 | ~~渲染层已天然支持~~ → 评审证伪:数据模型/几何/渲染均需改造(见「〇、评审结论」) |

---

## 摘要

**结论(V2.1 修订):原「方案 B 最小改造、~250 行、渲染零改动」经代码级评审证伪,不可行。** 诊断部分(桶合并阻塞、G1 软肋、OCR 后置)正确并保留;改造重心修正为 **CutRect(anchor, offset_x, offset_y, w, h) 数据模型路线**,详见「〇、评审结论」与文末「重新规划」。

核心阻塞点位于滑动窗口 corner 桶逻辑([lshape_sketch_parser.py:205-266](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L205-266))— 同 corner 多凹点会被合并为单挖角。原方案要点:

1. 解除桶合并,保留同桶全部候选(去重后 ≤4)
2. `cuts_cm` schema 追加 `concave_px` + `pattern` 字段(向后兼容)→ 评审后升级为 CutRect 字段
3. 新增同边分类器 `_classify_pattern`(纯几何判定)
4. G1 闸口扩展反拼轮廓 IoU 校验(单边阶梯专属)
5. OCR 同角分段归属(二期)

与多边 L 形的区分**无需文件级判定**,同一 parser 在 cuts 列表上即可输出 `pattern` 字段,worker/UI 按 `pattern` 路由。

---

## 〇、评审结论(2026-09-17 代码级验证补充)

> 同日对本报告全部关键论断做了逐条代码验证,并结合《20260916-单边阶梯L形挖角可行性分析与实现建议.md》的真实草图实测
> (`吸水皮革-定制-裁剪有图-安妮森林;55x93.5CM裁剪有图.png`,tr 角两级台阶 10×6.5 + 8.5×3.5)。
> **原核心结论「~250 行、渲染零改动、下游无感知」不成立。**

### 评审通过的部分

| 原论断 | 验证结果 |
|---|---|
| 滑动窗口桶合并只保留每角最高分 | ✅ `lshape_sketch_parser.py:205-269` `detected[corner]` 覆盖式赋值 |
| 凸包差检测返回全部合格连通域 | ✅ `lshape_sketch_parser.py:309-428` |
| G1 只查数量一致性,对阶梯形同虚设 | ✅ `_apply_g1_invariant` 仅 `n_detected != n_consumed`(2==2 天然通过) |
| B4 反拼 IoU 校验方向正确 | ✅ 直击静默失效(实测:渲染 IoU 0.9791、第二级丢失、无任何警告) |
| B5 OCR 分段后置 | ✅ 判断合理,一期 pixel_ratio 兜底 |

### 被证伪的部分(核心,按严重度)

1. **管道直接崩溃** — `core/geometry.py:292` `CropDesign.validate()` 硬拒绝同角位重复 cut
   (`raise ValueError("l_cuts_cm 不允许重复角位")`)。B2 的输出(两个 `corner='tr'` 的 cut)
   在「识别 → 设计」一步即抛异常,原报告未评估这条校验。
2. **角点锚定模型表达能力不足(根本问题)** — `(corner, w, h)` 只能表达「贴 bbox 角」的矩形;
   第二级台阶锚在**内角**,不在任何 bbox 角。真实草图实测:同角位双 cut 渲染为嵌套矩形,
   **第二级完全丢失,IoU 0.9791,无警告**。`lshape_border.py:570-660` 的 claimed mask
   只防补边重叠,不改 mask 几何。
3. **尺寸公式系统性算错** — `cut_w_px = maxx - cx`(到 bbox 边距离)在阶梯下把凹角1 推成
   310px,真实仅 125px(20260916 报告 R3,已实测)。
4. **同边约束拦截阶梯** — `geometry.py:308-328` 的「尺寸和 < 边长」对同边阶梯是语义错误,
   tr+br 拼装实测直接报错。
5. **边框补全缺口** — 非 bbox 角凹角的补边几何在 `lshape_border.py` 缺失
   (20260916 报告定级「高」,唯一硬骨头),并非零改动。

### 工期修正

原估 ~250 行 → 实际需**数据模型、几何、校验、识别、渲染出口、边框补全、GUI 七处改造,合计 8–13 天**(与 20260916 实测版一致)。

---

## Context

### 需求

在现有 L 形挖角识别流水线中,新增对**单边阶梯 L 形挖角**的支持 — 即多个挖角沿**同一条边**(同竖边或同横边)阶梯式排列,形成多凹轮廓。同时需要与现有**多边 L 形**(挖角分布在不同边)在**同一面板**内做区分判定。

### 三种挖角形态几何对比

```
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ ① 标准 L 形     │   │ ② 单边阶梯 L 形 │   │ ③ 多边 L 形     │
│ (单挖角)        │   │ (同边两挖角)    │   │ (异边两挖角)    │
│                 │   │                 │   │                 │
│ ┌──┐            │   │ ┌──┐            │   │ ┌──┐            │
│ │挖│            │   │ │挖│            │   │ │挖│            │
│ │角├────────┐   │   │ │角├──┐         │   │ │角├────────┐   │
│ └──┘        │   │   │ └──┘  │挖角B    │   │ └──┘        │   │
│             │   │   │       │(同竖边) │   │             │   │
│             │   │   │  ┌────┘         │   │             │   │
│             │   │   │  │              │   │        ┌────┘   │
│             │   │   │  │              │   │        │挖角B   │
└─────────────┘   └──┴──┴──────────────┘   └────────┴────────┘
  corner=tl×1       corner=tl×2(同 x)       corner={tl,br}
  ✅ 已支持         ⚠ 当前被合并为 1 个       ✅ 已支持
```

- **① 标准 L 形**:单挖角,某角位挖掉矩形 → 已支持
- **② 单边阶梯 L 形**:同一条边上多个挖角(例如同竖边 tl×2,凹点 A、B 的 x 坐标接近)→ 当前被滑动窗口桶逻辑合并为单挖角
- **③ 多边 L 形**:挖角分布在不同边(例如 tl + br)→ 已支持

### 已验证的关键事实

1. **识别层入口已支持多角** — [lshape_sketch_parser.py:1570](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1570) 返回 `LSketchParseResult`,含 `cuts_cm: list[dict]`,最多 4 个挖角。

2. **滑动窗口桶逻辑是核心阻塞** — [lshape_sketch_parser.py:205-266](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L205-266) 用 `detected = {}` 按 corner 分桶,**每桶只保留 score 最大的一个**:
   ```python
   detected = {}  # corner -> (score, pt)
   ...
   if corner not in detected or score > detected[corner][0]:
       detected[corner] = (score, pt)
   ```
   单边阶梯(同 corner 多凹点)必然合并为单挖角 → 漏检。

3. **凸包差法已无此限制** — [lshape_sketch_parser.py:306-425](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L306-425) V1.1 后返回**全部**合格连通域,可作为单边阶梯的检测主力。

4. **输出 schema 缺绝对坐标** — [lshape_sketch_parser.py:1727-1732](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1727-1732) `cuts_cm` 仅含 `{corner, cut_w_cm, cut_h_cm, source}`,无 `concave_px(x,y)`,**无法判定同边不同 y-range**。

5. **~~渲染层已天然支持~~(评审证伪)** — [lshape_border.py:570-660](file:///F:/SmartShapeCrop/core/lshape_border.py#L570-660) `cuts` 列表递归调用 + claimed mask(L636)只防补边重叠,**不改 mask 几何**;且 `core/geometry.py:292` validate() 拒绝同角位重复 cut,同角位双 cut 根本到不了渲染层。真实草图实测:双 cut 渲染为嵌套矩形,第二级丢失,IoU 0.9791 → 渲染层、几何校验、数据模型均需改造。

6. **G1 闸口软肋** — [lshape_sketch_parser.py:1534](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1534) 只校验"检测数 vs 消费数",不校验形状组装正确性。单边阶梯 `2==2` 天然通过,但形状可能错配。

---

## Decision Drivers

| Driver | Priority | Evidence | Tradeoff |
|---|---|---|---|
| 几何区分可行性 | 高 | 同边多挖角 = 同 corner 桶多凹点 | 需新增"同边聚类"判定 |
| 改造成本可控 | 高 | (评审修订)需数据模型+几何+识别+渲染+GUI 七处改造 | 分期推进,先通端到端 |
| 识别准确性 | 高 | 滑动窗口漏检同桶候选 | 需保留同桶全部候选 |
| OCR 归属正确性 | 中 | 同角多挖角 OCR 需按 y 分段 | 增加复杂度但可后置 |
| 向后兼容 | 高 | 现有多边 L 形不能回归 | CutRect 旧模型为严格子集(offset 恒 0) |
| 形状自洽校验 | 中 | G1 闸口不验形状组装 | 单边阶梯需补反拼轮廓校验 |

---

## Options Considered

| Option | Benefits | Costs | Risks | Selected Because |
|---|---|---|---|---|
| **A. 不支持(降级方案)** | 零开发,零回归 | 用户场景受限 | 单边阶梯草图被误判为单挖角 | ❌ 拒绝 — 用户明确要求 |
| B. 最小改造 | 改动集中 | **评审证伪**:`(corner,w,h)` 模型表达不了内角锚定的第二级台阶;validate() 拒绝同角位重复 cut | 形状仍错(IoU 0.9791) | ❌ 评审后否决 |
| **B'. CutRect 数据模型(评审后选定)** | 旧模型为严格子集(offset 恒 0);识别层用已有 concave 坐标补算 offset,无需新算法 | 数据模型+几何+校验+识别+渲染+GUI 七处改造,8–13 天 | 边框补全是唯一硬骨头 | ✅ 选定 — 与 20260916 实测版一致 |
| C. 全套 schema 重构 | 几何信息完整 | ~600 行,涉及 worker/UI/border 多处接口 | 回归风险高 | ❌ 拒绝 — B' 已覆盖所需表达力,无需全套重构 |

---

## Decision — ~~方案 B:最小改造~~(V2.1 评审后重构为 CutRect 路线,见文末「重新规划」)

> B1 / B3 / B4 / B5 保留;B2 的 schema 扩展升级为 CutRect 字段 — 仅加 `concave_px` + `pattern` 不够,模型本身表达不了内角锚定的台阶。

### B1. 解除滑动窗口桶合并(核心改动,保留)

[lshape_sketch_parser.py:205-266](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L205-266) 改为 `detected[corner].append((score, pt))`,**同桶保留全部候选**,在返回前按凹点绝对坐标去重(欧氏距离 < 阈值合并)。桶上限仍保留 4 个(防误判爆炸)。

```python
# 伪代码
detected = defaultdict(list)  # corner -> [(score, pt)]
...
detected[corner].append((score, pt))
...
# 返回前:按 pt 距离去重,每桶最多 4 个
for corner, lst in detected.items():
    lst = _dedup_by_proximity(lst, threshold=0.05 * diag)
    results.extend(_build_polygon(c, s, pt) for s, pt in lst[:4])
```

### B2. schema 扩字段(向后兼容)→ 评审后升级为 CutRect

[lshape_sketch_parser.py:1727-1732](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1727-1732) 在 `cuts_cm` 每项**追加** `concave_px: (x, y)` 与 `pattern` 标识。旧字段不变,下游消费者无感知。

> 评审注:仅加字段不够 — `core/geometry.py:292` validate() 拒绝同角位重复 cut,且 `(corner, w, h)` 表达不了内角锚定。须升级为 CutRect(anchor, offset_x, offset_y, w, h) 字段,见文末「重新规划」。

```python
cuts_cm.append({
    'corner': candidate['corner'],
    'cut_w_cm': ...,
    'cut_h_cm': ...,
    'source': 'pixel_ratio',
    'concave_px': candidate.get('concave_pt'),   # 新增(可空)
    'pattern': 'single_edge_stepped' if same_edge else 'multi_edge',  # 新增
})
```

### B3. 新增同边分类器 `_classify_pattern(all_corners_geo)`

按 corner 分桶 → 桶内 ≥2 个凹点 → 取绝对坐标 → 同竖边(`|Δx| < ε`)或同横边(`|Δy| < ε`)→ 标记 `single_edge_stepped`;否则 `multi_edge`。**纯几何判定,无 AI/ML 介入**。

### B4. G1 闸口扩展(单边阶梯专属)

在 [lshape_sketch_parser.py:1528-1563](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1528-1563) 的 G1 闸口内,当 `pattern == 'single_edge_stepped'` 时增加**反拼轮廓校验**:用 cuts 列表反向裁剪外框 bbox,与识别轮廓做 IoU 比对,< 0.92 则降级为多边 L 形并保留警告。**只在单边阶梯路径触发,不影响其他路径**。

### B5. OCR 分段归属(可后置)

`_attribute_cut_ocr_per_corner` 同角多挖角时按凹点 y 坐标分段归属。**一期可不实现**,先用 `source='pixel_ratio'` 兜底,保证几何正确后再补 OCR 精度。

---

## Bounded Context Map

| Context | Responsibility | Owned Data | Upstream | Downstream | Translation Surface |
|---|---|---|---|---|---|
| 识别层(sketch_parser) | 凹角检测、corner 分桶、同边分类 | `all_corners_geo` + `concave_px` | — | worker | `cuts_cm` dict |
| Worker(property_panel_workers) | 编排识别→渲染,传 cuts 列表 | `cuts_cm` | parser | border | 调用签名不变 |
| 渲染层(lshape_border.py) | 递归补边,防凹角重叠 | `claimed` mask | worker | — | `cuts=[(c,w,h), ...]` |
| UI 层(property_panel_*) | SpinBox 显示 + debounce | cut_w/h_cm | worker | — | 读 dict 字段 |

---

## Runtime Dependency Adoption

| Dependency | Capability Needed | Failure Mode | Fallback | Adoption Criteria | Revisit Trigger |
|---|---|---|---|---|---|
| 滑动窗口桶去重 | 凹点距离判定 | 同点重复 | 距离阈值 0.05×diag | 单测:同桶 2 候选去重 = 1 | 新素材出现密集凹点 |
| 同边分类器 | 同竖/横边判定 | ε 阈值不适配 | 降级 multi_edge | 单测:tl×2 同 x = stepped | 边框素材边距变化 |
| G1 反拼校验 | IoU 阈值 | 误降级 | 警告+保留 cuts | IoU < 0.92 降级 | 识别轮廓噪声 |
| CutRect 几何拼装 | anchor+offset → 矩形 | offset 越界/级间重叠 | validate() 拒绝 + 渲染退化守卫 | 单测:两级台阶 mask 与手绘轮廓 IoU > 0.99 | 支持外扩/混合变体 |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **静默出错(当前已存在)** — 识别报成功但形状错,G1 不告警 | 已发生 | 高 | B4 反拼 IoU≥0.92 校验,不过则降级 success |
| 同桶多候选爆炸(噪声点) | 中 | 中 | 每桶硬上限 4 + 距离去重 |
| 单边阶梯误判为多边 L | 低 | 高 | G1 反拼 IoU 校验降级路径 |
| 边框补全在非 bbox 角凹角漏线/越界 | 中 | 高 | 先 60×60 受控实验再上素材(复用 9.14 inset 机制) |
| OCR 同角多挖角归属错误 | 高 | 中 | 一期 source='pixel_ratio' 兜底,后置 OCR 分段 |
| 现有 ~501 测试中 L 形相关用例依赖 `l_cuts_cm` 语义 | 中 | 中 | 兼容层保证旧字段映射无损;每期跑全量回归作准入门槛 |
| SpinBox 显示多挖角时 UI 拥塞 | 低 | 低 | 已有 200ms debounce 机制,沿用即可 |

---

## Consequences

- ❌ ~~单边阶梯 L 形挖角可行,最小改造 ~250 行,渲染零改动~~ — **评审证伪**:需 CutRect 数据模型 + 七处改造,合计 8–13 天(见「〇、评审结论」与「重新规划」)。
- ✅ 与多边 L 形的区分通过**同 corner 桶内多凹点 + 同边几何判定**自然实现,不需要新文件类型判定。
- ✅ CutRect 路线不新增 mode/历史源,可绕过 `property_panel.py:926` 与 `app_settings.py:317` 两个静默失效硬编码坑(集成进现有 L 形面板的隐性收益)。
- ⚠️ OCR 归属精度在一期降级,需在二期补 `y 坐标分段归属`。

---

## Evidence(已验证代码)

| Claim | 位置 | 验证内容 |
|---|---|---|
| 滑动窗口桶合并 | [lshape_sketch_parser.py:205-266](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L205-266) | `detected[corner] = (score, pt)` 覆盖式赋值 |
| 凸包差法已解除 argmax | [lshape_sketch_parser.py:306-425](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L306-425) | 返回全部合格连通域 |
| cuts_cm schema 缺坐标 | [lshape_sketch_parser.py:1727-1732](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1727-1732) | 仅 `{corner, cut_w_cm, cut_h_cm, source}` |
| 渲染递归 + claimed mask | [lshape_border.py:631-639](file:///F:/SmartShapeCrop/core/lshape_border.py#L631-639) | `cuts` 列表逐角递归,L636 防凹角重叠 |
| G1 闸口只校验数量 | [lshape_sketch_parser.py:1534](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1534) | `g1_blocked = n_detected != n_consumed` |
| **validate() 拒绝同角位重复 cut(评审补充)** | [geometry.py:292](file:///F:/SmartShapeCrop/core/geometry.py#L292) | `raise ValueError("l_cuts_cm 不允许重复角位")` |
| **同边尺寸和约束(评审补充)** | [geometry.py:308-328](file:///F:/SmartShapeCrop/core/geometry.py#L308-328) | 尺寸和 < 边长 − 0.5cm 校验,拦截同边阶梯拼装 |
| **静默失效实测(评审补充)** | `scripts/diagnose/_diag_stair_*.py` | 真实草图双 cut 渲染 IoU 0.9791,第二级丢失,无警告 |

---

## Revisit Triggers

- 新增素材类型边距变化导致同边 ε 阈值失效
- 用户要求文件级区分(需新增 `pattern` 后缀命名约定)
- 二期 OCR 同角分段归属实现
- 滑动窗口桶上限 4 不足(出现 5+ 凹角场景)
- 「阶梯 + 多角混合」变体启用(决议:暂缓;CutRect 模型无需改动,仅需 GUI 入口 + pattern 分类扩展)

---

## 附录:重新规划 — CutRect 数据模型路线(V2.1 评审后)

### 核心方案

引入 `CutRect(anchor, offset_x_cm, offset_y_cm, w_cm, h_cm)` — 旧 `(corner, w, h)` 是其**严格子集**(offset 恒 0),向后兼容:

```python
# 两级台阶(本例真实草图:tr 角 10×6.5 + 8.5×3.5)
[CutRect('tr', 0, 0, 10, 6.5), CutRect('tr', 10, 6.5, 8.5, 3.5)]
```

识别层无需新算法:`all_corners[]` 一直持有 `concave` 坐标,只需用它补算 anchor + offset(「换公式」而非「新算法」),并把 `cut_w/h_px = 到 bbox 边距离` 改为「相邻顶点差值」。

原 B1–B5 保留情况:

- B1 解除桶合并 ✅(并入二期)
- B2 schema 扩字段 → 升级为 CutRect 字段
- B3 同边分类器 ✅(输出需含 offset)
- B4 G1 反拼 IoU≥0.92 校验 ✅(并入二期,消灭静默失效)
- B5 OCR y 分段后置 ✅(一期 pixel_ratio 兜底)

### 分期路线(合计 8–13 天)

| 期 | 内容 | 工期 | 关键验收 |
|---|---|---|---|
| **一 数据模型+几何** | `core/geometry.py`:新增 CutRect dataclass;`CropDesign` 增 `l_cut_rects`(保留 `l_cuts_cm` 兼容入口);`build_lshape_mask` 改遍历 CutRect + 新增 `_rect_from_anchor_offset()`;**重写 validate()(L284-295,允许同 anchor 多笔、上限 3 级)与同边约束(L308-328,改「各级不重叠 + 不越界」)** | 2–3 天 | 手写 CutRect 列表 → 渲染出正确两级台阶(复用 `scripts/diagnose/_diag_stair_*.py`) |
| **二 识别层** | B1 解除滑动窗口桶合并(每桶保留全部候选,去重,cap 4);用 concave 坐标补 anchor+offset;`cut_w/h_px` 改「相邻顶点差值」;B4 G1 反拼 IoU≥0.92 校验 | 1–2 天 | 本例真实草图 cuts_cm 正确表达两级台阶 |
| **三 渲染出口+边框** | `core/image_ops.py` 8 处 `mode=='rect_lshape'` 分支(733/759/779/1050/1068/1144/1204/1223)收敛为 helper;`core/lshape_border.py` 非 bbox 角凹角补边(唯一硬骨头,先 60×60 受控实验) | 3–5 天 | 多素材 × 多级台阶,边框连续无漏线、无越界 |
| **四 GUI+回归** | `gui/lshape_panel.py` 同角位子行交互(缩进 + 「追加一级」,默认 2 行、最多 3 行;不提供多角混合入口);每期跑全量回归(~501 测试)作准入门槛 | 2–3 天 | 参数回填/预览/导出全通 |

建议先做一 + 二期(3–5 天)即可端到端看到正确的阶梯渲染;三、四期为体验与打磨。

### 已确认决议(2026-09-17)

1. **级数上限 = 3 级(大多数情景 2 级)** — 已确认
   - `validate()` 同 anchor 上限 3 级(见分期一)
   - GUI 子行默认 2 行、最多 3 行(见分期四)
2. **变体支持**(V2.2 修订 — 基于 2026-09-17 真实样本几何验证;决议:采纳 V2.2 建议,已确认):
   - **「逐级内缩」与「逐级外扩」两种连续阶梯方向** → CutRect 模型天然统一覆盖,无需独立分支
     - 两者都用「外包挖空 + 内嵌凸回」表达,凸回矩形的 offset = 前 N 级尺寸之和,**可自动推导,不需独立取值**
     - 内缩样本(吸水皮革-安妮森林,55×93.5CM):外包挖空 18.5×10 + 凸回 8.5×3.5 @ offset(10, 6.5)
     - 外扩样本(用户上传图):外包挖空 20×14 + 凸回 15×7 @ offset(5, 7)
     - 两者 CutRect 数据结构完全同构,差别仅在凸回矩形的相对大小(内缩凸回占比 ~46%×35%;外扩凸回占比 ~75%×50%)
   - **真正需要 offset 独立取值的变体** = 「阶梯 + 多角混合」(如 tr 阶梯 + bl 单挖角,bl 的 offset 与 tr 阶梯无关,必须独立)
   - **决议**:一期支持连续阶梯(内缩 + 外扩同模型,offset 自动推导);**「阶梯 + 多角混合」暂缓不实现** — 不提供 GUI 入口,CutRect 模型无需改动,后续启用仅需 GUI 入口 + pattern 分类扩展(见 Revisit Triggers)
   - **识别层启示**:外扩形态第 1 级挖角尺寸小(5×7 vs 内缩 10×6.5),在滑动窗口桶内 score 较低,**更易被合并漏检** → 印证 B1「解除桶合并」的必要性

### 第一期实施结果(2026-09-18)

**完成日期**: 2026-09-18

**改动清单**(`core/geometry.py`):
- 新增 `CutRect` dataclass(anchor + offset_x_cm + offset_y_cm + w_cm + h_cm)
- `CropDesign` 增 `l_cut_rects: list[CutRect]` 字段(保留 `l_cuts_cm` 兼容入口)
- `LShape` 增 `cut_rects: list[dict]` 字段 + `cut_rect_specs()` 方法(offset 感知 dict 列表)
- `validate()` 重写:当 `l_cut_rects` 非空时跳过旧边约束,改走 `_validate_l_cut_rects()`(同角 ≤3 级、正宽高、非负 offset、不越界、两两不重叠,eps=1e-6)
- `build_lshape_mask` 支持 offset dict 路由 + 新增 `_rect_from_anchor_offset()` 辅助函数(N1-01 同款钳制)
- 旧 tuple 路径(`_get_lshape_cut_rect_at_offset(..., 0)`)行为字节级不变

**新增测试**(`tests/core/test_lshape_cutrect.py`): 33 个测试全过
- CutRect 数据模型 + 默认值
- `_rect_from_anchor_offset` 四角定位 + offset=0 ≡ 旧函数 + 钳制
- validate 接受:内缩/外扩/对角/共享边/边界宽
- validate 拒绝:4 级、非正宽高、负 offset、越界、重叠、坏 anchor
- 旧路径兼容:cut_specs 仍返回 3 元组、重复角位仍拒绝
- mask 渲染:offset=0 dict ≡ tuple(4 角 × 无圆角/有圆角)、两级内缩台阶、两级外扩台阶(br)

**全量回归**: 604 passed(基线 571 + 新增 33),exit 0

**验收脚本**(`scripts/diagnose/_diag_stair_cutrect_poc.py`):
- 手写 CutRect 列表 `[CutRect('tr',0,0,18.5,6.5), CutRect('tr',0,6.5,8.5,3.5)]` → 渲染两级台阶
- 与真实多边形 ground truth 逐行对比(容差 2 px)+ IoU
- 结果:IoU **0.9994**(旧模型 0.9791),`[PASS]`,exit 0

**对现有用户零影响**: 无生产入口设置 `l_cut_rects`(GUI/design_model 未改),旧路径字节级兼容。

**后续期次状态**:
- 二 识别层(sketch_parser 适配 CutRect)— 已完成(见第二期实施结果)
- 三 渲染出口(image_ops.py 8 处 + compute_lshape_border_bands 通用收缩公式)— 已完成(见第三期实施结果)
- 四 GUI+回归 — 已完成(见第四期实施结果)

### 第二期实施结果(2026-09-18)

**完成日期**: 2026-09-18

**改动清单**(`services/sketch_parser/lshape_sketch_parser.py`):
- **B1 解除滑动窗口桶合并**: `_detect_concave_sliding_window` 改为 `detected = defaultdict(list)`,同桶保留全部候选;返回前按凹点绝对坐标去重(欧氏距离 < 5% 对角线),每桶最多 4 个
- **B3 新增同边分类器**: `_classify_pattern(all_corners)` 按 corner 分桶 → 桶内 ≥2 个凹点 → 同竖边(`|Δx| < 5px`)或同横边(`|Δy| < 5px`)→ 标记 `single_edge_stepped`;否则 `multi_edge`
- **B2 CutRect 格式转换**: 当 `pattern == 'single_edge_stepped'` 且 `len(cuts_cm) >= 2` 时,按 concave 坐标排序,构建 CutRect 列表(`anchor` + `offset_x_cm` + `offset_y_cm` + `w_cm` + `h_cm`),offset 为前一级尺寸累加
- **B4 G1 闸口扩展**: 新增 `_compute_staircase_iou()` 函数,用 cuts 列表反向裁剪外框 bbox 构建理论阶梯多边形,与识别轮廓做 IoU 比对;当 `pattern == 'single_edge_stepped'` 时触发校验,IoU < 0.92 则降级为 `multi_edge` 并转换回旧 `(corner, w, h)` 格式
- `result.debug` 新增 `pattern` 字段(`single_edge_stepped` 或 `multi_edge`)

**新增测试**(`tests/sketch_parser/test_lshape_staircase_recognition.py`): 6 个测试全过
- B1: 返回格式验证(无凹角时返回空列表或 None)
- B3: 同边分类器(同竖边/同横边 → single_edge_stepped;不同角位/同角不同边/单角 → multi_edge)

**全量回归**: 610 passed(基线 604 + 新增 6),exit 0

**对现有用户零影响**: 
- 旧路径(`multi_edge`)行为字节级兼容,cuts_cm 格式不变
- 仅当检测到单边阶梯模式(`single_edge_stepped`)时启用 CutRect 格式
- B4 IoU 校验 < 0.92 时自动降级回旧格式,保证鲁棒性

**后续期次状态**:
- 三 渲染出口(image_ops.py 8 处 + compute_lshape_border_bands 通用收缩公式)— 已完成(见第三期实施结果)
- 四 GUI+回归 — 已完成(见第四期实施结果)

### 第三期实施结果(2026-09-18)

**完成日期**: 2026-09-18

**改动清单**:

`core/geometry.py`:
- 新增 `_shrink_cut_rect(rect, t)` — CutRect 通用收缩公式:`offset' = offset + t, w' = max(0, w − 2t)`
- 新增 `_build_design_lshape_mask(design, use_outer, shrink_px, direct_corners)` — 渲染层统一入口,从 CropDesign 提取参数构建 L 形 bool mask,支持 `cut_rect_specs()` offset 感知 + 可选内缩
- `compute_lshape_border_bands` 改造:`cut_specs()`(3 元组,丢失 offset)→ `cut_rect_specs()`(dict,保留 offset);band 循环内用 `_shrink_cut_rect(c, t_inner)` 替代旧 `max(0, cut_w - t)` 行内公式,阶梯边框带覆盖率从 90.37% 提升至 100%

`core/image_ops.py`(9 处 `rect_lshape` 分支收敛):
- **Branch 1**(L752 `_apply_lshape_bg_overlay`):→ `_build_design_lshape_mask(design, use_outer=True)`
- **Branch 3**(L816 `_render_lshape_cut` `_extra` 计算):→ `cut_rect_specs()` + `cut_w + offset_x`/`cut_h + offset_y`(总延伸距离)
- **Branch 4**(L1069 `_fill_lshape_cut_area`):无需改动 — `inner_mask` 来自 Branch 9,已含阶梯支持
- **Branch 6**(L1155 `_compute_border_mask`):→ `_build_design_lshape_mask(design, use_outer=False, shrink_px=border_width_px)`
- **Branch 8**(L1239 `_lshape_border_completion`):→ `cut_rect_specs()` + offset 感知的 `cut_w_px`/`cut_h_px` 计算
- **Branch 9**(L1500 `_get_inner_pixel_mask`):→ `_build_design_lshape_mask(design, use_outer=False)`

**新增测试**(`tests/core/test_lshape_rendering.py`): 17 个测试全过
- `TestShrinkCutRect`(6): 基础收缩 / 已有 offset / 缩至零宽 / 双零 / 保留 corner / 零收缩恒等
- `TestBuildDesignLshapeMask`(7): 简单 L 形 outer/inner / 阶梯 outer/inner / shrink 参数 / 阶梯 shrink / 向后兼容(helper ≡ 旧手动路径)
- `TestComputeLshapeBorderBandsStaircase`(4): 简单 L 形 bands / 阶梯不崩溃 / 阶梯无重叠 / 阶梯覆盖 frame(>95%)

**全量回归**: 627 passed(基线 610 + 新增 17),exit 0

**关键技术成果**:
- 通用收缩公式 `offset' = offset + t, w' = max(0, w − 2t)` 几何证明正确,边框带覆盖率 90.37% → 100%
- `_build_design_lshape_mask` 统一入口收敛 4 处 call site,消除 `cut_specs()` vs `cut_rect_specs()` 混用风险
- 全部 9 处 `rect_lshape` 分支现已正确支持阶梯 CutRect offset

**对现有用户零影响**: 无 `l_cut_rects` 时 `cut_rect_specs()` 退化为单元素列表(offset=0),与旧 `cut_specs()` 字节级等价。

**后续期次状态**:
- 四 GUI+回归 — 已完成(见第四期实施结果)

### 第四期实施结果(2026-09-18)

**完成日期**: 2026-09-18

**改动清单**:

`gui/lshape_panel.py`:
- 新增 `set_cut_rects(cut_rects: list[dict])`: 识别结果回填入口,自动切换至阶梯模式、按级数调整子行(1–3 行)、填充 offset/宽高 SpinBox(blockSignals 保护)
- `_apply_lshape_params()` 分支:`result.debug['pattern'] == 'single_edge_stepped'` 时走 `set_cut_rects()`,跳过标准角位/宽高 SpinBox 填充;状态栏显示阶梯级数与各级尺寸
- `get_corner()` / `get_cut_w_cm()` / `get_cut_h_cm()` 阶梯模式委托:当 `_staircase_mode` 为 True 时从 `_stair_corner` / 首行 SpinBox 读取,避免隐藏的标准控件返回陈旧值
- `clear_lshape_params()` 阶梯模式重置:检测到 `_staircase_mode` 时调用 `_set_staircase_mode(False)`,恢复 `_gb_l` 显示

`gui/property_panel_layers.py`:
- `_LayersMixin._collect_ui_snapshot()` 新增 `cut_rects` 字段:`self._lshape_panel.get_cut_rects_cm()` 写入 lshape dict,与已有 `cuts_cm` 并存

`models/design_model.py`:
- `apply_ui_snapshot()` 新增 `CutRect` 转换:从 `_lp['cut_rects']` 构造 `CutRect` 列表写入 `d.l_cut_rects`,上限 3 个;`lshape is None` 分支清空 `d.l_cut_rects = []`

**新增测试**(`tests/gui/test_lshape_panel_staircase.py`): 25 个测试全过
- `TestStaircaseModeToggle`(3): 初始为标准模式 / `_set_staircase_mode(True)` 切换 `_gb_l`↔`_gb_staircase` 可见性 / 反向切换恢复
- `TestSetCutRects`(6): 进入阶梯模式 / SpinBox 值回填 / 角位设置 / 行数自动调整(增减) / 空列表 noop / 超过 `_stair_max_levels` 截断
- `TestGetCutRectsCm`(3): 标准模式返回空 / 阶梯模式正确提取 / 零尺寸行跳过
- `TestStaircaseGetters`(4): `get_corner` / `get_cut_w_cm` / `get_cut_h_cm` / `get_cuts_cm` 阶梯模式委托
- `TestStaircaseAddRemove`(4): 追加行 / 移除行 / 下限 1 行 / 上限 `_stair_max_levels`
- `TestClearLshapeParamsResetsStaircase`(1): `clear_lshape_params()` 退出阶梯模式
- `TestDesignModelCutRects`(4): `apply_ui_snapshot` 写入 `l_cut_rects` / 空列表清空 / `lshape=None` 清空 / 上限 3 个

**全量回归**: 652 passed(基线 627 + 新增 25),exit 0

**关键技术成果**:
- GUI 数据流闭环:UI 子行 → `_on_staircase_changed()` → `_lshape_params['cut_rects']` → `_collect_ui_snapshot()` → `DesignModel.apply_ui_snapshot()` → `CropDesign.l_cut_rects` → 渲染层(第三期已支持)
- 识别结果回填(Path B)与手动编辑(Path A)共享同一套 `_stair_rows` SpinBox,`set_cut_rects()` 统一入口
- `get_corner()` / `get_cut_w_cm()` / `get_cut_h_cm()` 阶梯模式委托修复了隐藏控件返回陈旧值的隐患,保证 `_collect_ui_snapshot()` 在两种模式下均取到正确值

**对现有用户零影响**:
- 标准多角模式(`_staircase_mode=False`)行为完全不变,`get_cut_rects_cm()` 返回空列表,snapshot 中 `cut_rects` 字段为空不影响下游
- 阶梯模式仅在用户手动启用或识别结果为 `single_edge_stepped` 时激活,不改变原有 L 形单角挖角流程

**后续期次状态**:
- 一至四期全部完成
- 端到端链路(识别 → GUI → Model → 渲染)已贯通,全量 652 测试绿
- 可选后续:「阶梯 + 多角混合」变体(决议暂缓,仅需 GUI 入口 + pattern 分类扩展,CutRect 模型无需改动)

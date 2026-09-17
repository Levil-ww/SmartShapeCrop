# Architecture Decision Record — 单边阶梯 L 形挖角识别可行性

| 字段 | 值 |
|---|---|
| 文档类型 | ADR(架构决策记录) |
| 主题 | 单边阶梯 L 形挖角识别 + 与多边 L 形区分判定 |
| 状态 | Proposed(待评审) |
| 日期 | 2026-09-17 |
| 涉及模块 | `services/sketch_parser/`、`core/lshape_border*.py`、`workers/property_panel_workers.py` |
| 决策范围 | 识别层改造、schema 扩展、G1 闸口扩展 |
| 不影响 | 渲染层(`apply_lshape_border_completion` 已天然支持) |

---

## 摘要

**结论:可行,推荐方案 B(最小改造,~250 行,渲染零改动)。**

核心阻塞点位于滑动窗口 corner 桶逻辑([lshape_sketch_parser.py:205-266](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L205-266))— 同 corner 多凹点会被合并为单挖角。改造方案:

1. 解除桶合并,保留同桶全部候选(去重后 ≤4)
2. `cuts_cm` schema 追加 `concave_px` + `pattern` 字段(向后兼容)
3. 新增同边分类器 `_classify_pattern`(纯几何判定)
4. G1 闸口扩展反拼轮廓 IoU 校验(单边阶梯专属)
5. OCR 同角分段归属(二期)

与多边 L 形的区分**无需文件级判定**,同一 parser 在 cuts 列表上即可输出 `pattern` 字段,worker/UI 按 `pattern` 路由。

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

5. **渲染层已天然支持** — [lshape_border.py:631-639](file:///F:/SmartShapeCrop/core/lshape_border.py#L631-639) `cuts` 列表递归调用,并有 `claimed` mask(L636)防凹角过渡区重叠 → 渲染无需改动。

6. **G1 闸口软肋** — [lshape_sketch_parser.py:1534](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1534) 只校验"检测数 vs 消费数",不校验形状组装正确性。单边阶梯 `2==2` 天然通过,但形状可能错配。

---

## Decision Drivers

| Driver | Priority | Evidence | Tradeoff |
|---|---|---|---|
| 几何区分可行性 | 高 | 同边多挖角 = 同 corner 桶多凹点 | 需新增"同边聚类"判定 |
| 最小改造成本 | 高 | 渲染层零改动,只改识别+schema | 改动应控制在 ~250 行 |
| 识别准确性 | 高 | 滑动窗口漏检同桶候选 | 需保留同桶全部候选 |
| OCR 归属正确性 | 中 | 同角多挖角 OCR 需按 y 分段 | 增加复杂度但可后置 |
| 向后兼容 | 高 | 现有多边 L 形不能回归 | 新分类步骤必须纯增量 |
| 形状自洽校验 | 中 | G1 闸口不验形状组装 | 单边阶梯需补反拼轮廓校验 |

---

## Options Considered

| Option | Benefits | Costs | Risks | Selected Because |
|---|---|---|---|---|
| **A. 不支持(降级方案)** | 零开发,零回归 | 用户场景受限 | 单边阶梯草图被误判为单挖角 | ❌ 拒绝 — 用户明确要求 |
| **B. 最小改造(推荐)** | ~250 行,渲染不动,向后兼容,降级路径清晰 | 新增分类步骤 + schema 扩字段 | OCR 归属需按 y 分段(可后置) | ✅ 选定 — 改动集中、可验证 |
| C. 全套 schema 重构 | 几何信息完整 | ~600 行,涉及 worker/UI/border 多处接口 | 回归风险高 | ❌ 拒绝 — 收益/成本比差 |

---

## Decision — 方案 B:最小改造

### B1. 解除滑动窗口桶合并(核心改动)

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

### B2. schema 扩字段(向后兼容)

[lshape_sketch_parser.py:1727-1732](file:///F:/SmartShapeCrop/services/sketch_parser/lshape_sketch_parser.py#L1727-1732) 在 `cuts_cm` 每项**追加** `concave_px: (x, y)` 与 `pattern` 标识。旧字段不变,下游消费者无感知。

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

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 同桶多候选爆炸(噪声点) | 中 | 中 | 每桶硬上限 4 + 距离去重 |
| 单边阶梯误判为多边 L | 低 | 高 | G1 反拼 IoU 校验降级路径 |
| 渲染层 claimed mask 漏判凹角过渡区 | 中 | 中 | 已有 L636 防重叠,回归测试覆盖 |
| OCR 同角多挖角归属错误 | 高 | 中 | 一期 source='pixel_ratio' 兜底,后置 OCR 分段 |
| SpinBox 显示多挖角时 UI 拥塞 | 低 | 低 | 已有 200ms debounce 机制,沿用即可 |

---

## Consequences

- ✅ 单边阶梯 L 形挖角**可行**,最小改造 ~250 行,渲染零改动。
- ✅ 与多边 L 形的区分通过**同 corner 桶内多凹点 + 同边几何判定**自然实现,不需要新文件类型判定。
- ⚠️ "同一面板内如何区分两个文件进行判断" — **无需区分文件**,同一 parser 在 cuts 列表上即可输出 `pattern` 字段,worker/UI 按 `pattern` 路由即可。若用户坚持文件级区分,可在 `target_name_override` 加 `pattern` 后缀(可选)。
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

---

## Revisit Triggers

- 新增素材类型边距变化导致同边 ε 阈值失效
- 用户要求文件级区分(需新增 `pattern` 后缀命名约定)
- 二期 OCR 同角分段归属实现
- 滑动窗口桶上限 4 不足(出现 5+ 凹角场景)

---

## 附录:实施步骤建议(一期)

按依赖顺序执行,每步可独立验证:

1. **识别层 B1**:修改 `_detect_concave_sliding_window` 桶逻辑,加去重 + 桶上限 4
2. **识别层 B3**:新增 `_classify_pattern(all_corners_geo)` 纯几何分类器
3. **schema B2**:`cuts_cm` 追加 `concave_px` + `pattern` 字段
4. **G1 扩展 B4**:`single_edge_stepped` 路径触发反拼 IoU 校验
5. **回归测试**:沿用现有 500 测试集 + 新增 4 个单边阶梯 fixture(tl×2 / tr×2 / bl×2 / br×2)
6. **二期 B5(可后置)**:`_attribute_cut_ocr_per_corner` 同角 y 分段

预期改动量:识别层 ~180 行,测试 ~70 行,渲染层 0 行,UI 层 0 行。

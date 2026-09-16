# 综合形状（中间挖洞 + L 形挖角）实现方案分析

**版本** V1.0 · **日期** 2026-09-15 · **分析对象** SmartShapeCrop V2.2.1（`F:\SmartShapeCrop`，基线 commit `300c3dc`）

**目标草图** `E:\智能裁剪设计器\测试草图文件-综合中间+L形\吸水皮革-定制-裁剪有图-白色大理石8;88x185CM裁剪有图.png`

| 项 | 值 |
|---|---|
| 对应既有规划 | **F-1「多 L 形挖角 + 中间洞复合支持」（P2，原估 3–4 周）** |
| 对应既有软肋 | **SR-3「单 L 形无法复合（多 corner + 中间洞）」** |
| 本报告定位 | F-1 的**第 0 期**：把 F-1 从"3–4 周"细化到逐文件行号，并给出开工前可复现的可行性证据 |

---

## 0. 结论速览

### 0.1 把决策拆成两层，避免把可延后的选择提前锁死

```
┌─ 第一层：引擎（无选择余地，必须做） ───────────────────────────┐
│  新增 mode = 'rect_lshape_hole'                                │
│  几何组合：KEEP = 矩形 − Σ挖角 − 洞                            │
│  把 19 处 `mode == / != 'rect_lshape'` 硬判断按四类分治         │
└────────────────────────────────────────────────────────────────┘
                              ↓  引擎之上再选外壳
┌─ 第二层：外壳（可延后决策，切换成本极低） ─────────────────────┐
│  ① 新开第 4 个 Tab 面板（继承 LShapePanel，不复制脚手架）       │
│  ② 在「L形挖角设计」面板内加一个「同时中间挖洞」勾选分组        │
│  —— 两者共用同一套引擎，先做 ② 上线，按实际频次再升级为 ①      │
└────────────────────────────────────────────────────────────────┘
```

### 0.2 直接回答提问

| 提问 | 回答 |
|---|---|
| 新建一个独立项目软件？ | ❌ **否决**。图形引擎、边框补全三级路由、OCR 解析、素材匹配、Tesseract 内嵌打包全是重资产，新建 = 复制约 1.5 万行并永久双份维护 |
| 在此基础上单开一个面板？ | ✅ **推荐，但请注意：单开面板只是"外壳"选择，真正必须做的是新增一个 mode**。面板本身不解决任何难点 |
| 为什么必须先新增 mode | `mode` 是互斥枚举，渲染入口是 `if / elif / else` 三分支（`image_ops.py:1496/1502/1510`），**不可能**靠"同时把 mode 设成两个值"实现 |

### 0.3 KPI

| 指标 | 值 | 备注 |
|---|---|---|
| 实现复杂度 | **中等** | 几何层易（已用 POC 实测证实，见 §7）；难点在"洞与 L 形边框补全共存" |
| 预计工作量 | **8–14 个工作日** | 优于 F-1 原估 3–4 周（多角基础设施已落地），但远高于拍脑袋的"3 天" |
| 引擎改造 | **必修 1 项** | 新 mode + 19 处 mode 判断四类分治 |
| 最大不确定项 | **草图标注语义扩展** | 现有 A–F 六值标签 → 复合草图 10 个数值（对齐 V1.1 §5 的识别层工期） |

---

## 1. 需求拆解

### 1.1 草图真值读数

| 标注 | 值 | 语义 |
|---|---|---|
| 185 / 88 | 185 × 88 cm | 外框宽 / 高 |
| 35 / 10（右上） | 35 × 10 cm | 右上角挖角 宽 / 高 |
| 80 / 60 | 80 × 60 cm | 中间洞 宽 / 高 |
| 左45 / 右60 | 45 / 60 cm | 洞的左 / 右边距 |
| 上10 / 下18 | 10 / 18 cm | 洞的上 / 下边距 |

### 1.2 数值自洽验算

```
横向：45 + 80 + 60 = 185 ✓   （等于外框宽）
纵向：10 + 60 + 18 =  88 ✓   （等于外框高）
```

> **这是个好消息**：复合形状比纯 L 形多出 2 个**强等式约束**（V1.1 §2.1 的纯 L 形案例只有尺寸链求和，没有内框自洽约束）。
> 10 个 OCR 数值之间有 2 个等式可以互相校验，误识更容易被检出并回退到几何值。**这直接降低了第三阶段的风险。**

### 1.3 形状公式

```
KEEP = Rect(185×88) − Cut(tr, 35×10) − Hole(80×60)
```

| 区域 | x 范围 | y 范围 |
|---|---|---|
| 外框 | 0 – 185 | 0 – 88 |
| L 挖角 | 150 – 185 | 0 – 10 |
| 中间洞 | 45 – 125 | 10 – 70 |

**两个直接影响实现的几何事实**

1. **洞与挖角不相交、也不相邻**——挖角下沿 `y=10` 与洞上沿 `y=10` 恰好共线，但 x 区间相隔 25 cm 实心带。
   §7 的 POC 已实测：重叠 0 像素，**1px 膨胀后相邻像素仍为 0**。
   → 10px 黑框必须**分形状各自绘制后 OR 合并**，**不能**用"整体 union 再内缩"（联合内缩会在两区域边界产生错误缺口）。
2. **挖角整体落在右边距环带内**（挖角 x 起点 150 > 洞右边界 125）。
   → 挖角只从"外框环带"上削掉一块，不侵入洞附近材料，几何耦合度低。

### 1.4 与现有三种模式的关系

| 现有模式 | 几何操作 | 与综合形状的关系 |
|---|---|---|
| `rect_hole` | 矩形 − 中间洞 | 提供 **洞** 这一半 |
| `rect_lshape` | 矩形 − Σ角部矩形（≤4） | 提供 **挖角** 这一半 |
| `ellipse_hole` | 矩形 − 椭圆 | 无关 |
| **`rect_lshape_hole`（新增）** | 矩形 − Σ角部矩形 − 中间洞 | = 前两者的**组合** |

---

## 2. 代码现状盘点

### 2.1 已经具备、可直接复用的资产

| 能力 | 位置 | 复合模式能直接用的部分 |
|---|---|---|
| 多角 L 形 mask | `core/geometry.py:717 build_lshape_mask(..., cuts=[...])` | **原生支持 1–4 角**，签名已含 `cuts` |
| 多角参数模型 | `core/geometry.py:150 l_cuts_cm` + `:394 l_shapes_px()` | 数据结构现成，最多 4 角、禁重复角位 |
| 洞 mask | `core/image_ops.py:1496` + `fill_rect_mask` | 单洞 / 多洞 UNION 两套逻辑都在 |
| 洞的逐洞 10px 黑框 | `core/image_ops.py:1089-1127` | **"full & ~shrunk" 环带算法现成**，可直接复用到洞 |
| L 形切边 10px 黑框 | `core/image_ops.py:1144-1162` | 逐 cut 的"L 形 & ~收缩 L 形"算法现成 |
| L 形素材边框补全（逐角调度） | `../../core/lshape_border.py` + `lshape_border_route.py` | **V1.1 第二期已完成"逐角调度 + 相邻角冲突策略"**，这是 F-1 最大的前置债务，已还清 |
| L 形草图几何识别 | `../../services/sketch_parser/lshape_sketch_parser.py` | 用 `RETR_EXTERNAL` 取**最大外轮廓**做凹角检测 → **中间多一个洞不影响外轮廓提取** |
| 4 行挖角 UI + 实时边余量提示 | `gui/lshape_panel.py:236-259` | 现成 |
| G1/G2/G3 闸口 | 识别层 + `../../gui/lshape_panel.py` + `canvas_widget.py` | 现成的"识别不完整必须报警"防线，复合模式可直接继承 |
| 手动修正通路 | `_params_source` + 就地编辑 | 现成（V1.1 §7 判定为零风险通路） |
| 后台线程 / 打包内嵌 Tesseract | `../../workers`、`../../packaging/packageV2.2.py` | 零改动 |

**结论：约 85% 的管线可复用。真正要新写的只有"把两块 mask 组合起来 + 让草图多认几个数"。**

### 2.2 阻塞点：`mode` 是互斥枚举

```
core/geometry.py:133   mode: Literal['rect_hole', 'rect_lshape', 'ellipse_hole']
core/geometry.py:243   _VALID_MODES = frozenset({'rect_hole', 'rect_lshape', 'ellipse_hole'})

core/image_ops.py:1496   if   design.mode == 'rect_hole':     ... return
core/image_ops.py:1502   elif design.mode == 'rect_lshape':   ... return
core/image_ops.py:1510   else:  # ellipse_hole               ... return
```

### 2.3 硬判断分布（19 处），必须四类分治

`mode == / != 'rect_lshape'` 全工程共 **19 处**（逐行实测）：

| 文件 | 处数 | 行号 |
|---|---|---|
| `../../core/image_ops.py` | 9 | 733, 759, 779, 1050, 1068, 1144, 1204, 1223, 1502 |
| `../../gui/property_panel_generate.py` | 6 | 204, 220, 270, 327, 479, 522 |
| `../../core/geometry.py` | 2 | 277, 584 |
| `../../models/design_model.py` | 2 | 114, 166 |

字面量总量：`'rect_lshape'` 28 次、`'rect_hole'` 14 次、`'ellipse_hole'` 9 次。

> **⚠️ 注意**：其中 3 处是 `!=`（`design_model.py:114`、`property_panel_generate.py:220`、`:327`）。
> 只搜 `== 'rect_lshape'` 会**漏掉这 3 处**——这正是本项目历史上"改了这个坏那个"的典型成因。

**这 19 处绝不能无脑全局替换，必须四类分治：**

**类 ① 渲染语义 —— 改为 `mode in LSHAPE_LAYOUT_MODES`（8 处，全在 `image_ops.py`）**

| 行号 | 函数 | 作用 |
|---|---|---|
| 733 | `_apply_lshape_bg_overlay` | 非 L 形区域填 `outer_bg_color` |
| 759 | `_render_band_layers` | L 形 + 外背景图时跳过边框带着色 |
| 779 | `_render_lshape_cut` | 挖角区填白 + 素材底色采样 |
| 1050 | `_fill_lshape_cut_area` | 挖角区填 `hole_bg_color` |
| 1068 | 同上（`has_outer_img` 分支） | cut 区保持外背景色 |
| 1144 | `_compute_border_mask` | L 形切边 10px 黑框（收缩 L 形差集） |
| 1204 | `_apply_unified_black_border` | L 形 + 池素材时跳过统一黑框 |
| 1223 | `_lshape_border_completion` | **触发素材边框补全**（漏掉这处 = 切边没有边框） |

**类 ② 分派点 —— 必须新增独立分支，不能改成 membership 判断（2 处）**

| 位置 | 说明 |
|---|---|
| `core/image_ops.py:1502` | `_get_inner_pixel_mask()` 的 `elif`——复合模式不是"也是 L 形"，而是"L 形 **减去** 洞"，需要独立的 mask 组合逻辑 |
| `core/geometry.py:584` | `compute_border_bands()` 的 L 形路由——复合模式的边框带 = L 形带 ∪ 洞环带 |

> 若把这两处误改成 `in LSHAPE_LAYOUT_MODES`，会得到"L 形 + 洞"的**并集**（洞被填满）而不是**差集**，且不会报错。

**类 ③ 展示与 UI 同步 —— 需扩展（4 处）**

| 位置 | 作用 | 处理 |
|---|---|---|
| `property_panel_generate.py:204` | L 形从 `LShapePanel` 推算画布尺寸 | 扩展（复合模式同样需要） |
| `property_panel_generate.py:270` | 回填 `LShapePanel`（corner/cut/外框） | 扩展 |
| `property_panel_generate.py:479` | 成功对话框文案（L 形参数行） | 扩展（追加洞信息行） |
| `property_panel_generate.py:522` | 成功对话框文案（识别结果行） | 扩展 |

> **这 4 处最容易被误判为「类 ④」而漏改。** 它们长得像参数守卫，实际只是展示/同步。
> 尤其 `:204` ——看起来像"L 形独占"，但复合模式**同样需要**从面板推算画布尺寸。

**类 ④ 参数语义守卫 —— 不能扩展，需重写逻辑（5 处）**

| 位置 | 现有逻辑 | 为何不能扩展 / 要改成什么 |
|---|---|---|
| `models/design_model.py:114` | `if d.mode != 'rect_lshape'` 才写 inner_margin | L 形语义下边距无意义被固定为 0；**复合模式下边距恰恰是洞的定义** → 改为"仅 `rect_lshape` 跳过" |
| `models/design_model.py:166` | `if d.mode == 'rect_lshape': pool_hole_transparent = True` | 复合模式需按"洞是否填素材"决定 → 复合模式走洞的分支逻辑 |
| `gui/property_panel_generate.py:220` | `if mode != 'rect_lshape'` 才写 inner_margin | 同 `design_model.py:114` |
| `gui/property_panel_generate.py:327` | `if hm == "image" and mode != 'rect_lshape'` 才启动内挖素材匹配 | 复合模式的洞**可能也要填素材** → 需放开（对应 §9.4 的 Q2） |
| `core/geometry.py:277` | `validate()` 的 L 形专项校验 | 需新增复合分支；**挖角约束的基准矩形要从 `inner_rect` 改为 `outer_rect`** |

> **执行建议**：为这 19 处逐条加注释 `# [COMPOSITE] 类①/②/③/④`，再逐条改写。
> 不要做字符串级批量替换——本项目的 `mode == 'rect_lshape'` 在四类里的正确动作各不相同。

### 2.4 隐藏耦合（最容易踩的坑）

| 位置 | 问题 | 后果 |
|---|---|---|
| `gui/property_panel.py:926` | **硬编码索引映射** `{'rect_hole': 0, 'rect_lshape': 1, 'ellipse_hole': 2}.get(d.mode, 0)` | 不同步 → 模板加载**静默回落 `rect_hole`**，形状错误且不报错 |
| `gui/property_panel.py:135-137` | `_cb_mode` 只有 3 个 `addItem` | 需追加第 4 项 |
| `gui/property_panel.py:559` | `findData('rect_lshape')` 自动切模式路径 | 复合模式需要自己的入口 |
| `core/app_settings.py:317` | `if src not in (CROPPER, POOL, LSHAPE): src = CROPPER` —— **未知 source 静默回落到 cropper** | 若新增第 4 个历史源却忘了同步白名单，历史记录会**静默写进圆角裁剪工具的历史** |
| `core/geometry.py:277-324` | `validate()` 逐边约束基于 `inner_rect` | 复合模式下基准矩形应为 `outer_rect` |
| `main.py:151-153 / 451-453 / 518-519` | 4 个 Tab 装配点 + `closeEvent` 与 `aboutToQuit` **两处**线程退役 | 漏接 → 关窗时 `QThread: Destroyed while thread is still running` 崩溃（N-P0-02 的历史教训） |

### 2.5 与既有文档 / 规划的关系

| 既有文档 | 原有结论 | 与本报告的关系 |
|---|---|---|
| `L形挖角已知问题与后续规划.md` **规划 F-1**（P2，3–4 周） | 「多 L 形挖角 + 中间洞复合支持」，设计方向 = 数据结构列表化 + 布尔 `Σ CutRect` 差集 | **本报告 = F-1 的第 0 期**：把 3–4 周细化到逐文件行号，并给出开工前可复现的 POC 证据；§7 证明布尔差集方向正确 |
| 同上 **SR-3** | 「单 L 形无法复合（多 corner + 中间洞）」，化解方式指向 F-1 | 本报告即 SR-3 的正式化解方案 |
| `SmartShapeCrop-多角L形挖角可行性分析 V1.1` §8 | **建议扩展现有 L 面板，不要单开面板** | 该结论针对 **"多角 vs 单角"（同一参数族，属参数扩展）**；本问题针对 **"L + 洞"（新增几何实体）**，前提不同，故面板结论需重新论证 —— 见 §8 逐条对照 |
| `SmartShapeCrop-多洞面板拆分可行性分析 V1.0` | 点名「寄生 + 多链路穿透读写」是最大难度来源 | 本报告选择**继承 `LShapePanel`**（不复制脚手架），且**不把洞参数寄生到水池面板** |

> **本文档与 V1.1 §8 的结论并不矛盾，而是互补**：V1.1 回答"多角要不要新面板"（答案：不要），本报告回答"复合要不要新面板"（答案：可以有，但只是外壳选择）。

---

## 3. 方案对比

| 维度 | A. 新建独立项目 | **B. 现有项目 + 新增第 4 个 Tab 面板（继承 LShapePanel）** ⭐ | **C. 在 L 面板内加「同时中间挖洞」分组** ✅ 推荐先行 | D. 在水池设计器内加 L 形 |
|---|---|---|---|---|
| 复用率 | 0%（或复制粘贴） | ~85% | ~85% | ~85% |
| 必须重写 | UI + OCR 解析 + 三级边框路由 + 素材匹配 + Tesseract 内嵌打包 + 全部测试 | 仅几何组合与草图标签扩展 | 同 B | 同 B |
| 代码维护 | **永久双份**（边框路由已修 5 次以上，改一处要同步两处） | 单份 | 单份 | 单份 |
| 对现网回归风险 | 无 | **低**（旧 mode 分支零改动、L 面板零改动） | **最低**（外壳改动仅 ~1 天，引擎改动与 B 完全相同） | **高**（水池面板 991+1043 行，职责已过载） |
| 新增历史源 | 需全套 | 需第 4 源（**注意 `app_settings.py:317` 静默回落陷阱**） | **不需要**（沿用 `TARGET_SRC_LSHAPE`） | 不需要 |
| 新增 Tab / 线程退役点 | — | 需（**两处**） | **不需要** | 不需要 |
| UI 清晰度 | 最好 | 好 | 一般（L 面板 1036 行再加 7 个洞控件会偏挤） | 差 |
| 复合草图 10 标注的语义契合度 | — | **好**（独立入口，G1/G2/G3 闸口不必双模态化） | 一般（识别分支要分叉） | 差 |
| 结论 | ❌ 否决 | ✅ 推荐（**演进目标**） | ✅ **推荐先行** | ❌ 否决 |

### 3.1 为什么不选 A（新建项目）

1. **图形引擎 100% 重复**：`../../core/geometry.py`(873) + `../../core/image_ops.py`(1785) + `../../core/corner` + `../../core/lshape_border.py`(1255) + `../../core/lshape_border_route.py`(648) 全是纯函数，没有哪个"新项目"能绕开。
2. **OCR 与打包是重资产**：Tesseract 内嵌、`PathResolver` 跨平台探测、五层架构、PyInstaller hidden imports 清单，重做一遍纯浪费。
3. **双份维护的代价已经付过一次**：L 形边框补全的三级路由（Profile / V13 / 旧路径）是被真实案例反复打磨出来的，任何副本都会立刻开始漂移。
4. **业务上这不是新产品**：客户要的还是"裁剪有图的成品图"，只是形状更复杂。新的 exe 会让使用者操作路径分裂。

### 3.2 为什么 B 与 C 可以先后走

**引擎完全相同**——两者共用同一个 `mode`、同一套几何组合、同一套渲染分支。差异只在"参数从哪个面板读"。

| | C（L 面板内扩展） | B（新 Tab，继承 LShapePanel） |
|---|---|---|
| 外壳改动量 | ~1 天（加 1 个 GroupBox + 覆写取值） | ~1–2 天（子类 + 接线 + 线程退役） |
| 需改 `LShapePanel`？ | 是（但用"默认关闭的勾选"可保证零行为变化） | **否**（继承，L 面板零改动） |
| 历史源 | 沿用 `TARGET_SRC_LSHAPE` | 新增第 4 源（须同步 `app_settings` 白名单）或复用 lshape 源 |
| 升级成本 | 0（就是 B 的前置状态） | — |

> **建议路径**：先按 **C** 落地（引擎 + 最小外壳，约 1 周内可用）→ 上线后按实际订单频次决定是否升级为 **B**。
> 因为外壳切换不动引擎，这个决定**可以推迟**，不必现在拍板。这符合"先拿可用版本、再按数据投入"的节奏。

### 3.3 明确否决 D

水池面板已 991 + 1043 行，且《多洞面板拆分可行性分析 V1.0》已把"洞参数寄生在水池面板、被多条链路穿透读写"列为**最大难度来源**。再把 L 形塞进去等于制造第三个穿透点。

---

## 4. 推荐落地路线（6 阶段）

> **贯穿全程的硬约束**：每一阶段结束时，现有 3 个模式行为必须 100% 不变（回归基线见 §9.2）。

### 阶段 1 —— 几何层（`../../core/geometry.py`）· 0.5–1 天
- 新增 `'rect_lshape_hole'` 到 `Literal` 与 `_VALID_MODES`
- 新增 `build_composite_mask(...)`：`L 形(outer_rect, cuts) − 洞(inner_rect, 带圆角)`
- `validate()` 新增复合分支（挖角约束按 **`outer_rect`** 计算；洞边距沿用现有逻辑）
- `compute_border_bands()` 新增复合路由
- **验收**：§7 的 4 项集合代数恒等式全 PASS + `validate()` 正/反例

### 阶段 2 —— 渲染管线（`../../core/image_ops.py`）· 3–5 天
- `_get_inner_pixel_mask()` 新增复合分支：`inner_mask = hole_mask | cut_mask`
- 类 ① 的 **8 处**渲染语义硬判断改为 `design.mode in LSHAPE_LAYOUT_MODES`
- 类 ② 的 **2 处分派点**（`:1502`、`geometry.py:584`）新增独立复合分支
- `_compute_border_mask()` 复合分支：`border = L形切边环带 ∪ 洞环带`（两个现成算法 OR 合并）
- `_render_lshape_cut()` / `_fill_lshape_cut_area()` 复合适配（**详见 §6.2 的 R4/R5**）
- `_lshape_border_completion()` 触发条件纳入复合模式
- **验收**：渲染 POC 与 §7 一致；3 个旧模式黄金样本**像素级**比对无差异

### 阶段 3 —— 草图识别（`../../services/sketch_parser`）· 3–5 天
- 新增 `composite_sketch_parser.py`
  - **可复用**：`_extract_largest_contour`（`RETR_EXTERNAL` → 中间有洞不影响）、`_detect_concave_sliding_window`、`_detect_by_convex_hull`、`_finalize_lshape_geometry`
  - **需新增**：洞的内轮廓提取（`RETR_CCOMP` / `RETR_TREE`）
  - **需新增**：10 标注角色映射（现 `_assign_labels_by_geometry` 只认 A–F 六值，`lshape_sketch_parser.py:1023-1036`）
  - **需新增**：复合自洽校验 `ml + hole_w + mr == outer_w`、`mt + hole_h + mb == outer_h`（§1.2 的两个等式，天然的 OCR 纠错闸口）
- 外框真值始终信任目标文件名（沿用 `parse_lshape_sketch` 既有不变量）
- **验收**：对 `测试草图文件-综合中间+L形` 与 `测试草图文件-L形+中间挖洞` 两个真实案例，10 个数值误差 ≤ 0.5 cm；OCR 不可用时几何降级不崩溃

### 阶段 4 —— 外壳（`../../gui`）· 1–2 天
- **C 路线**：`../../gui/lshape_panel.py` 追加「同时中间挖洞」GroupBox（勾选 + 洞宽高 + 四边距 + 挖空方式），`get_lshape_params()` 附加 hole 字段
- **B 路线**：`gui/composite_panel.py` **继承 `LShapePanel()`**，覆写 `get_lshape_params()`；`../../main.py` 三处装配 + `closeEvent`/`aboutToQuit` 两处线程退役
- `../../gui/property_panel.py`：`_cb_mode` 追加第 4 项；**`:926` 硬编码映射改为 `_cb_mode.findData(mode)`**
- `../../models/design_model.py`：新增复合分支（**关键**：inner_margin 必须允许写入，不能被 `mode != 'rect_lshape'` 的值守挡住）
- **验收**：离屏 `QT_QPA_PLATFORM=offscreen` 面板构造冒烟 + 信号契约测试

### 阶段 5 —— 回归与打包 · 1–2 天
- 新增 `tests/core/test_composite_shape.py`（几何 + 校验）、`tests/integration/test_composite_flow.py`（端到端）
- 全量测试回归 + 旧 3 模式像素级比对
- `../../packaging/packageV2.2.py` 与 spec 补 hidden import；**核对 `dist/*.exe` 时间戳 ≥ 最新源码时间戳**

### 阶段 6 —— 文档同步 · 0.5 天
- `../../README.md`：架构概览 / 目录结构 / 核心模块说明 / 快速开始（测试数量）
- `L形挖角已知问题与后续规划.md`：F-1 标记进度、SR-3 标记化解
- `` 归档（分类约定见附录 C）

---

## 5. 关键实现要点（签名级）

### 5.1 几何层

```python
# core/geometry.py
COMPOSITE_MODE = 'rect_lshape_hole'
LSHAPE_LAYOUT_MODES = frozenset({'rect_lshape', COMPOSITE_MODE})   # 类① 判断统一入口

def build_composite_mask(
    size: tuple[int, int],
    outer_rect: RectShape,                    # L 形板 = 整幅画布（不是 inner_rect！）
    cuts: list[tuple[str, float, float]],
    hole_rect: RectShape,
    hole_corners: dict[str, float],
    outer_corners: dict[str, float],
) -> np.ndarray:
    """KEEP = L形(outer_rect, cuts) − 洞(hole_rect, 带圆角)，返回 bool mask。"""
```

> **⚠️ 最容易写错的一点**：现有 `rect_lshape` 分支把 `inner_rect` 当作 L 形的外轮廓（因为该模式下边距被强制为 0，两者相等）。
> 复合模式下 **L 形外轮廓必须是 `outer_rect`**，`inner_rect` 改为表示洞。**两处基准矩形互换**是本次改造最易出错的地方。

### 5.2 渲染管线

```python
# core/image_ops.py  _get_inner_pixel_mask()
elif design.mode == 'rect_lshape_hole':
    cut_mask  = ~build_lshape_mask(size, outer_rect, cuts, ...)   # 挖掉的角
    hole_mask = fill_rect_mask(inner_rect, inner_corners)         # 洞
    return cut_mask | hole_mask          # 「被移除区域」= 挖角 ∪ 洞
```

```python
# core/image_ops.py  _compute_border_mask()  复合分支
# 两个现成算法直接 OR；不做联合内缩（§1.3 已证实两区域不相交）
border = lshape_edge_band(outer_rect, cuts, bw)   # 复用 :1144-1162 的算法
       | hole_ring(inner_rect, bw)                # 复用 :1089-1127 的算法
```

### 5.3 `mode` 判断收敛（防漏改）

```python
# core/geometry.py
def is_lshape_layout(mode: str) -> bool:
    """L 形渲染语义（类①）——8 处渲染判断统一走这里。"""
    return mode in LSHAPE_LAYOUT_MODES
```

> 现状是 19 处裸字符串比较（其中 3 处是 `!=`，只搜 `==` 会漏），漏改一处 = 一个难以定位的视觉 Bug（例如"切边没画 10px 黑框"）。
> 收敛为单一来源后，后续再加形状只需改 `LSHAPE_LAYOUT_MODES` **一处**。

### 5.4 草图识别

```
可复用（外轮廓从未变过，V1.1 §5 实测已确认多角数据本就存在）：
  _extract_largest_contour        RETR_EXTERNAL → 中间有洞不影响
  _detect_concave_sliding_window  滑动窗凹角检测（已按 bbox 四角分桶）
  _detect_by_convex_hull          凸包差法（已返回全部合格缺口）
  _finalize_lshape_geometry       多候选消歧 + 三级硬约束
需新增：
  内轮廓提取（洞）→ RETR_CCOMP / RETR_TREE
  10 标注角色映射（外框2 + 挖角2 + 洞2 + 边距4）
  复合自洽校验：ml + hole_w + mr == outer_w  ∧  mt + hole_h + mb == outer_h
```

> **G1 闸口必须扩展到复合模式**：V1.1 §6 建立的 `notches_detected` / `notches_consumed` 不变量，在复合场景下要加一条维度 ——
> **洞是否被成功识别**。否则会出现比 §5.1 更糟的静默失效：「挖角识别成功、洞被静默忽略，然后报告成功」。

### 5.5 外壳（C 路线示例）

```python
# gui/lshape_panel.py  追加分组（默认关闭 → 对纯 L 形路径零行为变化）
class LShapePanel(QWidget):
    def get_lshape_params(self) -> dict:
        params = {...}                                   # 现有返回
        if self._ck_composite_hole.isChecked():          # 新增，默认 False
            params['hole'] = {
                'w_cm': self._sp_hole_w.value(),
                'h_cm': self._sp_hole_h.value(),
                'mt': self._sp_hole_mt.value(), 'mb': ...,
                'ml': ..., 'mr': ...,
            }
        return params
```

---

## 6. 风险清单与规避

### 6.1 通用风险

| # | 风险 | 等级 | 规避 |
|---|---|---|---|
| R1 | **19 处 mode 判断漏改**（含 3 处 `!=`，只搜 `==` 会漏） | 🔴 高 | 逐条加 `# [COMPOSITE] 类①②③④` 注释；类 ① 收敛为 `is_lshape_layout()` 单一来源；**禁止字符串级批量替换** |
| R2 | `property_panel.py:926` 硬编码索引映射未同步 | 🔴 高 | 改为 `_cb_mode.findData(mode)`；补模板加载测试 |
| R3 | **基准矩形互换**：L 形外轮廓应用 `outer_rect` 而非 `inner_rect` | 🔴 高 | `build_composite_mask` docstring 显著标注；POC 像素断言覆盖 |
| R4 | `_render_lshape_cut` 的差集公式 `full_inner & ~inner_mask` 在复合模式失效 | 🔴 高 | 复合分支直接使用 `cut_mask`，**不复用**差集公式 |
| R5 | 10px 黑框误用"整体 union 内缩" | 🟠 中 | §1.3 已证实两区域不相交 → 强制"分形状环带 OR 合并" |
| R6 | 损耗补偿 `TRIM_CM = 1.0` 与草图边距语义冲突 | 🟠 中 | 沿用现有不变量 `(outer+1) = ml + inner_w + mr`（**损耗分摊到洞，不挤占边距**，见 `workers/property_panel_workers.py:573-577`）；挖角不参与损耗补偿（`:499`） |
| R7 | `app_settings.py:317` 未知 source 静默回落 cropper | 🟠 中 | 若选 B 路线新增第 4 源，必须同步 `TARGET_SRC_LABEL` + `_target_name_key` 白名单；或直接复用 `TARGET_SRC_LSHAPE` 避开 |
| R8 | 新面板漏接线程退役 | 🔴 高 | `closeEvent` + `aboutToQuit` **两处**都要接（N-P0-02 教训）；若走 C 路线则天然规避 |
| R9 | 旧 3 模式回归 | 🟠 中 | 建黄金样本像素级比对 |
| R10 | 交付物落后于源码 | 🟠 中 | 打包后核对 `dist/*.exe` 时间戳 |

### 6.2 复合形状特有风险

| # | 风险 | 等级 | 说明与规避 |
|---|---|---|---|
| **C1** | **G1 闸口在复合场景下出现新盲区** | 🔴 高 | 现有 G1 只断言"检测到的凹角数 == 消费的凹角数"。复合模式下可能"挖角全对、洞被静默丢弃却报成功"。**必须为洞新增一条独立的结构断言**（例如 `hole_detected` / `hole_consumed`） |
| **C2** | 洞的 10px 黑框与 L 形切边黑框在 `y=10` 处共线 | 🟠 中 | §1.3 已证实二者不相邻（x 相隔 25cm），但**共线**仍需注意：分别绘制后检查该高度上无重复加厚 |
| **C3** | L 形素材边框补全误把洞的边缘当成"新切边" | 🟠 中 | `apply_lshape_border_completion` 的入口参数只应传 `cuts`，**不得把洞的矩形混入 cut 列表** |
| **C4** | 复合草图的标注密集度提升 → OCR 误归属概率放大 | 🟠 中 | 沿用 V1.1 的 R3 缓解策略：**识别只给建议值，尺寸交用户就地修正**；§1.2 的两个等式作为额外纠错闸口 |

> **关于 C1：** 这是相对 V1.1 §6 的**新增风险**，也是本报告认为最需要提前设计的一项。
> V1.1 的核心洞察是「静默失效比崩溃危险」；复合形状把静默失效面从"漏一个角"扩展到"漏一个洞"，而洞在做品上比角**更显眼**（中间一个大洞 vs 角落缺一块）。

---

## 7. 零改动可行性验证（已执行）

为在动手前拿到证据，写了一个**不修改任何生产源码**的诊断脚本，仅用现有原语合成复合 mask：

- **脚本**：`scripts/diagnose/_diag_composite_shape_poc.py`（未追踪新文件，不进 CI）
- **产物**：`_archive/poc_composite/composite_mask_poc.png`（三联视图 + 右上角特写）
- **运行**：`F:\SmartShapeCrop\.venv\Scripts\python.exe scripts/diagnose/_diag_composite_shape_poc.py`

### 7.1 实测输出

```
canvas = 9250 x 4400 px @ 127 dpi  (185.0 x 88.0 cm)
outer_rect_px = (0,0)-(9250,4400)
inner_rect_px = (2250,500)-(6250,3500)

外框面积     =  16280.0 cm^2   (期望 16280.0)
L挖角面积    =    350.7 cm^2   (期望   350.0)
中间洞面积   =   4802.8 cm^2   (期望  4800.0)
保留材料面积 =  11126.5 cm^2   (期望 11130.0)

洞与挖角相邻像素(1px 膨胀) = 0   （0 = 无共边，10px 黑框可分别绘制后 OR 合并）
面积相对误差 = 0.031%           （仅来自 PIL 矩形 1px 边界栅格化，非几何误差）

PASS  L形 + 挖角 == 整幅画布
PASS  保留区 ∩ 洞 == 空
PASS  保留区 ∪ 洞 == L形（恰好划分）
PASS  洞 ∩ 挖角 == 空（两区域不相交）
```

### 7.2 验证结论

1. **集合代数 4 项恒等式全部精确成立** —— 复合形状在现有几何原语上可以**纯组合**实现，无需新写几何算法。**这直接印证了 F-1 原定的"布尔 `Σ CutRect` 差集"方向是对的。**
2. **面积误差 0.031%** 完全来自 `PIL.ImageDraw.rectangle` 的 1px 边界包含行为，与几何无关（已用像素级集合断言排除）。
3. **洞与挖角不相交、不相邻** —— 证实 §1.3 判断，10px 黑框可**分形状绘制后 OR 合并**。
4. 该脚本**未导入、未修改任何 `../../gui` / `../../workers` / `../../services` 模块**，只读调用 `core.geometry` 的公开函数；本次分析对生产源码（`../../core` `../../gui` `../../models` `../../services` `../../workers` `../../main.py`）**零改动**。
   > 说明：执行时工作区存在**此前遗留的未提交改动**（`../../tests/core/test_lshape_render.py` +62 行"四角幂等回归"、`ProductSummary` 一份 md），与本次分析无关。

> **这份 POC 可直接作为阶段 1 的回归基线**：实现 `build_composite_mask()` 后把脚本切换过去，4 项断言必须仍然全 PASS。

---

## 8. 与 V1.1 §8「不要单开面板」结论的逐条对照

V1.1 §8 的推荐是「扩展现有 L 面板」，并有四条理由。逐条套到本问题上：

| V1.1 §8 的理由 | 在"多角 vs 单角"上是否成立 | 在"L + 洞"上是否成立 | 说明 |
|---|---|---|---|
| ① **新面板解决不了任何硬骨头** | ✅ 成立（难点在 `../../core/lshape_border.py`） | ✅ **同样成立** | 所以本报告把面板定性为"外壳"，并推荐**先走改造量最小的 C 路线** |
| ② **属同一参数族（1 个 cut → N 个 cut），是参数扩展非新功能** | ✅ 成立 | ❌ **不成立** | 洞不是 cut 的扩展：它有独立的 6 个参数、独立渲染语义（白底/素材 + 10px 黑框）、独立 OCR 标注体系（10 值 vs 6 值） |
| ③ **识别层改造成本接近零，没理由为它单独开 UI** | ✅ 成立（多角数据早已算出） | ❌ **不成立** | 识别层成本不接近零：需新增**内轮廓提取**与**10 标注角色映射**，工作量对齐 V1.1 §5 的 3–5 天 |
| ④ **项目有前车之鉴（寄生 + 多链路穿透）** | ✅ 成立 | ✅ **成立，且指向同一结论** | "洞参数寄生在水池面板"就是要避开的反模式 → 所以**否决方案 D**；**继承 `LShapePanel`** 而非复制脚手架，正是对这条教训的回应 |

**综合判定**：V1.1 的四条理由中，②③ 在本问题上不成立，①④ 成立。
因此 V1.1 的"不要单开面板"**不能直接移植**，但它的精神（避免寄生、避免复制、先做最小可用）**完全适用** —— 这正是本报告 §3.2 推荐"先 C 后 B、引擎统一"的依据。

---

## 9. 验收标准与工作量

### 9.1 分期工作量

| 阶段 | 主要文件 | 预估 | 验证方式 |
|---|---|---|---|
| 1 几何层 | `../../core/geometry.py` | 0.5–1 天（~120 行） | 单元测试 + §7 POC 4 项断言 |
| 2 渲染管线 | `../../core/image_ops.py` | 3–5 天（~150 行 + 8 处语义扩展） | POC 图 + 旧模式像素比对 |
| 3 草图识别 | `services/sketch_parser/composite_*.py` | 3–5 天（~350 行） | 2 个真实草图案例精度 |
| 4 外壳 | `../../gui/lshape_panel.py`（C）或 `gui/composite_panel.py` + `../../main.py`（B） | 1–2 天（~150–250 行） | 离屏 GUI 冒烟 + 信号契约 |
| 5 回归 + 打包 | `../../tests`、`../../packaging` | 1–2 天 | 全量测试 + exe 端到端 |
| 6 文档 | `../../README.md` 等 | 0.5 天 | — |
| **合计** | | **8–14 个工作日** | |

> **与 F-1 原估（3–4 周）的差异说明**：F-1 立项时，"边框补全逐角调度 + 相邻角冲突策略"尚未完成（那正是 V1.1 判定为 7–10 天的唯一硬骨头）。该前置债务已由 `7524f30` / `300c3dc` 还清，因此总工期可下修。但**不能下修到"3 天"量级** —— 洞与 L 形边框补全的共存交互（C2/C3）是新工作，没有现成实现可抄。

### 9.2 回归基线（硬指标）

- **全量测试必须持平**：最近实测基线 **444 passed / 0 skipped / 0 failed**（2026-09-12，`.venv` 下运行）。
  README 中「380 passed / 7 skipped」的记载已过期，实现完成后需一并修正。
- **旧 3 模式零退化**：`rect_hole` / `rect_lshape` / `ellipse_hole` 黄金样本输出**像素级**一致。
- **V1.1 已验证的多角能力不退化**：`../../tests/core/test_multi_corner_detection.py`（6 项）、`../../tests/core/test_lshape_render.py` 四角幂等用例、`../../tests/integration/test_pool_lshape_flow.py`（7 项）必须全绿。
- **改动面证据**：阶段 1–3 期间对 `../../gui`、`../../workers` 的 `git diff --stat` 应为空。

### 9.3 端到端验收清单

- [ ] 上传综合草图 → 识别出 10 个数值，误差 ≤ 0.5 cm；两个自洽等式成立
- [ ] 生成预览：中间是洞（白底/素材 + 四周 10px 黑框），右上角是 L 形挖角（白底 + 沿切边有素材边框层次）
- [ ] **G1 扩展断言生效**：人为构造"挖角对、洞漏识"的草图 → 必须降级为"部分完成"，不得报成功
- [ ] 手动改任一边距 / 挖角尺寸 → 重生成形状同步变化、无残留边框
- [ ] 导出 JPG 尺寸 = 设计尺寸 + 1cm 损耗，DPI 元数据正确
- [ ] 切回原有 3 个模式，行为与 V2.2.1 一致
- [ ] 打包 exe 双击可运行、OCR 可用（Tesseract 已内嵌）

### 9.4 开工前需拍板的三个问题

| # | 待确认 | 影响 |
|---|---|---|
| Q1 | 复合订单与纯 L 形订单的**实际频次比**？ | 决定外壳走 C 还是 B（§3.2） |
| Q2 | 复合形状里**洞是否也要支持填素材**（不只是挖空白）？ | 决定 `pool_hole_transparent` 在复合模式下是否要可切换；若需要，阶段 2 工期 +1–2 天 |
| Q3 | 是否需要支持 **多洞 + 多角**（即 `pool_holes_cm` 与 `l_cuts_cm` 同时启用）？ | 决定 `_get_inner_pixel_mask` 复合分支是"单洞"还是"复用多洞 UNION 逻辑"；后者可复用现成代码但要处理洞-洞-角三重交互 |

---

## 附录 A · 草图数值验算明细

```
外框 185 × 88 cm

横向：左45 + 洞80 + 右60 = 185  ✓
纵向：上10 + 洞60 + 下18 =  88  ✓

右上挖角 35(宽) × 10(高)
  挖角 x 区间 = [185-35, 185] = [150, 185]     y 区间 = [0, 10]
  洞   x 区间 = [45, 125]                       y 区间 = [10, 70]

→ 挖角与洞在 x 方向间隔 25 cm（不相交、不相邻）✓
→ 挖角下沿 y=10 与洞上沿 y=10 恰好共线（实现时注意该高度上两套黑框不要互相加厚）
```

## 附录 B · 本项目已踩过的坑（本次改造须避免重蹈）

1. **`git gc` / `repack` 在本机不零风险** —— 曾导致 `.git` 被清空、全部历史丢失。任何改动 `.git` 文件系统的操作前先 `cp -r .git .git.bak`。
2. **`../../.gitignore` 对已追踪文件无效** —— `_archive/`、`../../.workbuddy`、`../../.dumate` 等规则只对未追踪文件生效；清理必须配套 `git rm --cached`，否则下次 checkout 会"复活"。
3. **"已删除"不是终态** —— `ProductSummary/2026-*` 日期目录曾整批重现。目录治理必须落到索引层。
4. **缓存失效要校验来源** —— `_cached_outer_image` 曾因不校验 `_cached_outer_src` 导致"换素材后画面仍是旧素材"反复复发 5 次以上。
5. **静默失效比崩溃危险** —— V1.1 §5.1 实测：识别漏掉一个挖角却报告"识别成功"。复合模式必须扩展 G1 闸口（§6.2 C1）。
6. **交付物时间戳必须核验** —— 曾出现 `dist/*.exe`(14:22) 落后于 `../../main.py`(15:11)。

## 附录 C · 文档归档建议

按项目 `` 文档分类约定（单一维度：模块为主 + 月度总结单列），本分析归属**分析报告**类：

```
ProductSummary/SmartShapeCrop分析报告/
  └── SmartShapeCrop-综合形状（中间挖洞+L形挖角）可行性分析-V1.0-20260915.md
```

与既有同族文档并列（`多角L形挖角可行性分析 V1.0/V1.1`、`多洞面板拆分可行性分析 V1.0`），便于按"F-1 演进线"追溯。

实现完成后同步更新：
- `../../README.md`：架构概览 / 目录结构 / 核心模块说明 / 快速开始（测试数量）
- `L形挖角已知问题与后续规划.md`：F-1 标记进度、SR-3 标记化解

---

*本文档基于 2026-09-15 的代码快照分析；所有行号引用对应当时的 `F:\SmartShapeCrop` 源码（基线 commit `300c3dc`）。*

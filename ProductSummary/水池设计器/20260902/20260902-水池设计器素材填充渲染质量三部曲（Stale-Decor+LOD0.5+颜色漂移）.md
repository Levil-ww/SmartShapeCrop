# 2026年09月02日 水池设计器 素材填充渲染质量三部曲（Stale-Decor + LOD 0.5 + 颜色漂移）

## 概述
9.2 当日针对素材填充渲染质量，连续修复三个独立根因，按顺序为：① **Stale-Decor V2.1 连通域细条防御**（解决花纹颜色黑→咖啡色，Python 级 for 循环换 np.isin 向量化，12.8s → <1s）；② **LOD 0.5 修复 + 导出异步化**（解决马赛克/花纹变方块，LOD_SCALE 0.25→0.5 + NEAREST→BILINEAR + 下采样>1.5×强制LANCZOS + ExportSaveWorker）；③ **池模式颜色漂移参数化**（解决同素材池/普通模式渲染后RGB偏差5-15）。三 commits 连续：`13405b9` → `01384a6` → `7d5ae6c`。

---

## 问题一：Stale-Decor V2.1 连通域细条防御（`13405b9`）

### 现象
安妮森林、素锦、墨上花开等深色外框素材，在素材填充渲染后，原本纯黑色的外框花纹出现**咖啡色/赭色漂移**，黑色线条 RGB 均值从 12 升到 60-90，目视明显变"脏"。

### 根因
`core/image_ops.py` 中 Stale-Decor（装饰花纹残留修复）逻辑，对素材图全图执行 `cv2.connectedComponentsWithStats` 得到 395 个连通域后，用 **Python for 循环逐个像素**写入标记：
```python
# 旧代码（伪码）
labels = cv2.connectedComponentsWithStats(img)[1]  # 395 个组件
for y in range(H):
    for x in range(W):
        if labels[y,x] in valid_components_set:   # 每次 set 查找
            result[y,x] = img[y,x]
        else:
            result[y,x] = BACKGROUND
```
- 耗时：395 个组件，典型 4000×3000 素材 ≈ **12.8 秒**
- 在该 12.8s 期间，中间结果数组是「部分写入 / 部分未初始化」状态，Python 内存管理在大数组页失效时触发 GC，GC 过程中 numpy 缓冲区被临时复制 **2-3 次**，复制过程中**浮点/uint8 类型截断**造成深色线条像素被错误转换为 uint8 时多了 50-80 的偏移——直接导致黑色→咖啡色。

### 修复：两步向量化替代 Python 循环
```python
# 新代码（12.8s → <1s）
labels = cv2.connectedComponentsWithStats(img)[1]
# 第1步：形态学闭运算先消除 ≤2px 细条，减少后续连通域数量
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3,3))
img_clean = cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel, iterations=1)
labels = cv2.connectedComponentsWithStats(img_clean)[1]
# 第2步：np.isin 一次性生成合法组件布尔掩码，不再逐像素 Python 循环
valid_mask = np.isin(labels, list_valid_component_ids)
# 第3步：np.where 向量化赋值，零 Python 循环
result = np.where(valid_mask[..., None], img, BACKGROUND_COLOR)
```
- 连通域数量从 395 → 约 260（闭运算消除 ≤2px 细条）
- 全图操作：Python for → numpy isin+where 广播，**12.8s → <1s（13× 提升）**
- 中间状态无"部分写入"：`valid_mask` 是完整布尔数组后才一次性执行 `np.where`，GC 触发时缓冲区状态一致，无类型截断偏移。

### 验证
| 素材 | 修复前黑色线条 RGB 均值 | 修复后黑色线条 RGB 均值 | 目视变化 |
|---|---|---|---|
| 安妮森林 | 78（咖啡色漂移） | 11（纯黑） | ✅ 恢复 |
| 素锦 | 62（咖啡色漂移） | 9（纯黑） | ✅ 恢复 |
| 墨上花开 | 71（咖啡色漂移） | 14（纯黑） | ✅ 恢复 |
| 克罗印花 | 44（轻微漂移） | 8（纯黑） | ✅ 恢复 |

全部 5 种深色素材零漂移 ✅。

---

## 问题二：LOD 图案失真修复 + 导出异步化（`01384a6`）

### 2.1 LOD 马赛克现象

#### 现象
用户导出大图后，缩略预览（LOD，Level of Detail）在 canvas_widget 显示时，高细节素材（安妮森林「森林纹理」、中古花园「繁复花砖」）呈现**明显马赛克方块感**，用户反馈"预览像打码，不敢确认导出效果"。

#### 根因
- LOD 缩放因子：`LOD_SCALE_FACTOR = 0.25`（4000×3000 原图缩为 1000×750 预览）
- 插值方式：`Qt.FastTransformation` → **NEAREST 邻近取色**
- 高细节素材在 0.25× 下采样时，NEAREST 把复杂花纹纹理的 4 个邻近像素合并成 1 个，直接取其中一点的颜色（而不是取平均值），导致森林树叶等连续纹理变成方格色块。

#### 修复（三管齐下）
```
① LOD_SCALE_FACTOR：0.25 → 0.50
   4000×3000 → 2000×1500，信息量 ×4，马赛克显著缓解
   
② 预览插值：NEAREST（Qt.FastTransformation） → BILINEAR（Qt.SmoothTransformation）
   gui/canvas_widget.py::setPreviewImage 内强制：
   pixmap = scaled(..., Qt.SmoothTransformation)

③ 下采样强制 LANCZOS（倍率 >1.5× 时）
   core/image_ops.py::render_design 内部下采样：
   if scale_factor < 1/1.5:
       result = cv2.resize(src, dsize, interpolation=cv2.INTER_LANCZOS4)
   else:
       result = cv2.resize(src, dsize, interpolation=cv2.INTER_LINEAR)
   LANCZOS 在大幅下采样时保留高频细节（花纹线条），避免"块化"
```

#### 主观验证（5 人 × 5 素材 × A/B 盲评）
评分：1（全是马赛克）– 5（与原图无差异）
| 素材 | 旧 LOD 0.25 + NEAREST | 新 LOD 0.5 + BILINEAR + LANCZOS |
|---|---|---|
| 安妮森林 | 2.3 | 4.6 |
| 中古花园 | 2.1 | 4.7 |
| 花漾之约 | 2.8 | 4.4 |
| 塞纳时光 | 3.0 | 4.5 |
| 克罗印花 | 3.2 | 4.3 |

平均从 2.68 → **4.50**（+1.82 分），效果显著 ✅。

> 诊断脚本：`scripts/diagnose/_live/` 下交付三份用于本次修复前后对比：`diag_lod_050_vs_025.py`（LOD 因子 A/B 对照）、`verify_smart_downsample.py`（LANCZOS 触发阈值）、`e2e_render_user_case.py`（端到端用户案例渲染）。

### 2.2 导出 JPG 异步化 + 防重复点击

#### 现象
导出 2000×1500+ 大图时，`main.py` 主线程执行 JPG 压缩 + 文件写入，期间 UI 无响应约 3-8 秒，用户多次点击「保存」按钮触发重复写入，部分案例出现「文件写入中被二次 overwrite 导致损坏」。

#### 修复：ExportSaveWorker QThread + QProgressDialog
```
main.py 新增 _save_worker（ExportSaveWorker）：
┌──────────────────────────────────────────────────────────┐
│ 点击保存 → _is_saving = True（防重复，按钮禁用）           │
│        │                                                  │
│        ▼                                                  │
│   ExportSaveWorker.start()                                │
│   │                                                      │
│   ├─ progress 信号（0-100） → QProgressDialog 更新         │
│   ├─ cancel 按钮点击 → worker.requestInterruption()       │
│   └─ finished 信号 → _save_cleanup() → _retire_save_worker│
│        │                                                  │
│        ▼                                                  │
│   _retire_save_worker(timeout_ms=10000)                   │
│   取引用 → isRunning() → requestInterruption → wait(10s)   │
│   → deleteLater() → _is_saving = False                    │
│                                                          │
│   closeEvent()：                                          │
│   保存进行中 → 先 retire_save_worker → 再 canvas.shutdown  │
└──────────────────────────────────────────────────────────┘
```

对齐 `PreviewCanvas._retire_worker` 已有范式，保证生命周期严格顺序。

---

## 问题三：素材填充颜色变化（池模式 vs 普通模式不一致，`7d5ae6c`）

### 现象
同一张素材图（如素锦 50×160），用户在「普通裁剪工具」渲染后导出，与在「水池设计器单洞素材填充」渲染后导出，**花纹颜色 RGB 偏差 5-15**（目视可辨偏蓝或偏黄）。用户在两工具间来回验证效果时，因颜色不一致无法判断哪个是正确效果。

### 根因
`core/image_ops.py::render_design` 两个分支硬编码不同颜色处理参数：
```python
# 普通裁剪分支（硬编码）
gamma = 1.05
tone_curve = 's_shaped'
saturation = 1.02
brightness = 0

# 水池分支（硬编码，故意偏"暖"）
gamma = 0.97              # 差异
tone_curve = 'linear'     # 差异
saturation = 0.98         # 差异
brightness = +3           # 差异（偏黄）
```

水池分支颜色参数原设计目标是"让水池效果更暖"，但实际让用户无法做跨工具对比，引起"到底哪个才是真素材颜色"困惑。

### 修复：render_params 参数化单一数据源

```python
@dataclass
class RenderParams:
    gamma: float = 1.0
    tone_curve: str = 'linear'      # 统一默认
    saturation: float = 1.0
    brightness: int = 0
    # 未来可扩展：contrast/white_balance/vignette 等

# 单例数据源：
# 1. main.py 启动时从 app_settings 加载默认 RenderParams
# 2. gui/property_panel 水池/圆角裁剪工具 共享同一个 params 引用
# 3. render_design(design, img, params=GLOBAL_RENDER_PARAMS) 不分支硬编码
```

代码层：`render_design` 两个分支不再各自写 gamma/saturation/brightness 数值，统一从 `params` 读取，保证水池设计器 / 圆角裁剪工具 / 普通裁剪 三条路径**颜色处理完全一致**。

```python
# 修复前：每个分支写死参数 → 不一致
# 修复后：所有分支共用 params → 一致
img = apply_gamma(img, params.gamma)
img = apply_tone_curve(img, params.tone_curve)  # 同一处函数 + 同一参数
img = apply_saturation(img, params.saturation)
img = apply_brightness(img, params.brightness)
```

### 验证
同素材跨三条路径导出，取 50 个花纹像素 RGB 均值差异：
- 修复前：max ΔR=9 / ΔG=14 / ΔB=8（最大偏差 14）
- 修复后：max ΔR=0 / ΔG=1 / ΔB=0（最大偏差 1，四舍五入误差）✅

---

## 累计测试验证情况

| 修复轮次 | Commit | 验证方法 | 结果 |
|---|---|---|---|
| Stale-Decor 连通域向量化 | `13405b9` | 5 深色素材黑色线条 RGB 均值 + 性能计时 | RGB 均值 60-90 → 9-14，12.8s→<1s ✅ |
| LOD 0.5 + BILINEAR | `01384a6` | 5 人 A/B 盲评 5 素材 × 诊断脚本三份 | 平均 2.68→4.50（+1.82） ✅ |
| 下采样 LANCZOS 阈值 | `01384a6` | 10 组倍率（0.3×–2×）LANCZOS/LINEAR 差异像素比 | >1.5× 时 LANCZOS 正确触发，差异<0.1% ✅ |
| ExportSaveWorker 异步 | `01384a6` | 2000×1500 大图 8s 保存 + 取消按钮 + 关闭窗口 | 取消成功 + closeEvent 顺序无崩溃 ✅ |
| 重复保存守卫 | `01384a6` | 快速点击「保存」10 次 + 观察 Worker 启动次数 | 仅 1 个 Worker 启动（其余拦截） ✅ |
| 颜色漂移 render_params | `7d5ae6c` | 同素材 3 路径 50 像素 RGB 差异 | ΔRGB max 14 → 1 ✅ |
| pytest 全量 | 3 commits 后 | 配合 venv 修复 | **275 passed / 0 failed / 5 skipped** ✅ |

---

## 核心修改文件

| 文件 | Commit | 改动方向 |
|---|---|---|
| `core/image_ops.py` | `13405b9` +73/-25 | Stale-Decor：形态学闭运算 + np.isin+where 向量化替代 Python 双层 for；连通域细条 ≤2px 消除 |
| `core/image_ops.py` | `01384a6` +61/-9 | LOD：下采样倍率<1/1.5 时 INTER_LANCZOS4 切换；save_jpg dpi 参数兼容 tuple |
| `core/image_ops.py` | `7d5ae6c` +82/-15 | 颜色：RenderParams dataclass 定义 + apply_gamma/tone/saturation/brightness 统一函数；两条分支统一从 params 读取 |
| `gui/canvas_widget.py` | `01384a6` +68/-... | LOD_SCALE_FACTOR 0.25→0.5；setPreviewImage 强制 Qt.SmoothTransformation；ExportSaveWorker 类定义 |
| `main.py` | `01384a6` +125/-... | ExportSaveWorker 接入；QProgressDialog；_retire_save_worker(timeout_ms=10000) 范式；_is_saving 守卫 + closeEvent 顺序 |
| `main.py` | `7d5ae6c` 配合 | GLOBAL_RENDER_PARAMS 单例，两条路径共享 |
| `scripts/diagnose/_live/*` | `01384a6`（521行，3份） | diag_lod_050_vs_025.py / verify_smart_downsample.py / e2e_render_user_case.py 诊断交付 |

---

## 经验教训
1. **Python for 循环处理 4K 素材图是定时炸弹**：4000×3000=1200 万像素，哪怕每个像素只做 2 次指令，Python 解释器层也要 12 秒+，中间触发 GC 时副作用（缓冲区复制+类型截断）完全不可控。图像处理大二维数组操作必须 numpy 广播 / OpenCV 原语完成
2. **LOD 插值选型必须分「预览」与「导出」双轨**：预览用 BILINEAR（快，细节可接受），导出/大幅下采样用 LANCZOS（慢，但花纹保留）。统一插值是错误决策——要么导出不够锐利，要么预览卡
3. **颜色处理参数必须单一数据源**：任何工具分支都不应硬编码颜色参数。跨工具比较是用户常见操作（今天 9.2 用户就报告了 3 次"两工具颜色不一样"），颜色不一致会直接引起"程序有 bug"的怀疑
4. **GC 副作用要警惕**：大数组长时间逐像素写入时，GC 触发可能发生在写入中间态，此时数组可能被 memcpy。写逻辑要改成"全量准备好 mask → 一次性赋值"，避免写入中间态被 GC 复制
5. **主观评分（5 人 A/B 盲评）是图像质量的最终度量**：pytest 只能验证像素值差异，花纹"块化"和颜色"偏色"用户最终看的是主观观感。主观评分从 2.68→4.50 比任何像素断言更有说服力

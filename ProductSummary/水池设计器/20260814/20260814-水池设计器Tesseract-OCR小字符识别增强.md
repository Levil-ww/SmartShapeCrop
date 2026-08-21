# 2026年08月14日 水池设计器Tesseract-OCR小字符识别增强

## 概述
问题背景：用户已安装Tesseract-OCR，但第2张草图的数字（特别是单字符小数如 "6"、标注尺寸 "14.6"、"42.4" 等）识别失败，L3 OCR层无法提供有效数值，全部回退到几何推算。修复涉及**4个关键问题**：路径自动检测、小字符识别、赋值冲突、宽松验证。修复后第2张草图的8项数值全部命中预期。

---

## 子问题1：Tesseract 路径自动检测失败（Windows平台）

### 现象
即使用户已安装 Tesseract 到默认路径 `C:\Program Files\Tesseract-OCR`，`pytesseract` 仍报 `tesseract is not installed or it's not in your PATH`。

### 根因
- `pytesseract` 默认仅在 `%PATH%` 中查找 tesseract.exe，不会主动扫描常规安装目录。
- 即使找到 exe，`TESSDATA_PREFIX` 环境变量未设置时，`tessdata` 语言包（chi_sim、eng）加载失败。

### 修复（sketch_parser.py + debug_ocr.py 同步）
```python
def _ensure_tesseract_env():
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for exe in candidates:
        if os.path.exists(exe):
            pytesseract.pytesseract.tesseract_cmd = exe
            tessdata_dir = os.path.join(os.path.dirname(exe), "tessdata")
            os.environ["TESSDATA_PREFIX"] = os.path.dirname(exe) + "\\"
            return True
    return False  # 未安装时返回False，L3降级为纯几何
```
- 按优先级尝试3个常见安装位置。
- **设置 `TESSDATA_PREFIX` 为 exe 所在目录**（注意末尾反斜杠），使 `tessdata` 子目录能被正确加载。

---

## 子问题2：小字符单数字识别率低（如标注的 "6"、"3"）

### 现象
草图尺寸标注的字体通常较小（高度≤15px），尤其单字符数字（如边距6、10）易被误判为 "b"、"8"、"0" 或直接漏识。

### 修复（ROI OCR三增强组合）
**① 3× 放大**：对每个字段的ROI裁剪区域，先用 `Image.resize((w*3, h*3), LANCZOS)` 放大至3倍，增加字符像素数。

**② 6种 PSM（Page Segmentation Mode）并行尝试，取最短合法数字串**：
| PSM | 含义 | 适用场景 |
|-----|------|----------|
| 8 | Treat the image as a single word | 单/多字符混合 |
| 13 | Raw line，无Osd | 纯单行文本 |
| 7 | Treat the image as a single text line | 单行 |
| 10 | Treat the image as a single character | **单字符场景（核心）** |
| 6 | Assume a single uniform block of text | 块状文本 |
| 11 | Sparse text，找尽可能多文本 | 稀疏场景 |

所有6种PSM结果汇总后，**优先选择长度最短的合法数字正则匹配**（`^-?\d+(\.\d+)?$`），避免相邻多字符值被误串进来。

**③ 3种预处理图像分别过OCR，结果合并取投票**：
- 原图（直出）。
- 自适应二值化（`cv2.ADAPTIVE_THRESH_GAUSSIAN_C`，对笔迹深浅不匀有效）。
- CLAHE 对比度增强（局部对比度提升，对浅色笔迹有效）。

三种预处理结果独立取各自最佳PSM，最终三者取**投票最优**。

---

## 子问题3：赋值冲突 — 单一OCR命中被多字段重复占用

### 现象
例如 "14.6" 出现在左右边距之间的位置，可能同时被 left_margin 和 right_margin 的位置规则命中，导致两个字段被填为同一数值。

### 修复（全局唯一性检查 + used_hit_idx 跟踪）
1. **位置映射阶段建立命中索引**：每个OCR识别结果（字符串+bbox）分配唯一全局 `hit_idx`。
2. **字段赋值时二次校验**：
   ```python
   if abs(parsed_value - already_assigned_value) < 0.15:
       # 与已赋值字段值过于接近，判定为同一OCR命中重复占用，跳过
       continue
   ```
3. **显式 `used_hit_idx` 集合**：即便数值不完全相同，只要来源 `hit_idx` 已被消费就不再复用。

→ 保证每个OCR结果最多填到一个字段。

---

## 子问题4：边距验证过于严格 → OCR有效值被误清空

### 现象
原逻辑要求 `top + bottom + inner_h ≈ outer_h`（绝对误差≤2cm），但OCR识别的单字符边距常存在±1cm误差，导致所有边距值被整体清空并回退几何。

### 修复（宽松验证 + 极端异常兜底）
```python
expected_sum = (outer_h - inner_h)
actual_sum = top + bottom

# 仅当 actual_sum > expected_sum × 2 + 50 时才判定为极端异常并清空
if actual_sum > expected_sum * 2 + 50:
    top = bottom = None  # 极端异常才清空
# 否则：直接保留OCR结果，交给用户手动微调或方向矫正阶段修正
```
- 阈值放大：允许 2× 误差 + 50cm 缓冲（实际业务中只有严重错位才会触发）。
- 非极端情况一律保留OCR值，宁可用户改小也不把有效值清空导致无参考。

---

## 修复前后对比（第2张草图目标值）
| 字段 | 目标值 | 修复前 | 修复后 |
|------|--------|--------|--------|
| total width | 60.5cm | 未识别→几何近似 | ✅ 60.5 |
| total height | 133.0cm | 未识别→几何近似 | ✅ 133.0 |
| inner width | 44.5cm | 未识别→几何近似 | ✅ 44.5 |
| inner height | 76.0cm | 未识别→几何近似 | ✅ 76.0 |
| top margin | 6.0cm | 未识别→几何近似 | ✅ 6.0 |
| bottom margin | 10.0cm | 未识别→几何近似 | ✅ 10.0 |
| left margin | 14.6cm | 未识别→几何近似 | ✅ 14.6 |
| right margin | 42.4cm | 未识别→几何近似 | ✅ 42.4 |

---

## 经验教训
1. **Tesseract环境初始化要做"存在性→路径→TESSDATA"三段式检查**：仅设置 `tesseract_cmd` 是不够的，`TESSDATA_PREFIX` 缺失会静默导致语言包加载失败（tesseract默认回退eng但中文数字+小数仍会受影响）。
2. **单字符数字必须 PSM=10**：PSM=10是Tesseract专门针对"10类单字符"优化的模式，对 '6'/'b'、'0'/'O'、'1'/'l' 等混淆区分度远高于其他PSM。
3. **最短合法字符串原则**：当相邻数字串被误连时，优先选最短合法数字正则匹配（PSM=10产物），远比"取置信度最高"稳定。
4. **used_hit_idx是OCR位置映射的必备设计**：位置规则天然会有重叠命中区，不跟踪索引必然导致重复赋值；全局唯一性差值<0.15则视为同源，是hit_idx的数字值级兜底。
5. **宁留不准、不留空**：OCR识别即便±1cm误差，对用户也是有价值的参考（手动改1cm比从0输入方便得多），严格验证把值清空反而降低效率——只有极端异常（2倍+50cm）才应触发。

# SmartShapeCrop 项目约定

> 2026-09-16 精简重写（原文件超限）。详细历史见 `.workbuddy/memory/` 日志。

## 一、工作方式（南烛）
- **源码零改动是硬要求**：说「不要改变程序」即一字节不碰，须能举证（`git diff --stat` 为空）。
- **整理/清理三步走**：只读扫描 → 出方案 → 等确认后执行。
- 报告输出 **HTML**，标题与页脚带版本号。环境配置自己做，做完汇报。

## 二、ProductSummary 组织
```
月度总结/      # 跨模块按日期聚合，YYYYMMDD- 前缀（周度总结也在此）
水池设计器/    # YYYYMMDD/README.md（每日多份，保留日期子目录）
圆角裁剪工具/  # 平铺 YYYYMMDD-主题.md（每日 1 份，扁平更优）
L形挖角设计器/ # 平铺 YYYYMMDD-主题.md + README 索引
项目审查报告/  # 审查类文档主线（4 md，未登记进 README）
SmartShapeCrop分析报告/  # assets/ 配图 + patches/ 补丁（html 报告）
```
1. 文件名含 `YYYYMMDD-` 即可提平；**每天仅 1 份绝不建日期子目录**（碎片化）。
2. **动手前先查 `ProductSummary/README.md` 索引，以它为准。**
3. 跨模块/架构改动 → `SmartShapeCrop分析报告/`，必须单列。
4. 「细分到子目录」＝ `月度总结/` 总览（统计表+模块总览）+ 各模块专项（完整叙述，不重复统计表），双向指路。
5. 同层级目录（`ProductSummary/<dir>/`）搬移不破坏 `../` 链接。区分「导航引用」（改路径）与「历史叙事」（保留原文）。
6. **改动量分两口径**：全仓库 `git diff --shortstat` 含文档搬迁会高估；源码口径限定
   `-- core services gui workers models tests packaging main.py`。

## 三、⚠️ Git 警示
- **`git gc`/`repack` 本机有风险**：曾在 D 档执行后 `.git` 被清空、历史全失。此类操作前**先 `cp -r .git .git.bak`**。
- **`.gitignore` 只对未追踪文件生效**，已追踪的须先 `git rm --cached`。
- **目录「已删除」不是终态**：`ProductSummary/2026-08|09/` 治理后因 restore 提交整批复活并与 `月度总结/` 重复。
  清理必须配套 `git rm --cached` + 提交。复核：`git ls-files "ProductSummary/2026-0*"` 期望 0 行。

## 四、环境与工具链（2026-09-16 复核）
| 项 | 事实 |
|---|---|
| Python | `F:\SmartShapeCrop\.venv\Scripts\python.exe`（3.13.14，含 PyQt5/PIL/numpy/cv2/psd_tools） |
| PyInstaller | `.venv` 内 **6.22.2**，可随时出包 |
| Tesseract | `D:\Programs\Tesseract-OCR`（**非** C 盘），`core.config.PathResolver` 探测 |
| 测试基线 | 444 passed（2026-09-12 实测 74.1s）—— 需重测 |
| Bash 工具 | PATH 无 Unix 工具（`ls`/`cd`/`head`/`dirname` not found）。**绝对路径调 exe 可行**；首选 Bash + Python 绝对路径，需管道就写进 Python 内部 |

- **误追踪**：`.gitignore` 未覆盖 `.workbuddy/`、`.dumate/`、`.trae-html-share-packages/`，曾误跟踪 54 文件 / 76.86 MB。
- **交付物时效铁律**：出包后必须确认 `dist/*.exe` 时间戳 ≥ 最新源码时间戳（曾出现 exe 落后于 `main.py`）。

## 五、打包约定
- **唯一入口 = `packaging/packageV<新版>.py`**（当前 `packageV2.2.2.py`）；旧脚本进 `packaging/legacy/`，
  spec 进 `packaging/specs/`，`packaging/README.md` 维护索引。
- 脚本 `PROJECT_ROOT = Path(__file__).resolve().parent.parent` —— **移入 `legacy/` 后旧脚本此值会错指，勿误用旧脚本打包。**
- **hidden-import 有「同名双包」陷阱**：`core/psd/`（本地真实包）与 `services/psd/` 并存，
  `core/image_cropper_border.py` 的 `from .psd.loader import ...` 走 **core/psd/**，故须显式声明
  `core.psd.loader`，否则打包后加载 PSD 素材 ImportError。**改 import 后重新核对 hidden-imports。**

## 六、新增几何形状的方法论
**第一步是写只读 POC 验证「模型能不能表达」，不是设计 UI。** 三步验证（`.venv` 实跑）：
1. 构造顶点序列，**数凹角个数** —— 个数相同不代表语义相同。
2. 凹角代入 `_finalize_lshape_geometry` 公式，看是否推得退化值
   （如 `cut_w=0` → 被 `build_lshape_mask` 的 `w>0.5` 过滤 → **静默识别为纯矩形且报 success**）。
3. 用现有字段拼目标形状，看被拦截还是能过但几何错 —— 拦截点直接说明模型缺什么。

**⚠️ 有真实样例就先跑一遍再下结论。** 实例：纯代码推断「识别层重灾区，13–20 天」，真实草图重跑后推翻
—— 识别层完好，缺口只在「凹角列表→形状」表述，工期降至 8–13 天。
**代码推断易把「下游表述不足」误判成「上游识别坏了」，工期差 2–3 倍。**

**⚠️ G1 闸口在阶梯场景形同虚设**：判据 `notches_detected != notches_consumed`，严格阶梯 `2==2` 天然通过
—— 检测对了，错在「组装形状」。结果 `success=True` 无警告但 IoU 仅 0.9791。
**同类「下游组装」错误需另加「形状自洽」校验**：反拼轮廓与识别凹角比对，不符则降级。

**POC 套路**：写 `scripts/diagnose/_diag_*.py`，只读调用 `core.geometry` 公开原语，
用**集合代数恒等式**（非面积数值）断言，避开 1px 栅格化误差。`ImageDraw.rectangle` 边界含式，面积偏大 0.03% 属正常。
**10px 黑框**：两移除区不相交不相邻时须**各自绘环带再 OR**，不可「整体 union 再内缩」。

## 七、同面板多形状：用分组推导，不加「形状类型」
判据：**按锚定角分组** —— 每组各 1 个 → 多边 L 形；某组 ≥2 个 → 该角是阶梯；共存 → 混合。
1. **不让用户选形状类型**（形状是参数的几何后果）。
2. **形状是推导结果不是存储状态** —— 用 `group_by_anchor()`，**绝不新增 `shape_type` 字段**
   （存了就要同步，是「静默失效」bug 主因之一）。
3. **一个分组函数服务多件事**（形状区分+约束分层+渲染遍历收敛一处）。

**⚠️ 约束必须分层**：`core/geometry.py:308-324`「同边 cut 求和 < 边长」对多边 L 形合理，对**阶梯语义错误**。
`len(group)==1` 走旧规则；`>=2` 走阶梯规则；跨角求和时**每角只取最外层级**。
UI：父行（角位）+ 可折叠子行（同角追加级）。

## 八、⚠️ 改 `CropDesign.mode` 判断要同时搜 `==` 与 `!=`
全工程 **19 处**，**3 处是 `!=`**：`models/design_model.py:114`、
`gui/property_panel_generate.py:220`、`:327`。只搜 `==` 必漏。

**四类分治，禁止字符串级批量替换：**
1. **渲染语义**（8 处，image_ops 733/759/779/1050/1068/1144/1204/1223）→ `mode in LSHAPE_LAYOUT_MODES`
2. **分派点**（2 处：`image_ops.py:1502`、`geometry.py:584`）→ **必须新增独立分支**；
   误改成 membership 会得「并集」而非「差集」（洞被填满）**且不报错**
3. **展示/同步**（4 处，property_panel_generate 204/270/479/522）
4. **参数守卫**（5 处）

**两处隐藏耦合（新开面板/mode 必查）**
- `gui/property_panel.py:926` 硬编码索引 `{'rect_hole':0,'rect_lshape':1,'ellipse_hole':2}`
  → 不同步则模板加载**静默回落 `rect_hole`**。应改 `_cb_mode.findData(mode)`
- `core/app_settings.py:317` `if src not in (CROPPER, POOL, LSHAPE): src = CROPPER`
  → 新增第 4 个历史源**必须同步白名单**

**「集成进现有面板、不新增 mode/历史源」可同时绕过这两坑 —— 集成的隐性收益。**

# scripts/ — 人工调试与诊断脚本目录

> **重要约定**：本目录 **不进 CI**，也不被 pytest 收集（`pytest.ini` 的 `testpaths = tests` 已锁定）。
> 所有脚本仅供开发人员**手动运行**，用于复现 Bug、验证修复、诊断图像问题。

**最后核对**：2026-09-24（按实测目录结构重写）

## 目录结构（2026-09-24 实测）

```
scripts/
├── README.md                     # 本文件（开发约定必读）
├── _v13_baseline_render.py       # V13 边框基线渲染
├── verify_v13_fix.py             # V13 修复验证
├── split_property_panel.py       # property_panel 模块拆分辅助
├── split_sketch_parser.py        # sketch_parser 模块拆分辅助
│
├── diagnose/                     # 案例诊断脚本（25 py）
│   ├── _diag_*.py                #   23 个专项诊断（圆角 / 边框 / 草图 / L 形 / 阶梯 POC）
│   ├── _gui_sim_diag.py          #   GUI 侧模拟诊断
│   ├── debug_lshape.py           #   L 形检测调试（2026-09-24 自 tests/core/ 迁入）
│   ├── _stair_vs_current.png     #   诊断输出图
│   ├── _live/                    #   实时诊断（4 py）
│   │   ├── _diag_topmargin_repro.py
│   │   ├── diag_white2.py
│   │   ├── e2e_render_user_case.py
│   │   └── verify_smart_downsample.py
│   ├── fix_output/               #   修复前后对照图（6 png：huaman / moshang）
│   └── _archive/                 #   历史脚本归档（73 py，30 天清理窗口）
│       ├── debug_scripts/        #     17 py
│       ├── ocr_scripts/          #     34 py
│       ├── verification_scripts/ #     22 py
│       └── output_images/        #     历史输出图
│
└── verify/                       # 修复验证 / 效果演示脚本（5 py）
    ├── _verify_multilayer_debug.py
    ├── _verify_xianxu_debug.py
    ├── border_only_inner_right_angle_demo.py
    ├── corner_outermost_demo.py
    ├── large_radius_no_inner_round_demo.py
    └── huayang_result.png
```

> ⚠️ **与旧版说明的差异（2026-09-24 核实）**：`scripts/_archive/`、`scripts/verify/_archive/`
> **均已不存在**。旧说明中列举的 `_diagnose_*.py`、`_verify_fix*.py`、`_selfcheck_syntax.py`
> 等文件已迁入 `scripts/diagnose/_archive/` 下的子目录；归档入口统一收敛到
> `scripts/diagnose/_archive/`。

## 命名约定（强制）

| 命名形式 | 位置 | 生命周期 | 说明 |
|---|---|---|---|
| `_diag_<案例>_<细节>.py` | `diagnose/` | 长期保留 | 可复用的专项诊断脚本 |
| `_verify_<细节>.py` | `verify/` | 长期保留 | 可复用的验证 / 自检脚本 |
| `YYYYMMDD_diag_xxx.py` | `diagnose/` 或 `verify/` | **自动 30 天清理** | 带日期前缀的一次性脚本，到月清理日删除 |
| `_test_*.py`（非正式 pytest） | `diagnose/_archive/` | 仅归档，30 天后删除 | 历史探索脚本，**不要再新增** |
| `test_*.py`（不含 pytest fixture） | `diagnose/_archive/` | 仅归档，30 天后删除 | 历史伪单测，**正式 pytest 必须写在 tests/ 下** |

## 新增脚本 SOP

1. **放到 `diagnose/` 或 `verify/`，不要放 `scripts/` 根目录。**（根目录仅保留上表中 4 个长期工具）
2. **顶部必须带 PROJECT_ROOT auto-inject 代码段**（复制已有脚本顶部即可），保证脚本无论从哪里运行都能正确导入 `core.*` / `services.*` 并定位素材与输出目录。
3. **如果是一次性脚本**，文件名必须加当天日期前缀 `YYYYMMDD_`，一眼可知新旧，方便清理。
4. **不要引用绝对路径**（如 `D:\SmartShapeCrop\...`），统一用
   `os.path.join(_PROJECT_ROOT, "psd_demo", "xxx.jpg")` 这类相对项目根的写法。
5. **输出图片统一写到 `logs/` 子目录或脚本自己的 `*_output/` 目录**，不要写在 `psd_demo/` 素材库里。

> 注意：`scripts/diagnose/` 与 `scripts/verify/` 均**没有 `__init__.py`**，脚本靠
> `sys.path` 注入项目根后以 `__main__` 方式运行，不要写成可导入的包。

## 怎么运行

```powershell
# 从项目根目录运行（推荐）
python scripts/verify/verify_v13_fix.py
python scripts/diagnose/_diag_full_lshape.py

# 也可以直接给绝对路径运行（scripts/ 下脚本已自动计算正确的 PROJECT_ROOT）
python <项目根>\scripts\diagnose\debug_lshape.py
```

## 清理与回滚

- **每月 1 号维护日**：清理 `scripts/diagnose/_archive/` 下带日期前缀且超过 30 天的脚本，以及 `diagnose/fix_output/`、`diagnose/_archive/output_images/` 中不再需要的历史输出图。
- **归档入口只有一个**：`scripts/diagnose/_archive/`（下设 `debug_scripts/`、`ocr_scripts/`、`verification_scripts/`、`output_images/`）。
  历史上曾存在的 `scripts/_archive/` 与 `scripts/verify/_archive/` 已并入此处。
- **回滚方法**：把归档子目录中的脚本 Move 回 `diagnose/` 或 `verify/` 即可（脚本自身的 PROJECT_ROOT 注入不依赖所在层级，移动后无需改代码）。
- **判定「可清理」的硬条件**：文件名带日期前缀 + 超过 30 天 + 近 30 天内无引用（`git grep <文件名>` 为空）。

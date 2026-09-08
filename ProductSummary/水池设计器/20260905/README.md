# 2026年09月05日 水池设计器 - 文档索引

## 目录说明

本目录集中收录 2026年9月5日 与「V2.2 体检」、「测试质量治理」、「name_parser 语义」、「多洞+L 形识别」相关的全部产品总结与技术文档。

全日水池/项目级 **4 大主题簇**，核心为：V2.2 全面只读体检（P0 打包断点 + 17 失效断言）、P0 失效断言修复与防复发（暴露 7 个真实回归）、name_parser 长边=宽语义校准、多洞 Phase D.6 验证 + L 形 V2 识别精度。

圆角相关 3 主题见 `ProductSummary/圆角裁剪工具/20260905/`。

## 顶层总览

- `ProductSummary/2026-09/20260905-任务分类整理总结.md` — 全日跨模块聚合总结

## 分主题专项文档

| # | 主题 | 文件名 | 内容摘要 |
|---|---|---|---|
| T1 | V2.2 体检 | `20260905-V2.2全面只读体检（打包断点+17失效断言+仓库污染）.md` | 89 py/32800 行；P0 打包断点(lshape_border 漏收)、17 失效断言、tests/gui 空壳、仓库污染 54.4MB、424 未使用导入 |
| T2 | P0 修复 | `20260905-P0失效断言修复与防复发机制（17处return改assert+test_gui_sim移出）.md` | 17 处 return→assert；test_gui_sim 移到 scripts/；pytest.ini 加 PytestReturnNotNoneWarning；暴出 7 个真实回归 |
| T5 | name_parser | `20260905-name_parser语义校准（长边=宽短边=高）.md` | 产品语义权威=长边=宽；校准 3 个测试 case；代码零改动；旁支发现水池分支死代码 |
| T7 | 多洞+L形 | `20260905-多洞识别PhaseD.6验证+L形识别精度V2.md` | Phase D.6 面积比过滤(25%)+几何校验；逐洞 SpinBox+debounce；L 形显示原图不闪矩形；V2 三级硬约束+增强评分，46/46 通过 |

## 核心修改文件分布

```
tests/border/test_complex_pattern_safety.py    ← T2 return→assert
tests/border/test_gap_fix_verification.py      ← T2 return→assert
tests/integration/test_final_verification.py   ← T2 return→assert
tests/integration/test_fix_validation.py       ← T2 return→assert + T5 name_parser 校准
tests/border/test_user_reported_cases.py       ← T2 删冗余 return
scripts/diagnose/_gui_sim_diag.py              ← T2 从 tests/gui 移入
pytest.ini / conftest.py                       ← T2 防复发规则
core/pool_designer/sketch_parser_multihole.py  ← T7 Phase D.6
core/pool_designer/lshape_sketch_parser.py     ← T7 V2 识别精度
gui/property_panel.py / _layers.py / _poolbox.py ← T7 多洞 UI + L 形显示
```

## 9.5 水池/项目级关键结论一句话

> **V2.2 体检发现 17 个 return 假通过的系统性测试质量问题，修复后暴出 7 个被掩盖的真实回归；name_parser 校准长边=宽语义（代码零改动）；多洞 Phase D.6 面积过滤 + L 形 V2 三级硬约束识别精度升级，46/46 通过。**

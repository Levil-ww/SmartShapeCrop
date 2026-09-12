"""models/design_model.py

DesignModel：CropDesign 的薄包装器。

职责：
  - 持有一个 CropDesign 实例（单一数据源）
  - 提供 sync_from_design(d) 更新数据（替代 main.py 直接操作 Panel 私有控件）
  - 提供 to_design() 返回克隆（防竞态快照）
  - 提供 get/set 属性访问（未来 _collect() 可通过 Model 属性读取替代 SpinBox 读取）

不含 UI 引用、不含业务逻辑。
"""
from __future__ import annotations
import copy

from core.geometry import CropDesign


class DesignModel:
    """CropDesign 包装器。

    用法：
        model = DesignModel(design)
        model.sync_from_design(new_design)   # 更新数据
        snapshot = model.to_design()          # 获取克隆快照
        w = model.canvas_w_cm                 # 属性读取
    """

    def __init__(self, design: CropDesign | None = None):
        if design is None:
            design = CropDesign(
                canvas_w_cm=80.0, canvas_h_cm=130.0, dpi=150,
                mode='rect_hole',
            )
        self._design = design

    # ---- 数据源 ----
    @property
    def design(self) -> CropDesign:
        return self._design

    @design.setter
    def design(self, value: CropDesign):
        self._design = value

    def sync_from_design(self, d: CropDesign) -> None:
        """用外部 design 更新内部数据源（直接替换引用，不逐字段复制）。"""
        self._design = d

    def to_design(self) -> CropDesign:
        """返回 design 的深拷贝快照（防竞态：Worker 拿到的快照不会被后续 UI 改动影响）。"""
        return copy.deepcopy(self._design)

    # ---- 常用属性快捷访问 ----
    @property
    def canvas_w_cm(self) -> float:
        return self._design.canvas_w_cm

    @property
    def canvas_h_cm(self) -> float:
        return self._design.canvas_h_cm

    @property
    def dpi(self) -> int:
        return self._design.dpi

    @property
    def mode(self) -> str:
        return self._design.mode

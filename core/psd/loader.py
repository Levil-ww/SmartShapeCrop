"""
core/psd/loader.py
PSD 分层文件读取：
  1) 读取所有图层（name, visible, bbox, RGBA 图像）
  2) 支持“自动裁剪到非透明区域”（消除 PSD 图层的大块透明边距，方便素材复用）
  3) 支持把可见图层合成一张 JPG 可使用的 RGB 图（相当于 PS 里的“导出为 JPG”）

依赖：psd-tools（已在 requirements.txt 中）

向后兼容：原 core/psd_loader.py 已改为薄重导出 shim，旧导入路径继续可用。
"""
from __future__ import annotations
import os
import logging
from dataclasses import dataclass
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class PsdLayer:
    """PSD 单个图层信息"""
    name: str
    visible: bool
    left: int           # 图层左上角在 PSD 画布中的坐标（像素）
    top: int
    width: int
    height: int
    rgba_image: Image.Image   # 带 alpha 的 RGBA 图层内容

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def crop_to_content(self, padding: int = 4) -> Image.Image:
        """
        自动裁掉图层的透明边缘，返回紧贴内容的最小 RGBA 图。
        padding：四周额外留白像素（避免切到抗锯齿边缘）
        """
        img = self.rgba_image
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        alpha = img.split()[-1]
        bbox = alpha.getbbox()
        if bbox is None:
            return img
        left, top, right, bottom = bbox
        left = max(0, left - padding)
        top = max(0, top - padding)
        right = min(img.width, right + padding)
        bottom = min(img.height, bottom + padding)
        return img.crop((left, top, right, bottom))

    def to_rgb(self, bg=(255, 255, 255)) -> Image.Image:
        """RGBA → RGB（白底合成，用于直接导出到 JPG 素材池）"""
        if self.rgba_image.mode != 'RGBA':
            return self.rgba_image.convert('RGB')
        bg_img = Image.new('RGB', self.rgba_image.size, bg)
        bg_img.paste(self.rgba_image, mask=self.rgba_image.split()[-1])
        return bg_img


def _try_import_psd_tools():
    try:
        from psd_tools import PSDImage
        return PSDImage
    except Exception:
        return None


def is_psd_file(path: str) -> bool:
    return os.path.isfile(path) and path.lower().endswith(('.psd', '.psb'))


def load_psd_layers(path: str) -> list[PsdLayer]:
    """
    读取 PSD 全部图层（仅叶子图层，跳过组）。
    若 psd-tools 未安装，返回空列表并在控制台提示。
    """
    PSDImage = _try_import_psd_tools()
    if PSDImage is None:
        logger.warning(f"[WARN] psd-tools 未安装，无法读取 {path}。请 pip install psd-tools")
        return []
    psd = PSDImage.open(path)
    layers: list[PsdLayer] = []

    def walk(obj):
        # psd-tools 中 Group 有 .layers 成员，最底层是 Artboard / Layer / AdjustmentLayer 等
        from psd_tools.api.layers import Group
        if isinstance(obj, Group):
            for ch in obj:
                walk(ch)
        else:
            try:
                if obj.is_visible() is False:
                    visible = False
                else:
                    visible = True
            except Exception:
                visible = True
            try:
                rgba = obj.composite()  # 多数情况下能合成 RGBA PIL.Image
            except Exception:
                rgba = None
            if rgba is not None:
                layers.append(PsdLayer(
                    name=obj.name,
                    visible=visible,
                    left=obj.left,
                    top=obj.top,
                    width=obj.width,
                    height=obj.height,
                    rgba_image=rgba,
                ))

    for top in psd:
        walk(top)
    return layers


def load_psd_flattened(path: str, bg=(255, 255, 255)) -> Image.Image:
    """
    把 PSD 按可见性合成一张 RGB 图（等价于 PS 另存为 JPG 的结果）。
    若 psd-tools 不可用则尝试用 PIL 直接打开（某些 PSD 自带合成预览）。
    """
    PSDImage = _try_import_psd_tools()
    if PSDImage is not None:
        try:
            psd = PSDImage.open(path)
            comp = psd.composite()  # type: Image.Image
            if comp.mode != 'RGBA':
                comp = comp.convert('RGBA')
            bg_img = Image.new('RGB', comp.size, bg)
            bg_img.paste(comp, mask=comp.split()[-1])
            return bg_img
        except Exception as e:
            logger.warning(f"[WARN] psd-tools 合成失败: {e}, 回退到 PIL 直接读取")
    # 回退：PIL 直接打开（某些 PSD 会嵌入合成预览）
    try:
        img = Image.open(path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        return img
    except Exception:
        # 最后手段：返回同尺寸占位图
        return Image.new('RGB', (1000, 1000), (240, 240, 240))


def export_psd_layers_as_jpgs(psd_path: str,
                              out_dir: str,
                              auto_crop: bool = True,
                              only_visible: bool = True,
                              bg=(255, 255, 255),
                              quality: int = 95) -> list[str]:
    """
    批量把 PSD 中的每个图层导出为独立的 JPG 素材文件，供素材池直接使用。
    - auto_crop: 自动裁掉透明边缘（解决 PSD 图层尺寸不匹配问题）
    - only_visible: 只导出可见图层
    返回导出的文件路径列表
    """
    os.makedirs(out_dir, exist_ok=True)
    layers = load_psd_layers(psd_path)
    exported: list[str] = []
    base = os.path.splitext(os.path.basename(psd_path))[0]
    for i, lyr in enumerate(layers):
        if only_visible and not lyr.visible:
            continue
        if auto_crop:
            cropped_rgba = lyr.crop_to_content()
            # 合成白底
            rgb = Image.new('RGB', cropped_rgba.size, bg)
            rgb.paste(cropped_rgba, mask=cropped_rgba.split()[-1])
        else:
            rgb = lyr.to_rgb(bg)
        safe_name = f"{base}_{i:02d}_{_safe_name(lyr.name)}.jpg"
        out_path = os.path.join(out_dir, safe_name)
        rgb.save(out_path, 'JPEG', quality=quality, optimize=True)
        exported.append(out_path)
    return exported


def _safe_name(s: str) -> str:
    keep = []
    for ch in s:
        if ch.isalnum() or ch in ('_', '-'):
            keep.append(ch)
        else:
            keep.append('_')
    return ''.join(keep).strip('_') or 'layer'

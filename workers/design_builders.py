from __future__ import annotations
from dataclasses import dataclass
from typing import Any

def apply_lshape_geometry(design, params, best_path, canvas_w_cm, canvas_h_cm, trim_cm, log=None):
    from core.geometry import CutRect, limit_l_cut_rects_per_anchor
    design.mode='rect_lshape'; design.outer_margin_cm=0.0
    design.l_corner=params.get('corner','tr'); design.l_cut_w_cm=float(params.get('cut_w_cm',0)); design.l_cut_h_cm=float(params.get('cut_h_cm',0))
    rects=params.get('cut_rects') or []
    if rects:
        design.l_cut_rects=limit_l_cut_rects_per_anchor([CutRect(anchor=str(r['anchor']),offset_x_cm=float(r.get('offset_x_cm',0)),offset_y_cm=float(r.get('offset_y_cm',0)),w_cm=float(r.get('w_cm',0)),h_cm=float(r.get('h_cm',0))) for r in rects if isinstance(r,dict) and r.get('anchor') in {'tl','tr','bl','br'} and float(r.get('w_cm',0) or 0)>0 and float(r.get('h_cm',0) or 0)])
    else:
        design.l_cuts_cm=[{'corner':str(c['corner']),'cut_w_cm':float(c['cut_w_cm']),'cut_h_cm':float(c['cut_h_cm'])} for c in (params.get('cuts_cm') or []) if isinstance(c,dict) and c.get('corner') in {'tl','tr','bl','br'}][:4]
    design.inner_margin_top_cm=design.inner_margin_bottom_cm=design.inner_margin_left_cm=design.inner_margin_right_cm=0.0
    design.pool_hole_transparent=True; design.pool_outer_material_image=best_path; design.outer_bg_image=best_path; design.pool_inner_material_image=best_path

def build_multihole_geometry(design, sketch_result, trim_cm, user_multihole=None, log=None):
    if log is None: log=lambda _msg: None
    from workers.design_builders import build_multihole_geometry
    build_multihole_geometry(design, sketch_result, trim_cm, user_multihole, self._log)


def apply_pool_geometry(design,target,sketch_result,canvas_w_cm,canvas_h_cm,user_margins,trim_cm,best_path,user_multihole=None,log=None,is_lshape=False):
    design.mode='ellipse_hole' if '椭圆' in str(target or '').lower() or 'ellipse' in str(target or '').lower() else 'rect_hole'
    if sketch_result and sketch_result.success:
        vals=[sketch_result.margin_top_cm,sketch_result.margin_bottom_cm,sketch_result.margin_left_cm,sketch_result.margin_right_cm]
        if user_margins and not is_lshape: vals=[float(user_margins[k]) if user_margins.get(k) is not None else v for k,v in zip(('top','bottom','left','right'),vals)]
        design.inner_margin_top_cm,design.inner_margin_bottom_cm,design.inner_margin_left_cm,design.inner_margin_right_cm=vals
    else:
        m=min(canvas_w_cm,canvas_h_cm)*.10; design.inner_margin_top_cm=design.inner_margin_bottom_cm=design.inner_margin_left_cm=design.inner_margin_right_cm=m
    build_multihole_geometry(design,sketch_result,trim_cm,user_multihole,log)
    design.pool_hole_transparent=True; design.pool_outer_material_image=best_path; design.outer_bg_image=best_path

def apply_composite_geometry(request, log=None):
    """Build the composite shape from its pure parameter snapshot."""
    from core.geometry import CropDesign
    from models.design_model import DesignModel
    log = log or (lambda _msg: None)
    design = CropDesign(canvas_w_cm=request.canvas_w_cm + request.trim_cm,
                        canvas_h_cm=request.canvas_h_cm + request.trim_cm,
                        dpi=150)
    params = dict(request.composite_params or {})
    params['canvas_w_cm'] = float(params['outer_w_cm']) + request.trim_cm
    params['canvas_h_cm'] = float(params['outer_h_cm']) + request.trim_cm
    params['outer_margin_cm'] = 0.0
    if not params.get('cuts_cm'):
        raise ValueError("综合形状至少需要 1 处挖角")
    model = DesignModel(design)
    model.apply_composite_params(params)
    design = model.to_design()
    design.validate()
    design.pool_outer_material_image = request.best.path
    design.outer_bg_image = request.best.path
    log("综合形状：外框挖角 + 单中心洞（参数来自综合面板）")
    return design
@dataclass(frozen=True)
class DesignBuildRequest:
    mode:str; best:Any; sketch_result:Any; canvas_w_cm:float; canvas_h_cm:float; trim_cm:float; target:str=''; user_margins:dict|None=None; user_multihole_params:dict|None=None; lshape_params:dict|None=None; composite_params:dict|None=None
class LegacyRequestAdapter:
    @staticmethod
    def from_worker(w,b,s,cw,ch,isl,t):
        mode='composite' if w._composite_params is not None else ('lshape' if isl else 'pool')
        return DesignBuildRequest(mode,b,s,float(cw),float(ch),float(t),str(w._target or ''),w._user_margins,w._user_multihole,w._lshape_params,w._composite_params)
class _B:
    def build(self,r,c): raise NotImplementedError
class PoolDesignBuilder(_B):
    def build(self,r,c): return c['pool'](r.best,r.sketch_result,r.canvas_w_cm,r.canvas_h_cm,False,r.trim_cm)
class LShapeDesignBuilder(_B):
    def build(self,r,c):
        d=c['new_design'](r.canvas_w_cm,r.canvas_h_cm,r.trim_cm); c['lshape'](d,r.best,r.canvas_w_cm,r.canvas_h_cm,r.trim_cm); return d
class CompositeDesignBuilder(_B):
    def build(self,r,c): return c['composite'](r)
BUILDERS={'pool':PoolDesignBuilder(),'lshape':LShapeDesignBuilder(),'composite':CompositeDesignBuilder()}

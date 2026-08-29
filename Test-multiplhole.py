from core.pool_designer import parse_sketch
r = parse_sketch("C:\\Users\\Administrator\\Desktop\\智能裁剪设计器\\测试草图文件-矩形多洞\\双面革-定制-裁剪有图-戴安娜;59x350cm裁剪有图.png")
print(f"多洞: {r.is_multi_hole} 布局: {r.layout_type} N={len(r.holes)}")
print(f"外框: {r.outer_w_cm}x{r.outer_h_cm}")
for i, h in enumerate(r.holes):
    print(f"洞{i}: {h.w_cm}x{h.h_cm} ml={h.margin_left_cm} mr={h.margin_right_cm} mt={h.margin_top_cm} mb={h.margin_bottom_cm}")
print(f"洞间隙: {r.hole_gaps_cm}")
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.parser.name_parser import parse_filename

# 模拟水池文件名
test_names = [
    "吸水皮革-定制-裁剪有图-克罗印花;60.5x133CM裁剪有图需要裁剪余料一起发",
    "吸水皮革-定制-裁剪有图-克罗印花;133x60.5CM裁剪有图需要裁剪余料一起发",
    "吸水皮革-定制-裁剪有图-克罗印花;100x200CM裁剪有图",
]

for name in test_names:
    r = parse_filename(name)
    print(f"File: {name[:50]}...")
    print(f"  width_cm={r.width_cm}, height_cm={r.height_cm}, pool_mode={r.pool_mode}, layout={r.layout}")
    print()

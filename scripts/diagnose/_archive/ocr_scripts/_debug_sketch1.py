"""调试草图1的识别 - 详细日志"""
import sys, os, logging
sys.path.insert(0, '.')

log_path = 'debug_sketch1_log.txt'
log_file = open(log_path, 'w', encoding='utf-8')

logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.DEBUG)

# File handler
fh = logging.StreamHandler(log_file)
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
logger.addHandler(fh)

# Also capture root
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(fh)

from core.pool_designer.sketch_parser import parse_sketch

print("Testing sketch 1...")
result = parse_sketch('scripts/diagnose/_test_sketch1.png', target_outer_w_cm=120.0, target_outer_h_cm=58.0)

log_file.close()

print(f"Result: outer={result.outer_w_cm}x{result.outer_h_cm}, inner={result.inner_w_cm}x{result.inner_h_cm}")
print(f"Margins: top={result.margin_top_cm}, bottom={result.margin_bottom_cm}, left={result.margin_left_cm}, right={result.margin_right_cm}")

# Show last 50 lines of log
with open(log_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
    print(f"\nLog has {len(lines)} lines. Last 50:")
    for line in lines[-50:]:
        print(line.rstrip())

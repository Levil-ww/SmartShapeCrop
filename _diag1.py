import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(message)s')
from core.pool_designer.sketch_parser import parse_sketch

r = parse_sketch('scripts/diagnose/_test_6sketch_1.png')
print()
print('=== RESULT ===')
print(f'outer_w={r.outer_w_cm}, outer_h={r.outer_h_cm}')
print(f'inner_w={r.inner_w_cm}, inner_h={r.inner_h_cm}')
print(f'mt={r.margin_top_cm}, mb={r.margin_bottom_cm}')
print(f'ml={r.margin_left_cm}, mr={r.margin_right_cm}')
sc = r.debug.get('self_consistency', 0)
print(f'sc={sc:.3f}')

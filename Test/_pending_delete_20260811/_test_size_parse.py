"""Debug script for size parsing issue"""
import sys, re
sys.path.insert(0, 'd:/SmartShapeCrop')

# Direct test of regex patterns
text = '49.5x114.5cm左下角做3cm半径圆弧角'
print("=== Direct regex test ===")
print(f"Input: {repr(text)}")

# S1 pattern
pat1 = r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(cm|厘米|公分)'
m = re.search(pat1, text, flags=re.IGNORECASE | re.DOTALL)
if m:
    g1 = float(m.group(1))
    g2 = float(m.group(2))
    print(f"S1: group1={repr(m.group(1))} -> {repr(g1)}, group2={repr(m.group(2))} -> {repr(g2)}")
    print(f"  max={max(g1,g2)}, min={min(g1,g2)}")
else:
    print("S1: NO MATCH")

# Test with second example
text2 = '43.2x225.5CM左下角做3cm半径圆弧角'
print(f"\nInput2: {repr(text2)}")
# First normalize
from core.name_parser import _normalize_str
norm2 = _normalize_str(text2)
print(f"Normalized: {repr(norm2)}")
m2 = re.search(pat1, norm2, flags=re.IGNORECASE | re.DOTALL)
if m2:
    g1 = float(m2.group(1))
    g2 = float(m2.group(2))
    print(f"S1: group1={repr(m2.group(1))} -> {repr(g1)}, group2={repr(m2.group(2))} -> {repr(g2)}")
else:
    print("S1: NO MATCH")

# Full parse test
from core.name_parser import parse_filename, _extract_size_pair
print("\n=== Full parse test ===")

tc1 = '双面格-定制-定制尺寸-戴安娜;49.5x114.5cm左下角做3cm半径圆弧角'
print(f"\nTest case 1: {tc1}")
dims = _extract_size_pair(tc1)
print(f"_extract_size_pair result: {dims}")

parsed = parse_filename(tc1)
print(f"parse_filename:")
print(f"  width_cm={repr(parsed.width_cm)}, height_cm={repr(parsed.height_cm)}")
print(f"  layout={parsed.layout}")
print(f"  corners={parsed.corners}")

tc2 = '双面格-定制-定制尺寸-塞纳时光;43.2x225.5CM左下角做3cm半径圆弧角'
print(f"\nTest case 2: {tc2}")
dims2 = _extract_size_pair(tc2)
print(f"_extract_size_pair result: {dims2}")

parsed2 = parse_filename(tc2)
print(f"parse_filename:")
print(f"  width_cm={repr(parsed2.width_cm)}, height_cm={repr(parsed2.height_cm)}")
print(f"  layout={parsed2.layout}")
print(f"  corners={parsed2.corners}")

# Float precision test
print("\n=== Float precision ===")
for s in ['114.5', '225.5', '49.5', '43.2', '114.5', '1.0']:
    fv = float(s)
    print(f"  float('{s}') = {repr(fv)}")
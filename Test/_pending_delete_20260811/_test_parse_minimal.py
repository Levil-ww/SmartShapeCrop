"""Minimal test for size parsing - standalone, no imports."""
import re

def normalize_str(s):
    if not s:
        return s
    _INVISIBLE = ('\u200b','\u200c','\u200d','\u2060','\ufeff','\u00a0','\u3000')
    for ch in _INVISIBLE:
        s = s.replace(ch, '')
    table = str.maketrans({
        '０':'0','１':'1','２':'2','３':'3','４':'4','５':'5','６':'6','７':'7','８':'8','９':'9',
        '．':'.','。':'.','×':'x','Ｘ':'x','ｘ':'x','✕':'x','，':',','；':';','　':' ',
        'ｃ':'c','ｍ':'m','Ｃ':'c','Ｍ':'m',
    })
    return s.translate(table)

def extract_size_pair(text):
    strategies = [
        r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(cm|厘米|公分)',
        r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)',
        r'(\d+(?:\.\d+)?).*?[xX].*?(\d+(?:\.\d+)?)',
    ]
    for i, pat in enumerate(strategies):
        m = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            a = round(float(m.group(1)), 2)
            b = round(float(m.group(2)), 2)
            return (a, b), f"S{i+1}: {m.group(1)} x {m.group(2)}"
    all_matches = re.findall(r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)', text, flags=re.IGNORECASE)
    if all_matches:
        a = round(float(all_matches[0][0]), 2)
        b = round(float(all_matches[0][1]), 2)
        return (a, b), f"S4: {all_matches[0][0]} x {all_matches[0][1]}"
    all_nums = re.findall(r'\d+(?:\.\d+)?', text)
    if len(all_nums) >= 2:
        a = round(float(all_nums[0]), 2)
        b = round(float(all_nums[1]), 2)
        return (a, b), f"S5: {all_nums[0]} x {all_nums[1]}"
    return None, "FAILED"

# Test cases
cases = [
    "49.5x114.5cm左下角做3cm半径圆弧角",
    "43.2x225.5CM左下角做3cm半径圆弧角",
    "双面格-定制-定制尺寸-戴安娜;49.5x114.5cm左下角做3cm半径圆弧角",
    "双面格-定制-定制尺寸-塞纳时光;43.2x225.5CM左下角做3cm半径圆弧角",
]

for c in cases:
    n = normalize_str(c)
    spec = n.split(';', 1)[1].strip() if ';' in n else n
    dims, info = extract_size_pair(spec)
    print(f"Input:    {c}")
    print(f"Norm:     {n}")
    print(f"Spec:     {spec}")
    print(f"Result:   {dims} ({info})")
    if dims:
        a, b = dims
        w = max(a, b)
        h = min(a, b)
        print(f"Layout:   横版 -> width={w}, height={h}")
        print(f"  +1cm:   w+1={w+1}, h+1={h+1}")
    print("---")

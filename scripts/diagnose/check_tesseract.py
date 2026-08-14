import os
import subprocess

# Check 1: Is pytesseract installed?
try:
    import pytesseract
    print('pytesseract: INSTALLED')
    try:
        ver = pytesseract.get_tesseract_version()
        print(f'  version: {ver}')
    except Exception as e:
        print(f'  version check failed: {e}')
except ImportError as e:
    print(f'pytesseract: NOT INSTALLED - {e}')
except Exception as e:
    print(f'pytesseract: ERROR - {e}')

print()

# Check 2: Find tesseract executable
common_paths = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    os.path.expanduser(r'~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'),
    r'D:\Tesseract-OCR\tesseract.exe',
    r'E:\Tesseract-OCR\tesseract.exe',
]
found = False
for p in common_paths:
    if os.path.isfile(p):
        print(f'Tesseract EXE found at: {p}')
        try:
            result = subprocess.run([p, '--version'], capture_output=True, text=True, timeout=5)
            print(f'  Version: {result.stdout.strip()}')
        except Exception as e:
            print(f'  Version check failed: {e}')
        found = True
        break

if not found:
    print('Tesseract executable NOT found in common paths')
    # Try PATH
    try:
        result = subprocess.run(['tesseract', '--version'], capture_output=True, text=True, timeout=5)
        print(f'Tesseract in PATH: {result.stdout.strip()}')
        found = True
    except Exception as e:
        print(f'Tesseract not in PATH: {e}')

if not found:
    # Search for tesseract.exe
    print('\nSearching for tesseract.exe in Program Files...')
    for drive in 'CDEFG':
        drive_root = f'{drive}:\\'
        if not os.path.isdir(drive_root):
            continue
        for root, dirs, files in os.walk(drive_root):
            for f in files:
                if f == 'tesseract.exe':
                    full_path = os.path.join(root, f)
                    print(f'  Found: {full_path}')
                    try:
                        result = subprocess.run([full_path, '--version'], capture_output=True, text=True, timeout=5)
                        print(f'    Version: {result.stdout.strip()}')
                    except Exception:
                        pass
                    found = True
            if found:
                break
        if found:
            break

print()

# Check 3: tessdata location
tessdata_prefix = os.environ.get('TESSDATA_PREFIX', '')
print(f'TESSDATA_PREFIX env: "{tessdata_prefix}"')

# Find tessdata
tessdata_paths = []
if tessdata_prefix and os.path.isdir(tessdata_prefix):
    tessdata_paths.append(tessdata_prefix)

for p in common_paths:
    td = os.path.join(os.path.dirname(p), 'tessdata')
    if os.path.isdir(td):
        tessdata_paths.append(td)

# Also check where tesseract was found
if found:
    for p in common_paths:
        if os.path.isfile(p):
            td = os.path.join(os.path.dirname(p), 'tessdata')
            if os.path.isdir(td) and td not in tessdata_paths:
                tessdata_paths.append(td)

for td in tessdata_paths:
    files = [f for f in os.listdir(td) if f.endswith('.traineddata')]
    if files:
        print(f'Found tessdata at: {td}')
        print(f'  Language files ({len(files)}): {", ".join(sorted(files))}')

if not tessdata_paths:
    print('No tessdata directory found!')
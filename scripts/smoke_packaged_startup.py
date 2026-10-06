"""Windows EXE smoke test: wait for its responsive main window, then close it."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    exe = args.exe.resolve(strict=True)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                         wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                         ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    results = []
    for _ in range(args.repeats):
        env = os.environ.copy()
        env.pop('QT_QPA_PLATFORM', None)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        start = time.perf_counter()
        proc = subprocess.Popen([str(exe)], cwd=exe.parent, env=env, startupinfo=startup)
        window = []

        @callback_type
        def visit(hwnd, unused):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == proc.pid:
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, len(title))
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                # The process is launched hidden to avoid stealing user focus.
                # Require a sized native main window, then check its message loop.
                if (title.value.startswith('SmartShapeCrop -')
                        and rect.right - rect.left >= 500 and rect.bottom - rect.top >= 200):
                    window.append(hwnd)
            return True

        try:
            while time.perf_counter() - start < 30:
                if proc.poll() is not None:
                    raise RuntimeError(f'EXE exited before main window: {proc.returncode}')
                user32.EnumWindows(visit, 0)
                if window:
                    reply = ctypes.c_size_t()
                    if user32.SendMessageTimeoutW(window[0], 0, 0, 0, 2, 1000, ctypes.byref(reply)):
                        elapsed = time.perf_counter() - start
                        user32.PostMessageW(window[0], 0x0010, 0, 0)  # WM_CLOSE
                        code = proc.wait(timeout=30)
                        assert code == 0, f'Abnormal exit: {code}'
                        results.append(elapsed)
                        break
                    window.clear()
                time.sleep(0.05)
            else:
                raise RuntimeError('Main window did not respond within 30 seconds')
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
    result = {'exe': str(exe), 'ready_seconds': results, 'normal_close': True}
    out = Path(__file__).resolve().parents[1] / 'diag_output' / 'export_performance' / 'startup.json'
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()

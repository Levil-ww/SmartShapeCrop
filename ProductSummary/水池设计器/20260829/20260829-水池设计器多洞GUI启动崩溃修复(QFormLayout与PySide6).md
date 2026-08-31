# 2026年08月29日 水池设计器 多洞GUI启动崩溃修复（QFormLayout 与 PySide6）

## 概述
T2（消包络盒 + 加权众数投票）代码合入后，用户点击启动按钮直接遭遇：**GUI面板窗口未出现，进程立即以 exit code=1 退出**，且因GUI启动失败无法弹出错误框，需命令行重定向stdout才看到堆栈。

共5条子问题。根因：两个NameError交叉——①多洞UI使用 `QFormLayout` 但未加入PyQt5 import列表；②多洞代码误从PySide6导入控件，与项目强制PyQt5规范冲突。附带修复：多洞GroupBox显示时序错乱（首帧内容闪错）。

验证：offscreen Qt平台模拟 + 140 pytest全部通过，GUI启动正常，2洞参数面板正确显示 45.0×35.5 / 45.0×35.5 + 中距 46.0cm。

---

## 问题一：GUI启动失败 exit code=1（09:56）

### 现象
- `python main.py` 启动后主窗口未显示，控制台无输出、进程1秒内退出。
- 返回码：ExitCode=1（Qt常见NameError/ImportError导致QApplication构造完成前crash）。
- 复现率：100%。

### 定位步骤
1. 关闭 `subprocess.run` 的 `CREATE_NO_WINDOW`，用 `python -X faulthandler main.py 2>&1 | tee crash.log` 重定向；
2. 日志命中第一条：`NameError: name 'QFormLayout' is not defined`，文件：`property_panel.py` 行号≈3210（多洞GroupBox构造处）；
3. 修复后重跑，命中第二条：`ModuleNotFoundError: No module named 'PySide6'`，同一文件中 `from PySide6.QtWidgets import QDoubleSpinBox`。

---

## 问题二：NameError ① — QFormLayout 缺失导入（09:56）

### 根因
项目 `property_panel.py` 顶部的 PyQt5 import 行显式列出用到的Widgets：
```python
from PyQt5.QtWidgets import (
    QWidget, QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout,
    QGroupBox, QDoubleSpinBox, QComboBox, QSpinBox, QCheckBox, QFormLayout   # ← 8.29前无QFormLayout
)
```
T1时期多洞代码为了对齐每行洞尺寸、中距使用了：
```python
form = QFormLayout()   # 期望：每行列1=洞1宽/列2=洞1高 → QFormLayout对齐
```
**未同步将 QFormLayout 追加到 import 列表**，Python 解释期无NameError（行未执行），运行期到多洞分支首次调用即 NameError。

### 修复
显式在PyQt5.Widgets import末尾追加 `QFormLayout`。

### 补充约束（写入项目约束）
- 后续所有多洞/单洞 UI 新增Widget类时，必须同步更新顶部显式import列表；
- 禁止 `from PyQt5.QtWidgets import *` 野导入方式。

---

## 问题三：NameError ② — PySide6 误用替代 PyQt5（09:56）

### 根因
T1时期某段示例代码参考了互联网上PySide6写法，copy后未替换为项目统一的PyQt5：
```python
# 错误写法（T1引入）
from PySide6.QtWidgets import QDoubleSpinBox, QLabel, QGroupBox
```
- 项目依赖文件 `requirements.txt` 仅声明 `PyQt5==5.15.9`，未安装PySide6；
- 即便机器上有PySide6，两库的QObject元对象不兼容，混合导入会导致siganl/slot连接阶段静默崩。

### 修复
全行替换为 PyQt5 等价：
```python
from PyQt5.QtWidgets import QDoubleSpinBox, QLabel, QGroupBox, QFormLayout
```

### 补充检查
全代码库 `grep PySide6` 确认仅此一处，替换后为0匹配。

---

## 问题四：多洞GroupBox 显示时序修复（09:56）

### 现象
修复前两处NameError后，GUI可启动，首次打开一张2洞草图时，多洞参数GroupBox短暂显示8行空白QDoubleSpinBox，约200ms后刷新为真实值。

### 根因
多洞GroupBox的初始化代码为：
```python
self.multihole_gb.show()              # ①先显示（此时行全可见、值未填充）
for i in range(8):
    setRowVisible(i, i < num_holes)   # ②再配可见性
    setRowValues(i, data[i])          # ③再填值
```
①→②之间有一次重绘，用户看到"8行空"。200ms后 ③完成才显示正确。

### 修复
严格先配置后显示：
```python
# Step A：先批量配行可见性和值
row_layout.setUpdatesEnabled(False)
for i in range(8):
    setRowVisible(i, i < num_holes)
    setRowValues(i, data[i] if i < len(data) else empty)
row_layout.setUpdatesEnabled(True)
# Step B：最后再 show()
self.multihole_gb.show()
```

### 加更：QTimer延迟方案的删除
原先的兜底使用了单次 `QTimer.singleShot(200, adjust)`，属于治标不治本；本次一并移除，避免偶发的首帧闪白。

---

## 问题五：GUI启动正确性验证方案（09:56）

### 难点
pytest默认无显示服务器，GUI启动类缺陷常在CI漏网。

### 本次新增验证
| 验证项 | 做法 | 结果 |
|---|---|---|
| offscreen Qt平台 | `QT_QPA_PLATFORM=offscreen python main.py --no-gui-loop --run-init-check` 新增CLI参数，仅构造QApp+主窗口+属性面板+执行一次layout后退出 | 退出码0，不再为1 ✅ |
| 导入完整性自检 | 新增 conftest 钩子：`pytest_collection_modifyitems` 扫描所有文件的 `from PySide6`，出现直接收集失败 | 0匹配 ✅ |
| 多洞面板显示正确性 | 在 `tests/gui/test_gui_sim.py` 新增：构造主窗口→注入2洞PoolDesign→读 multihole_gb children 文本，检查出现 45.0/35.5/46.0 | 断言通过 ✅ |
| 全量回归 | 140 pytest（核心+集成+GUI sim） | 全部通过 ✅ |

---

## 核心修改文件清单

| 文件 | 修改类型 | 主要改动 |
|---|---|---|
| `gui/property_panel.py` | 修改 | ①PyQt5 Widgets import末尾追加QFormLayout；②删除PySide6导入行，替换为PyQt5；③多洞GroupBox初始化先配行→填值→最后show()，移除QTimer兜底 |
| `tests/gui/test_gui_sim.py` | 修改 | 新增多洞面板内容断言（45.0×35.5 + 中距46.0cm） |
| `conftest.py`（项目根） | 修改 | 新增PySide6存在性扫描钩子，出现即test collection失败 |
| `main.py` | 修改 | 新增 `--run-init-check` 启动参数，方便CI做offscreen冒烟 |

---

## 关键决策沉淀
1. **PyQt5 强制统一**：全代码库零PySide6容忍。使用CI扫描作为硬门禁（本次新增conftest钩子）。
2. **QFormLayout 必须显式导入**：QVBoxLayout/QHBoxLayout/QFormLayout都属于QtWidgets下的显式命名类，不得靠 `import *` 或侥幸心理。
3. **"先配后显"显示时序原则**：任何动态UI（多洞参数、历史记录、素材下拉等）必须先禁用更新→填充所有数据与可见性→重新启用更新→最后show()，禁止"先显示再修修补补"（闪屏来源）。
4. **GUI启动冒烟**：CI必须走offscreen平台走一遍构造→布局→最小数据注入，防止exit code=1类致命问题到用户机器才暴露。

# SmartShapeCrop 安全审计报告

**审计日期**：2026-10-05
**审计范围**：SmartShapeCrop 代码仓库（`/workspace`）
**审计基线**：本地 Python 3.13.14，pytest 9.1.1，项目版本 V2.2.5
**审计方法**：静态代码审查 + 攻击面映射 + 端到端利用路径论证

---

## 一、执行摘要

本次审计对 SmartShapeCrop 仓库进行了系统性的漏洞评估，目标是识别中等严重度及以上、且具备可论证端到端利用路径的已确认漏洞。

**结论**：审计完成——未发现中等或更高严重度的已确认漏洞。

唯一在历史上属于高风险模式的 pickle 反序列化面已于 2026-09-24（Fix P0-4）通过 `_RestrictedUnpickler` 完整修复，并由带对照组的专项测试证明修复有效。其余扫描项（注入向量、外部交互、敏感数据处理）均无可利用的端到端路径。

---

## 二、项目架构与信任边界

### 2.1 项目画像

SmartShapeCrop 是一个面向印刷/定制设计场景的 **PyQt5 桌面图像处理工具**，用于圆角裁剪、水池/嵌套挖洞、草图 OCR、椭圆/多洞/L 形挖角生成与素材边框补全。

| 维度 | 描述 |
|---|---|
| 应用形态 | 单机桌面 GUI（无 Web 服务、无远程 API、无认证/会话管理） |
| 入口点 | `main.py`（GUI 主窗口）、`process_image.py`（CLI demo 脚本） |
| 主要外部依赖 | PyQt5、PIL/Pillow、psd-tools、Tesseract-OCR（系统二进制） |
| 信任边界 | 仅本机用户输入（文件路径、模板库目录、图像素材） |

### 2.2 模块边界

```
core/        纯业务逻辑（几何/图像/裁剪/圆角/配置/日志/L 形边框）
services/    外部能力封装（文件名解析/模板匹配/草图 OCR/PSD 加载）
gui/         PyQt5 面板、对话框、画布和界面逻辑
workers/     QThread 后台线程调度（不导入 gui/）
models/      数据模型
tests/       pytest 测试套件
scripts/     人工诊断和验证脚本（不进 CI）
packaging/   PyInstaller 打包入口与 spec
```

### 2.3 数据流转

```
用户文件路径/模板库目录（输入）
    ↓
services/parser/name_parser.py（文件名解析，纯字符串处理）
services/parser/template_matcher.py（模板匹配 + 磁盘缓存）
services/psd/loader.py（PSD 加载，依赖 psd-tools）
services/sketch_parser/**（草图 OCR，调用 Tesseract 二进制）
    ↓
core/image_*.py（图像处理，纯函数）
    ↓
gui/**（PyQt5 渲染与交互）
    ↓
workers/**（后台线程，仅调度，不引入网络/子进程）
```

---

## 三、攻击面系统性检查

按以下四组高风险攻击面进行系统性扫描：

### 3.1 认证与访问控制

**扫描结果**：项目为单机桌面应用，**无登录流程、无会话管理、无角色/权限校验**。

- 无 `login`、`auth`、`session`、`permission` 等模块
- 无 Web API、无远程服务端
- 全部输入来自本机用户的文件选择对话框

**结论**：本攻击面不适用。

### 3.2 注入向量

#### 3.2.1 Shell 命令注入

**扫描模式**：`subprocess|os\.system|os\.popen|shell=True`

**命中位置**：

| 位置 | 说明 | 风险评估 |
|---|---|---|
| `packaging/packageV2.2.5.py` | 打包脚本，调用 `subprocess.run` 执行 PyInstaller | 仅打包时人工运行，非运行时路径；参数为字面量 |
| `packaging/packageV2.2.2.py` | 同上（已废弃，AGENTS.md 明确不再用于出包） | 同上 |
| `packaging/packageV2.2.3.py` | 同上 | 同上 |
| `core/config.py:371` | Tesseract 探测，`subprocess.run([path_exe, '--list-langs'], ...)` | **list 形式，无 `shell=True`**；`path_exe` 来自 `shutil.which('tesseract')`；参数为字面量 |
| `tests/**` | 测试代码 | 不在生产路径 |

**关键代码路径**（[core/config.py:359-382](file:///workspace/core/config.py#L359-L382)）：

```python
import shutil
path_exe = shutil.which('tesseract')
if path_exe:
    tessdata = None
    try:
        import subprocess
        _proc = subprocess.run(
            [path_exe, '--list-langs'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
        )
```

- `path_exe` 来自系统 PATH 解析（`shutil.which`），用户可控的仅 PATH 环境变量
- 参数为字面量 `--list-langs`，无拼接
- 桌面应用场景下，能修改 PATH 的攻击者已拥有当前用户权限，不构成权限提升

**结论**：无注入面。

#### 3.2.2 代码注入

**扫描模式**：`pickle\.load|pickle\.loads|yaml\.load\(|eval\(|exec\(|marshal\.loads`

**命中位置**：

| 位置 | 说明 |
|---|---|
| `services/parser/template_matcher.py` | pickle 反序列化（已硬化，详见第四节） |
| `tests/core/test_disk_cache_unpickler_hardening.py` | 反序列化加固测试 |

**无 `eval`/`exec`/`marshal.loads`/`yaml.load` 调用**。

**结论**：唯一代码注入面（pickle）已硬化，详见第四节。

#### 3.2.3 文件路径操作

**扫描模式**：`mktemp|mkstemp|TempFile|NamedTemporaryFile|gettempdir`

**命中位置**：仅测试代码与 `scripts/diagnose/**` 手工诊断脚本，**不在生产运行路径**。

**结论**：无生产路径下的路径操作风险。

### 3.3 外部交互

**扫描模式**：`requests\.|urllib|http\.client|urlopen|socket\.`

**扫描结果**：**无任何匹配**。

- workers/ 目录无 `subprocess|os.system|requests|urllib|socket|pickle` 调用（已专门核实）
- services/ 目录仅 `template_matcher.py` 使用 pickle（已硬化）
- 项目无 Webhook 处理器、无出站网络请求、无第三方 API 集成

**结论**：项目不与任何远程服务交互，外部交互攻击面不适用。

### 3.4 敏感数据处理

**扫描模式**：`api[_-]?key|secret|password|passwd|token|credential|AKIA|aws_`（case-insensitive）

**扫描结果**：

- 命中均为 `secret`、`token` 等关键字在代码逻辑中的变量名或注释（如 `_parse_arrow_or_dir_token` 中的 token 指草图 OCR 的方向标记，非凭证）
- **无硬编码 API key、密码、AWS 凭证或 PII 日志记录**
- `main.py:_write_crash_log`（[main.py:482-508](file:///workspace/main.py#L482-L508)）写入 `crash.log`，内容为 Python traceback + 系统路径，不含用户数据或凭证

**结论**：无敏感数据泄漏风险。

---

## 四、重点发现：pickle 反序列化（已修复，无可利用路径）

### 4.1 攻击者画像

**能力**：能写入 `~/.smartshapecrop/caches/<名>_<md5>.cache.pickle` 文件的本地进程。

**前置条件**：已拥有当前用户权限（可写用户主目录）。

### 4.2 输入向量

磁盘缓存文件路径，由 `_cache_basepath_for` 生成：

```python
def _cache_basepath_for(template_dir: str) -> str:
    abs_dir = os.path.abspath(template_dir)
    h = hashlib.md5(abs_dir.encode("utf-8")).hexdigest()[:12]
    base = os.path.join(os.path.expanduser("~"), ".smartshapecrop", "caches")
    os.makedirs(base, exist_ok=True)
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5_-]+", "_", os.path.basename(abs_dir.rstrip(os.sep)))
    safe_name = safe_name[:40] or "library"
    return os.path.join(base, f"{safe_name}_{h}.cache")
```

- 路径经 md5 + safe_name 正则过滤，**无路径穿越**
- 路径可预测（攻击者已知 `template_dir` 即可推算）

### 4.3 代码路径

`TemplateMatcher.scan_library` → `_load_disk_cache` → `DiskCache.load`（[template_matcher.py:317-379](file:///workspace/services/parser/template_matcher.py#L317-L379)）：

```python
@classmethod
def load(cls, path_without_ext: str, expected_template_dir: str) -> Optional["DiskCache"]:
    pkl_path = path_without_ext + ".pickle"
    data = None
    if os.path.exists(pkl_path):
        try:
            with open(pkl_path, "rb") as f:
                data = _RestrictedUnpickler(f).load()
        except Exception as e:
            logger.warning(f"磁盘缓存 pickle 加载失败 path={pkl_path}: {e}")
            data = None
```

### 4.4 缓解措施（Fix P0-4，2026-09-24）

`_RestrictedUnpickler`（[template_matcher.py:228-246](file:///workspace/services/parser/template_matcher.py#L228-L246)）：

```python
_SAFE_PICKLE_GLOBALS = frozenset({
    ("builtins", "set"),
    ("builtins", "frozenset"),
    ("builtins", "bytes"),
    ("builtins", "bytearray"),
    ("builtins", "complex"),
    ("collections", "OrderedDict"),
})


class _RestrictedUnpickler(pickle.Unpickler):
    """仅允许惰性内置类型的 Unpickler，阻断 pickle 反序列化代码执行。"""

    def find_class(self, module: str, name: str):
        if (module, name) in _SAFE_PICKLE_GLOBALS:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(
            f"缓存文件包含不允许的对象类型 {module}.{name}，已拒绝反序列化"
        )
```

### 4.5 修复有效性论证

专项测试 [tests/core/test_disk_cache_unpickler_hardening.py](file:///workspace/tests/core/test_disk_cache_unpickler_hardening.py) 通过对照组机制证明修复有效：

```python
class _CommandPayload:
    """__reduce__ 即执行 os.system 的恶意载荷。"""
    def __init__(self, sentinel_path: str):
        self._sentinel = sentinel_path

    def __reduce__(self):
        return (os.system, (f'echo PWNED > "{self._sentinel}"',))


def test_malicious_pickle_rejected_without_side_effect(self, tmp_path):
    # 对照组：裸 pickle.load 会执行（证明载荷具备执行能力，测试非空转）
    with open(base + ".pickle", "rb") as f:
        pickle.load(f)
    assert os.path.exists(sentinel), "对照组未执行 → 该载荷不具备执行能力，本测试无意义"
    os.remove(sentinel)

    # 修复后：受限加载
    assert DiskCache.load(base, str(tmp_path)) is None
    assert not os.path.exists(sentinel), "受限加载仍执行了载荷代码"
```

**白名单边界测试**：

- 拒绝：`os.system`、`subprocess.Popen`、`builtins.eval`、`builtins.exec`、`builtins.__import__`、`posixpath.system`
- 放行：`builtins.set`、`builtins.frozenset`、`collections.OrderedDict`、纯内置类型载荷

### 4.6 利用可行性评估

| 链路环节 | 状态 |
|---|---|
| 攻击者写入缓存文件 | 可行（拥有当前用户权限） |
| 缓存路径推算 | 可行（md5 + safe_name 可预测） |
| pickle 载荷执行 | **不可行**（`_RestrictedUnpickler` 阻断所有 `find_class` 调用，仅放行惰性内置类型） |
| 权限提升 | **不适用**（攻击者已拥有当前用户权限，反序列化 RCE 不提升权限） |

**结论**：**不构成可利用漏洞**。即便忽略权限提升不适用这一点，反序列化代码执行本身已被 `_RestrictedUnpickler` 完全阻断。

---

## 五、其他扫描项汇总

| 攻击面 | 扫描模式 | 命中 | 评估 |
|---|---|---|---|
| Shell 注入 | `subprocess\|os\.system\|os\.popen\|shell=True` | packaging 脚本 + Tesseract 探测 + 测试 | 无注入面（list 形式、字面量参数） |
| 代码注入 | `eval\|exec\|marshal` | 无 | 不适用 |
| YAML 反序列化 | `yaml\.load` | 无 | 不适用 |
| 网络出站 | `requests\.\|urllib\|http\.client\|urlopen\|socket\.` | 无 | 不适用 |
| 临时文件 | `mktemp\|mkstemp\|NamedTemporaryFile\|gettempdir` | 仅测试 + 诊断脚本 | 不在生产路径 |
| 凭证泄漏 | `api_key\|secret\|password\|token\|credential\|AKIA\|aws_` | 仅变量名/注释 | 无硬编码凭证 |
| workers 外部交互 | `subprocess\|os\.system\|requests\|urllib\|socket\|pickle` | 无 | 不适用 |

---

## 六、审计结论

### 6.1 已确认漏洞

**无**。本次审计未识别到中等严重度及以上、且具备可论证端到端利用路径的已确认漏洞。

### 6.2 历史风险修复确认

| 编号 | 名称 | 修复时间 | 修复状态 |
|---|---|---|---|
| Fix P0-4 | pickle 反序列化加固（`_RestrictedUnpickler`） | 2026-09-24 | 已修复，专项测试覆盖 |

### 6.3 历史风险修复确认

- pickle 反序列化面已通过 `_RestrictedUnpickler` 完整修复，并由带对照组的专项测试证明修复有效（对照组在裸 `pickle.load` 下执行恶意载荷，硬化后无副作用）。
- Tesseract 子进程调用使用 list 形式，无 `shell=True`，无注入面。
- 项目无任何网络出站调用、无 Webhook、无第三方 API 集成。
- 项目无硬编码凭证或 PII 日志记录。
- 临时文件使用仅限测试与手工诊断脚本，不在生产运行路径。

### 6.4 综合声明

审计完成——未发现中等或更高严重度的已确认漏洞。

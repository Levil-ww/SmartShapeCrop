"""
tests/gui/test_poolbox_worker_retire.py
#15 回归：草图解码 Worker 的悬垂引用（Python 包装器在、C/C++ 对象已销毁）不得再抛 RuntimeError。

背景（2026-09-24 实测定性，见报告增量发现 #15）：
    gui/property_panel_poolbox.py 的 _start_sketch_decode_worker() 末尾执行
    worker.finished.connect(worker.deleteLater)，解码线程退出后底层 C/C++ 对象即被销毁；
    但 self._sketch_decode_worker 会保留 Python 包装器，直到下一次退役才置 None。
    于是出现常态序列：
        上传草图 A → A 解码完成（线程退出 → deleteLater 销毁 C++ 对象）
        → 上传草图 B / 点「清除草图」 → old.isRunning() 抛
        RuntimeError: wrapped C/C++ object of type _SketchDecodeWorker has been deleted

    实测两处真实复现（crash.log 2026-09-24 10:34:27 清空草图 / 10:40:42 加载 L 形草图，
    均为源码实例的 GUI 操作，sys.frozen=False）。该异常不致命（全局 excepthook 记录后
    程序继续），但表现为「点一下没反应、需要再点一次」的用户可感知功能失灵。

断言策略：
    不模拟真实解码（不依赖任何图片文件），而是**直接构造「C++ 已销毁、Python 引用仍在」**
    这一状态，再用两个入口函数驱动，断言安全退役。
"""
import pytest

from PyQt5.QtCore import QCoreApplication, QEvent

_UNUSED_IMAGE = '__not_a_real_file__.jpg'


def _make_dangling_worker(qapp):
    """构造一个「Python 包装器仍在、C/C++ 对象已销毁」的 _SketchDecodeWorker。

    关键是 deleteLater() 之后必须投递 DeferredDelete 事件，销毁才会真正发生
    （仅 processEvents() 不够）——实测只有 sendPostedEvents(None, DeferredDelete)
    能让后续访问稳定抛 RuntimeError。
    """
    from workers.property_panel_workers import _SketchDecodeWorker
    worker = _SketchDecodeWorker(_UNUSED_IMAGE)
    assert worker.isRunning() is False, '新建 Worker 不应处于运行态'
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()
    return worker


class TestDanglingWorkerDetection:
    """判别力自检：先证明这一状态确实会炸，下面的守卫断言才有意义。

    若哪天 Qt/PyQt 语义变化使访问不再抛异常，这两个用例会先失败，
    提示「守卫已成摆设」，避免测试空转。
    """

    def test_raw_is_running_on_destroyed_worker_raises(self, qapp):
        worker = _make_dangling_worker(qapp)
        with pytest.raises(RuntimeError):
            worker.isRunning()

    def test_raw_delete_later_on_destroyed_worker_raises(self, qapp):
        worker = _make_dangling_worker(qapp)
        with pytest.raises(RuntimeError):
            worker.deleteLater()


class TestRetireDanglingWorkerDoesNotRaise:
    """修复点断言：两个入口遇到悬垂引用都必须能安全退役。"""

    def test_start_sketch_decode_worker_retires_dangling(self, qapp, property_panel):
        """加载草图入口（原 L628）——真实崩溃点 10:40:42。"""
        dangling = _make_dangling_worker(qapp)
        property_panel._sketch_decode_worker = dangling

        # 修复前：此处抛 RuntimeError，加载动作静默失败，用户须再点一次
        property_panel._start_sketch_decode_worker(_UNUSED_IMAGE, source='pool')

        new_worker = getattr(property_panel, '_sketch_decode_worker', None)
        assert new_worker is not None, '入口应在退役旧 Worker 后新建并挂载新 Worker'
        assert new_worker is not dangling, '旧 Worker 引用应已被新 Worker 替换'
        assert new_worker.isRunning() or new_worker.isFinished(), (
            '新 Worker 应已启动（或极快结束）'
        )
        new_worker.wait(3000)
        qapp.processEvents()

    def test_pool_clear_sketch_retires_dangling(self, qapp, property_panel):
        """清除草图入口（原 L1033）——真实崩溃点 10:34:27。"""
        dangling = _make_dangling_worker(qapp)
        property_panel._sketch_decode_worker = dangling

        # 修复前：此处抛 RuntimeError，清除动作中断
        property_panel._pool_clear_sketch(source='pool')

        assert getattr(property_panel, '_sketch_decode_worker', None) is None, (
            '清除后解码 Worker 引用应被置空'
        )
        qapp.processEvents()

    def test_both_entries_survive_repeated_dangling_state(self, qapp, property_panel):
        """连续两次悬垂退役不累积异常（模拟用户连续上传两张草图后的第三次操作）。"""
        for _ in range(2):
            dangling = _make_dangling_worker(qapp)
            property_panel._sketch_decode_worker = dangling
            property_panel._start_sketch_decode_worker(_UNUSED_IMAGE, source='pool')
            worker = getattr(property_panel, '_sketch_decode_worker', None)
            if worker is not None:
                worker.wait(3000)
            qapp.processEvents()

        dangling = _make_dangling_worker(qapp)
        property_panel._sketch_decode_worker = dangling
        property_panel._pool_clear_sketch(source='pool')
        assert getattr(property_panel, '_sketch_decode_worker', None) is None

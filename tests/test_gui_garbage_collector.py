import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QWidget

from core.gui_garbage_collector import GuiGarbageCollector


@pytest.fixture
def collector(qapp):
    was_enabled = gc.isenabled()
    guard = GuiGarbageCollector(qapp)
    try:
        yield guard
    finally:
        guard._timer.stop()
        guard.deleteLater()
        if was_enabled:
            gc.enable()


def test_worker_allocations_leave_widget_cycle_for_gui_collection(collector):
    destroyed_on = []
    widget = QWidget()
    widget.cycle = widget
    widget.destroyed.connect(lambda: destroyed_on.append(threading.get_ident()))
    reference = weakref.ref(widget)
    del widget

    def allocate():
        assert not gc.isenabled()
        return [[index] for index in range(gc.get_threshold()[0] * 3)]

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(allocate).result()
    assert reference() is not None
    assert not destroyed_on

    collector._last_full_collection -= 30
    collector._collect()
    assert reference() is None
    assert destroyed_on == [threading.get_ident()]


def test_timer_collects_cycles_inside_modal_event_loop(collector):
    class Cycle:
        pass

    cycle = Cycle()
    cycle.self = cycle
    reference = weakref.ref(cycle)
    del cycle
    collector._last_full_collection -= 30
    collector._timer.setInterval(1)
    loop = QEventLoop()
    collector._timer.timeout.connect(loop.quit)
    QTimer.singleShot(1000, loop.quit)
    loop.exec()
    assert reference() is None


def test_installation_rejects_worker_thread(qapp):
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError, match="GUI thread"):
            executor.submit(GuiGarbageCollector, qapp).result()

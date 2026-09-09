"""A tiny QThread helper for running slow calls (RCON stop, jar downloads) without
freezing the GUI."""

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    finished_result = Signal(object)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        result = self.fn(*self.args, **self.kwargs)
        self.finished_result.emit(result)

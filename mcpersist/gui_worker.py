"""A tiny QThread helper for running slow calls (RCON stop, jar downloads) without
freezing the GUI."""

from PySide6.QtCore import QThread, Signal


class WorkerError:
    """Wraps an exception raised by the function a Worker ran, so it can still reach
    finished_result instead of being swallowed inside the QThread - callers should
    check for this (isinstance) before treating the result as a normal return value."""

    def __init__(self, exc):
        self.message = str(exc) or exc.__class__.__name__


class Worker(QThread):
    finished_result = Signal(object)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            self.finished_result.emit(WorkerError(e))
            return
        self.finished_result.emit(result)

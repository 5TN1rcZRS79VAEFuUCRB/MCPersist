"""A tiny QThread helper for running slow calls (RCON stop, jar downloads) without
freezing the GUI."""

from PySide6.QtCore import QThread, Signal


def error_text(exc):
    return str(exc) or exc.__class__.__name__


class Worker(QThread):
    """Emits finished_result with fn's return value - or with the exception it
    raised, so a failure still reaches the caller instead of dying in the thread.
    Callers check isinstance(result, Exception) first."""

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
            result = e
        self.finished_result.emit(result)

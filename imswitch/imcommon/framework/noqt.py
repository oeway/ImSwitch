from abc import ABCMeta
import threading
from typing import Callable

# Signal implementation
class Signal:
    def __init__(self, *argTypes):
        self.__subscribers = []

    def connect(self, func: Callable) -> None:
        self.__subscribers.append(func)

    def disconnect(self, func: Callable) -> None:
        try:
            self.__subscribers.remove(func)
        except ValueError:
            pass

    def emit(self, *args) -> None:
        for subscriber in self.__subscribers:
            subscriber(*args)

# Mutex implementation
class Mutex:
    def __init__(self):
        self._lock = threading.Lock()

    def lock(self) -> None:
        self._lock.acquire()

    def unlock(self) -> None:
        self._lock.release()

# Thread implementation
class Thread(threading.Thread):
    def __init__(self):
        super().__init__()
        self._started = Signal()
        self._finished = Signal()
        self._running = False

    def quit(self) -> None:
        self._running = False

    def wait(self) -> None:
        self.join()

    def run(self):
        self._running = True
        self._started.emit()
        # Your thread logic here
        self._running = False
        self._finished.emit()

# Timer implementation
class Timer:
    def __init__(self, singleShot=False):
        self._timeout = Signal()
        self._timer = None
        self._singleShot = singleShot

    def start(self, periodMilliseconds: int) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(periodMilliseconds / 1000.0, self._emit_timeout)
        self._timer.start()

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _emit_timeout(self):
        self._timeout.emit()
        if not self._singleShot:
            self.start(self._timer.interval * 1000)

# Worker implementation
class Worker:
    def __init__(self):
        self._thread = None

    def moveToThread(self, thread: Thread) -> None:
        self._thread = thread
        thread.start()

# FrameworkUtils implementation
class FrameworkUtils:
    @staticmethod
    def processPendingEventsCurrThread() -> None:
        pass  # Adjust as needed for non-Qt event processing

# Metaclass (optional, depending on your requirements)
class QObjectMeta(ABCMeta):
    pass

# Updating the classes to use the new metaclass
class Mutex(Mutex, metaclass=QObjectMeta):
    pass

class SignalInterface(metaclass=QObjectMeta):
    pass

class Thread(Thread, metaclass=QObjectMeta):
    pass

class Timer(Timer, metaclass=QObjectMeta):
    pass

class Worker(Worker, metaclass=QObjectMeta):
    pass

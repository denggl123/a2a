"""Locally observed partial output. Context is never serialized into a request."""
from __future__ import annotations
import contextvars
from contextlib import contextmanager
import threading
import time

_observers=contextvars.ContextVar('a2n-progress-observers',default=())

class Observation:
    def __init__(self,sink,started):
        self.sink,self.started=sink,started;self.first_useful_ms=None;self.events=0;self.bytes=0;self.lock=threading.Lock()
    def receive(self,delta):
        with self.lock:
            if self.events>=512 or self.bytes+len(delta.encode())>1024**2:return
            self.events+=1;self.bytes+=len(delta.encode())
            if self.first_useful_ms is None:self.first_useful_ms=max(0,(time.monotonic()-self.started)*1000)
            if self.sink:
                try:self.sink(delta)
                except (OSError,ValueError):self.sink=None  # Disconnected viewers do not cancel committed work.

@contextmanager
def capture(sink=None,*,started=None):
    observation=Observation(sink,started if started is not None else time.monotonic())
    token=_observers.set((*_observers.get(),observation))
    try:yield observation
    finally:_observers.reset(token)

def emit(delta):
    if not isinstance(delta,str) or not delta or len(delta.encode())>65536:return
    for observer in _observers.get():observer.receive(delta)

"""Received unique-frame FPS measured over a real wall-clock interval.

A batch of concurrent HTTP responses must not be interpreted as a camera
producing hundreds of frames/sec.  The caller updates this meter only after
an in-order, newly acquired camera frame is accepted.
"""
from collections import deque

class RollingFrameRate:
    WINDOW = 2.0
    def __init__(self):
        self.timestamps = deque()
    def reset(self):
        self.timestamps.clear()
    def accept(self, now):
        if self.timestamps and now-self.timestamps[-1] >= self.WINDOW:
            self.reset()
        self.timestamps.append(now)
        self._trim(now)
    def _trim(self, now):
        while self.timestamps and now-self.timestamps[0] > self.WINDOW:
            self.timestamps.popleft()
    def rate(self, now, camera_fps=0.0, encode_fps=0.0):
        self._trim(now)
        if not self.timestamps or now-self.timestamps[-1] > self.WINDOW:
            return 0.0
        elapsed = max(1.0, min(self.WINDOW, now-self.timestamps[0]))
        measured = len(self.timestamps)/elapsed
        # Never claim more distinct received camera frames than the source
        # captures and the local encoder produces.  A zero source FPS means
        # unknown, not a zero-rate assertion.
        for limit in (camera_fps, encode_fps):
            if limit and limit > 0:
                measured = min(measured, float(limit))
        return round(max(0.0, measured), 1)

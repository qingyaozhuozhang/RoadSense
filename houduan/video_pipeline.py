"""Latest-frame-only video exchange and optional direct-LAN MJPEG preview.

The video producer must never wait for HTTP network or ONNX inference.
Direct preview serves exactly the latest JPEG; slow viewers don't accumulate latency.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class LatestJpeg:
    def __init__(self):
        self.condition = threading.Condition()
        self.jpeg = None
        self.sequence = 0
        self.updated_at = 0.0
        self.render_fps = 0.0
        self._last_frame = 0.0

    def put(self, jpeg):
        if not jpeg or not jpeg.startswith(b'\xff\xd8'):
            return False
        now = time.monotonic()
        with self.condition:
            if self._last_frame:
                dt = now - self._last_frame
                if dt > 0:
                    fps = min(120., 1 / dt)
                    self.render_fps = fps if not self.render_fps else .8 * self.render_fps + .2 * fps
            self._last_frame = now
            self.jpeg = jpeg
            self.sequence += 1
            self.updated_at = now
            self.condition.notify_all()
        return True

    def wait_after(self, previous, timeout=2.0):
        with self.condition:
            self.condition.wait_for(lambda: self.sequence != previous, timeout=timeout)
            return self.sequence, self.jpeg, self.updated_at

    def info(self):
        with self.condition:
            return {'frames_encoded': self.sequence,
                    'render_fps': round(self.render_fps, 1),
                    'last_frame_age': round(time.monotonic()-self.updated_at, 2) if self.updated_at else None}


def make_preview_server(hub, bind_host='127.0.0.1', port=8091):
    """Create local preview server; caller runs serve_forever in a daemon thread.

    The bind is loopback by default. Bind to 0.0.0.0 only on a trusted LAN.
    """
    class PreviewHandler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, fmt, *args):
            return
        def do_GET(self):
            if self.path.split('?')[0] == '/health':
                raw = json.dumps({'status': 'ok', **hub.info()}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                self.wfile.write(raw)
                return
            if self.path.split('?')[0] != '/live.mjpg':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control','no-store, no-cache, must-revalidate')
            self.send_header('Pragma','no-cache')
            self.send_header('X-Accel-Buffering','no')
            self.end_headers()
            previous = -1
            try:
                while True:
                    seq, jpeg, frame_at = hub.wait_after(previous, timeout=2)
                    if not jpeg or seq == previous or time.monotonic() - frame_at > 2:
                        continue
                    previous = seq
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                     + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n')
                    self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError, OSError):
                return
    server = ThreadingHTTPServer((bind_host, int(port)), PreviewHandler)
    server.daemon_threads = True
    return server

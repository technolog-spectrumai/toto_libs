# app/utils/log_sink.py
import threading
import time
import os, sys

class BufferedFileLogSink:
    def __init__(self, file_path, stream_name, original_stream, flush_interval=5):
        self.file_path = file_path
        self.stream_name = stream_name
        self.original_stream = original_stream
        self.buffer = []
        self.lock = threading.Lock()
        self.flush_interval = flush_interval
        self._start_flusher()

    def write(self, message):
        message = message.strip()
        if message:
            self.original_stream.write(message + '\n')
            with self.lock:
                self.buffer.append(f"[{self.stream_name.upper()}] {message}")

    def flush(self):
        self.original_stream.flush()

    def _start_flusher(self):
        def flush_loop():
            while True:
                time.sleep(self.flush_interval)
                self._flush_buffer()

        thread = threading.Thread(target=flush_loop, daemon=True)
        thread.start()

    def _flush_buffer(self):
        with self.lock:
            if self.buffer:
                with open(self.file_path, 'a', encoding='utf-8') as f:
                    f.write('\n'.join(self.buffer) + '\n')
                self.buffer.clear()


    @staticmethod
    def create(app_name):
        log_dir = os.path.join(os.path.dirname(__file__), '../../logs')
        os.makedirs(log_dir, exist_ok=True)

        log_file_path = os.path.join(log_dir, f'{app_name}.log')

        sys.stdout = BufferedFileLogSink(log_file_path, 'stdout', sys.__stdout__)
        sys.stderr = BufferedFileLogSink(log_file_path, 'stderr', sys.__stderr__)

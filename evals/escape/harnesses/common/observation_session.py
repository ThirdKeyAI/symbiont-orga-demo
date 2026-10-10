"""Serial protected observation of every gated worker in one controller run."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
from pathlib import Path

from harnesses.common.confined import _remove_container
from harnesses.common.protected_trace import ProtectedTrace


class ObservationSession:
    def __init__(self, *, image, label, directory, timeout=30):
        self.image, self.label, self.directory = image, label, directory
        self.timeout = timeout
        self.receipts = []
        self.error = None
        self.stop = threading.Event()

    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="matched-observation-")
        self.control_dir = Path(self.temporary.name)
        self.control_dir.chmod(0o755)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.control_dir / "gate.sock"))
        (self.control_dir / "gate.sock").chmod(0o666)
        self.server.settimeout(0.2)
        self.server.listen(4)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return self

    def _serve(self):
        try:
            while not self.stop.is_set():
                try:
                    connection, _ = self.server.accept()
                except TimeoutError:
                    continue
                index = len(self.receipts)
                if index >= 8:
                    connection.close()
                    raise RuntimeError("observation session exceeded eight workers")
                trace = self.directory / f"worker-{index}.strace"
                with connection, ProtectedTrace(image=self.image, trace_path=trace,
                        worker_uid=65534, worker_gid=65534, worker_label=self.label) as observer:
                    try:
                        observer.attach_and_release(connection=connection)
                        observer.finish_observation(self.timeout)
                    except BaseException:
                        if observer.receipt.get("worker_container"):
                            _remove_container(observer.receipt["worker_container"])
                        raise
                    finally:
                        if hasattr(observer, "worker_inspect"):
                            (self.directory / f"worker-{index}.docker.json").write_text(
                                json.dumps(observer.worker_inspect, indent=2) + "\n")
                self.receipts.append(json.loads(trace.with_suffix(".observer.json").read_text()))
        except BaseException as error:
            self.error = error

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(self.timeout + 20)
        if self.thread.is_alive():
            raise RuntimeError("observation session failed to stop")
        self.server.close()
        self.temporary.cleanup()
        if self.error:
            raise RuntimeError("observation session failed") from self.error

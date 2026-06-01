import json

import zmq


class KernelClient:
    """
    Talks to KernelServer (in mandragora/kernel_server.py)
    over ZeroMQ REQ-REP.
    """

    def __init__(self, addr: str = "tcp://127.0.0.1:5555"):
        self.addr = addr
        self.ctx = zmq.Context()

    def _send(self, payload: dict, timeout_ms: int = 15_000) -> dict:
        socket = self.ctx.socket(zmq.REQ)
        socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        socket.connect(self.addr)
        socket.send_string(json.dumps(payload))
        try:
            return json.loads(socket.recv_string())
        except zmq.Again:
            return {"error": "kernel_server_timeout"}
        except Exception as exc:
            return {"error": f"kernel_client_error: {exc}"}
        finally:
            socket.close(linger=0)

    def start(self, notebook_id, config=None, startup_timeout_ms: int = 120_000) -> dict:
        return self._send(
            {"action": "start", "notebook_id": notebook_id, "config": config or {}},
            timeout_ms=startup_timeout_ms,
        )

    def stop(self, notebook_id) -> dict:
        return self._send({"action": "stop", "notebook_id": notebook_id})

    def execute(self, notebook_id, code) -> dict:
        return self._send({"action": "execute", "notebook_id": notebook_id, "code": code})

    def status(self, notebook_id) -> dict:
        return self._send({"action": "status", "notebook_id": notebook_id})

    def kill(self, notebook_id=None) -> dict:
        payload = {"action": "kill"}
        if notebook_id is not None:
            payload["notebook_id"] = notebook_id
        return self._send(payload)

    def ocr(self, image_path: str, lang: str = "eng") -> dict:
        return self._send({"action": "ocr", "image_path": image_path, "lang": lang})

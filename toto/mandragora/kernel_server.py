import json
import os
import uuid

import zmq
from jupyter_client import KernelManager


class NotebookKernel:
    def __init__(self, config=None):
        config = config or {}

        self.kernel_name = config.get("kernel_name", "python3")
        self.kernel_id = config.get("kernel_id", str(uuid.uuid4()))
        self.timeout = config.get("timeout_ms", 30000) / 1000.0
        self.env = config.get("env", {})

        for key, value in self.env.items():
            os.environ[key] = str(value)

        try:
            self.km = KernelManager(kernel_name=self.kernel_name)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot create KernelManager for '{self.kernel_name}'. "
                "Make sure ipykernel is installed in this environment "
                f"(pip install ipykernel). Detail: {exc}"
            ) from exc

        try:
            self.km.start_kernel(
                env={**os.environ, **{k: str(v) for k, v in self.env.items()}}
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to start '{self.kernel_name}' kernel process. "
                f"Detail: {exc}"
            ) from exc

        self.kc = self.km.client()
        self.kc.start_channels()
        try:
            self.kc.wait_for_ready(timeout=60)
        except Exception as exc:
            self.km.shutdown_kernel(now=True)
            raise RuntimeError(
                f"Kernel process started but never became ready within 60 s. "
                f"Detail: {exc}"
            ) from exc

        for dep in config.get("dependencies", []):
            if isinstance(dep, dict):
                specifier = dep["package_name"] + (dep.get("version_spec") or "")
            else:
                specifier = dep
            self.execute(f"!pip install {specifier}")

        self.execute(
            "try:\n"
            "    from toto.core.file_client import FileClient as _FileClient\n"
            "    files = _FileClient()\n"
            "except Exception:\n"
            "    pass\n"
        )

        vault_files_map = config.get("vault_files", {})
        if vault_files_map:
            items = ", ".join(f"{repr(k)}: {repr(v)}" for k, v in vault_files_map.items())
            self.execute(f"vault_files = {{{items}}}")

    def execute(self, code, timeout=None):
        timeout = timeout or self.timeout

        self.kc.execute(code)

        stdout = ""
        stderr = ""
        rich_output = []
        execution_count = None

        try:
            reply = self.kc.get_shell_msg(timeout=timeout)
            execution_count = reply["content"].get("execution_count")
        except Exception as e:
            stderr += f"Shell timeout: {e}\n"

        while True:
            try:
                msg = self.kc.get_iopub_msg(timeout=timeout)
            except Exception:
                break

            msg_type = msg["header"]["msg_type"]
            content = msg["content"]

            if msg_type == "stream":
                if content["name"] == "stdout":
                    stdout += content["text"]
                elif content["name"] == "stderr":
                    stderr += content["text"]
            elif msg_type == "execute_result":
                rich_output.append({"type": "execute_result", "data": content["data"]})
            elif msg_type == "display_data":
                rich_output.append({"type": "display_data", "data": content["data"]})
            elif msg_type == "error":
                stderr += "\n".join(content["traceback"])
            elif msg_type == "status" and content["execution_state"] == "idle":
                break

        return {
            "stdout": stdout,
            "stderr": stderr,
            "execution_count": execution_count,
            "rich_output": rich_output,
        }

    def shutdown(self):
        self.kc.stop_channels()
        self.km.shutdown_kernel()


class KernelServer:
    def __init__(self, bind_addr: str = "tcp://0.0.0.0:5555"):
        self.bind_addr = bind_addr
        self.kernels: dict = {}

    def run(self):
        ctx = zmq.Context()
        socket = ctx.socket(zmq.REP)
        socket.bind(self.bind_addr)
        print(f"[KernelServer] Listening on {self.bind_addr}", flush=True)

        try:
            while True:
                try:
                    message = json.loads(socket.recv_string())
                    response = self._handle(message)
                    socket.send_string(json.dumps(response))
                except Exception as exc:
                    socket.send_string(json.dumps({"error": str(exc)}))
        finally:
            self._shutdown_all()
            socket.close(linger=0)
            ctx.term()

    def _handle(self, message: dict) -> dict:
        action = message["action"]
        notebook_id = message.get("notebook_id")

        if action == "start":
            config = message.get("config", {})
            try:
                self.kernels[notebook_id] = NotebookKernel(config)
                return {"state": "started"}
            except Exception as e:
                return {"error": str(e)}

        if action == "stop":
            kernel = self.kernels.pop(notebook_id, None)
            if kernel:
                kernel.shutdown()
            return {"state": "stopped"}

        if action == "execute":
            kernel = self.kernels.get(notebook_id)
            if not kernel:
                return {"error": "kernel_not_running"}
            return kernel.execute(message["code"])

        if action == "status":
            return {"running": notebook_id in self.kernels}

        if action == "kill":
            if notebook_id is None:
                self._shutdown_all()
                return {"state": "all_killed"}
            kernel = self.kernels.pop(notebook_id, None)
            if kernel:
                kernel.shutdown()
            return {"state": "killed"}

        if action == "ocr":
            return self._handle_ocr(message)

        return {"error": "unknown_action", "action": action}

    def _handle_ocr(self, message: dict) -> dict:
        try:
            import pytesseract
            from PIL import Image
            image_path = message["image_path"]
            lang = message.get("lang", "eng")
            img = Image.open(image_path)
            data = pytesseract.image_to_data(
                img, lang=lang, output_type=pytesseract.Output.DICT
            )
            lines = [
                {
                    "text": text,
                    "left": data["left"][i],
                    "top": data["top"][i],
                    "width": data["width"][i],
                    "height": data["height"][i],
                    "confidence": data["conf"][i],
                }
                for i, text in enumerate(data["text"])
                if text.strip()
            ]
            return {"lines": lines}
        except Exception as e:
            return {"error": str(e)}

    def _shutdown_all(self):
        for kernel in self.kernels.values():
            try:
                kernel.shutdown()
            except Exception:
                pass
        self.kernels.clear()

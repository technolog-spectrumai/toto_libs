import net from "node:net";
import { spawn } from "node:child_process";

const host = "127.0.0.1";
const port = 1420;

function isPortOpen() {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });

    socket.once("connect", () => {
      socket.end();
      resolve(true);
    });

    socket.once("error", () => {
      socket.destroy();
      resolve(false);
    });

    socket.setTimeout(500, () => {
      socket.destroy();
      resolve(false);
    });
  });
}

if (await isPortOpen()) {
  console.log(`[tauri-dev] Reusing existing Vite server on http://localhost:${port}`);
  process.exit(0);
}

const vite = spawn("npm", ["run", "dev"], {
  stdio: "inherit",
  shell: process.platform === "win32",
});

vite.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }

  process.exit(code ?? 0);
});

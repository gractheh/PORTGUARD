"""
PORTGUARD - start the API server and open the demo in a browser.
Usage: python run_demo.py
"""

import subprocess
import sys
import time
import webbrowser
from urllib.request import urlopen
from urllib.error import URLError

HOST = "127.0.0.1"
PORT = 8000
DEMO_URL = f"http://{HOST}:{PORT}/demo"
HEALTH_URL = f"http://{HOST}:{PORT}/api/v1/health"
POLL_INTERVAL = 0.25   # seconds between readiness checks
TIMEOUT = 15           # seconds to wait before giving up


def wait_for_server() -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        try:
            urlopen(HEALTH_URL, timeout=1)
            return True
        except (URLError, OSError):
            time.sleep(POLL_INTERVAL)
    return False


def main() -> int:
    print("Starting PORTGUARD server...")

    import tempfile, os
    log_fd, log_path = tempfile.mkstemp(prefix="portguard_", suffix=".log")
    log_file = os.fdopen(log_fd, "w")

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "api.app:app",
            "--host", HOST,
            "--port", str(PORT),
            "--reload",
        ],
        stdout=log_file,
        stderr=log_file,
    )

    try:
        if not wait_for_server():
            log_file.flush()
            print(f"[ERROR] Server did not start within {TIMEOUT}s.")
            try:
                with open(log_path) as f:
                    tail = f.read()[-2000:]
                if tail.strip():
                    print("\n--- Server log (last 2000 chars) ---")
                    print(tail)
                    print("--- end ---")
            except OSError:
                pass
            proc.terminate()
            return 1

        print(f"Server ready at http://{HOST}:{PORT}")
        print(f"Opening demo at {DEMO_URL}")
        print("Press Ctrl+C to stop.\n")
        webbrowser.open(DEMO_URL)

        proc.wait()

    except KeyboardInterrupt:
        print("\nShutting down...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        log_file.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def run_cmd(cmd, cwd=None, env=None):
    print("$", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=cwd, env=env or os.environ.copy())


def ensure_backend_deps():
    req = BACKEND / "requirements.txt"
    if not req.exists():
        return
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req)])


def start_api(dev: bool = False):
    args = [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
    if dev:
        args.append("--reload")
    return run_cmd(args, cwd=str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Run WG Accounting locally")
    parser.add_argument("--dev", action="store_true", help="Run in development mode (Uvicorn reload + Vite)")
    args = parser.parse_args()

    ensure_backend_deps()

    procs = []
    try:
        if args.dev:
            # start Vite if frontend exists
            if FRONTEND.exists() and (FRONTEND / "package.json").exists():
                # install deps if missing
                node_modules = FRONTEND / "node_modules"
                if not node_modules.exists():
                    subprocess.check_call(["npm", "install"], cwd=str(FRONTEND))
                procs.append(run_cmd(["npm", "run", "dev"], cwd=str(FRONTEND)))
            procs.append(start_api(dev=True))
        else:
            procs.append(start_api(dev=False))
        print("Running. API: http://localhost:8000")
        if args.dev and FRONTEND.exists():
            print("Frontend (Vite): http://localhost:5173")
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()



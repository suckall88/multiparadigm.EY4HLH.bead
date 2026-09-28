"""Launch the backend, frontend, and maintenance scheduler together for local use.

Usage: python start.py
Stop with Ctrl+C; all three child processes are terminated cleanly.

This script only starts processes - it never drops, recreates, or otherwise
touches the database.

Ez a rendszerindító szkript (Section 5.3 elvárása: kell egy start
script, ami a teljes rendszert — frontend+backend+maintenance —
elindítja, nem csak a backendet). Mindhárom komponens KÜLÖN Python
folyamatként indul, egymástól függetlenül futnak (ahogy a beadandó
elvárja: a frontend és backend önállóan indítható programok).
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

# Mindhárom parancs egy-egy önálló, a fejlesztői gépen is futtatható
# folyamatot indít: a FastAPI backendet (uvicorn), a Streamlit
# frontendet, és a maintenance ütemezőt (ami saját magában egy
# `while True` ciklusban fut, lásd maintenance/scheduler.py).
COMMANDS: list[list[str]] = [
    [sys.executable, "-m", "uvicorn", "backend.main:app", "--reload"],
    [sys.executable, "-m", "streamlit", "run", "frontend/app.py"],
    [sys.executable, "-m", "maintenance.scheduler"],
]


def main() -> None:
    # subprocess.Popen: elindítja a folyamatot, de NEM várja meg a
    # befejezését — így mindhárom egyszerre, párhuzamosan tud futni.
    processes = [subprocess.Popen(cmd) for cmd in COMMANDS]
    print(f"Started {len(processes)} processes (backend, frontend, maintenance scheduler).")
    print("Press Ctrl+C to stop everything.")

    def shutdown(*_args: object) -> None:
        """Ctrl+C (SIGINT) vagy leállítási kérés (SIGTERM) esetén
        mindhárom alfolyamatnak jelzi, hogy álljon le (`terminate`),
        megvárja őket max. 10 másodpercig, majd ha valamelyik nem
        reagálna, kényszerrel kilövi (`kill`) — így sosem marad
        "elárvult" háttérfolyamat a gépen."""
        print("\nStopping all processes...")
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    # Folyamatosan figyeli, hogy valamelyik alfolyamat esetleg magától
    # leállt-e (pl. hiba miatt) — ha igen, mindenki mást is leállít,
    # hogy ne maradjon a rendszer félig futó állapotban.
    while True:
        for proc in processes:
            if proc.poll() is not None:
                print(f"Process {proc.args} exited with code {proc.returncode}; stopping the rest.")
                shutdown()
        time.sleep(1)


if __name__ == "__main__":
    main()

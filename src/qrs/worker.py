"""Worker process for one run or analysis: python -m qrs.worker <job_dir> <data_dir>

Executes the run's compiled strategy.py, reading no data on or after the date in until.txt if the run
has one, and writes results and record.json into run_dir. On failure
it writes the error to error.txt (the traceback goes to the log) and exits with status 1. A process
per run isolates crashes and makes cancellation a matter of terminating it; it is not a security
sandbox.

The service keeps the worker's stdin open as a pipe. End of input means the service has gone, so
the worker exits instead of running on as an orphan."""

import os
import sys
import threading
import traceback
from pathlib import Path

from qrs.runtime import execute, frozen_until


def _exit_when_service_exits() -> None:
    # Read the file descriptor directly: a daemon thread blocked in buffered sys.stdin makes
    # interpreter shutdown abort.
    while os.read(0, 1024):
        pass
    os._exit(1)


def main(argv: list[str]) -> int:
    threading.Thread(target=_exit_when_service_exits, daemon=True).start()
    run_dir, data_dir = Path(argv[0]), Path(argv[1])
    try:
        if (run_dir / "analysis.json").exists():
            from qrs.analysis import execute as analyse  # only analyses load methods, and SciPy with them

            record = analyse(run_dir / "analysis.json", data_dir, run_dir)
        else:
            record = execute(run_dir / "strategy.py", data_dir, run_dir, frozen_until(run_dir))
    except Exception as e:
        traceback.print_exc()
        (run_dir / "error.txt").write_text(str(e))
        return 1
    print(f"Complete. result_sha256={record['result_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

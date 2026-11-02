"""AgroSense Café — IoT Simulator CLI Convenience Runner."""

import sys
from pathlib import Path

# Add src to sys.path so direct execution works without installation
src_path = Path(__file__).resolve().parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from agrosense_simulators.main import main  # noqa: E402

if __name__ == "__main__":
    main()

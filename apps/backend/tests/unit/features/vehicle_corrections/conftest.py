import sys
from pathlib import Path

# The test folders are not packages; make the shared builders importable.
sys.path.insert(0, str(Path(__file__).parent))

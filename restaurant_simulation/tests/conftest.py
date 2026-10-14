import sys
from pathlib import Path

# The modules use flat imports (from models import ...), so put the
# project folder on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

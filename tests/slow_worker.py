"""Test fixture: emulate a blocked hydration without accessing Box."""
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from thumbnail_worker import generate

if __name__ == '__main__':
    if Path(sys.argv[1]).name.startswith('slow'):
        time.sleep(20)
    generate(*sys.argv[1:4])

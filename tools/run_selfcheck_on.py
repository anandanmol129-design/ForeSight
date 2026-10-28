"""Run selfcheck.py against another data dir, with audit/cache redirected (never touches the
project's audit/ or .cache/). usage: python run_selfcheck_on.py <data dir> <scratch dir>"""
import runpy, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config
config.use_record(str(Path(sys.argv[1]).resolve()))   # also reads record.json
config.AUDIT_DIR = Path(sys.argv[2]) / "audit"; config.AUDIT_DIR.mkdir(parents=True, exist_ok=True)
config.CACHE_DIR = Path(sys.argv[2]) / "cache"
sys.argv = ["selfcheck.py"]
runpy.run_path(str(ROOT / "selfcheck.py"), run_name="__main__")

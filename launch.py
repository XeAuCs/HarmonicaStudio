"""Desktop launcher and packaged application smoke-test entry point."""
from pathlib import Path
import sys
if not getattr(sys,'frozen',False):
    sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--diagnose-worker':
        from harmonica_studio.performance import worker_main
        raise SystemExit(worker_main())
    if len(sys.argv)>1 and sys.argv[1]=='--self-test':
        from harmonica_studio.diagnostics import self_test
        raise SystemExit(self_test(Path(sys.argv[2])))
    from harmonica_studio.__main__ import main
    raise SystemExit(main())

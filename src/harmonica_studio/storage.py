from dataclasses import asdict
from pathlib import Path
import json, logging
from logging.handlers import RotatingFileHandler
from .models import Options


def configure_logging(root):
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(root/'studio.log',maxBytes=1_000_000,backupCount=3,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def load_options(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value,dict):
            return Options()
        allowed={k:v for k,v in value.items() if k in asdict(Options()) and k not in ('track','channel')}
        return Options(**allowed).validate()
    except (OSError,ValueError,TypeError):
        return Options()


def save_options(path,options):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    value=asdict(options)
    value.update(track=None,channel=None)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

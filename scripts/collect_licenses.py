"""Preserve installed dependency notices in the portable distribution."""
from importlib.metadata import distribution
from pathlib import Path
import shutil,sys

root=Path(__file__).resolve().parents[1]/'third_party/licenses'
root.mkdir(parents=True,exist_ok=True)
for name in ('PySide6-Essentials','shiboken6','pyinstaller','segno'):
    package=distribution(name)
    destination=root/name
    destination.mkdir(exist_ok=True)
    (destination/'METADATA.txt').write_text(package.read_text('METADATA') or '',encoding='utf-8')
    for entry in package.files or ():
        if any(word in str(entry).lower() for word in ('license','copying')) and entry.suffix.lower() in ('.txt','.md',''):
            source=Path(package.locate_file(entry))
            if source.is_file():shutil.copy2(source,destination/source.name)
for prefix in (sys.base_prefix,sys.prefix):
    source=Path(prefix)/'LICENSE.txt'
    if source.is_file():
        shutil.copy2(source,root/'Python-LICENSE.txt');break

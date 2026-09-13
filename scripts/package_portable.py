"""Assemble/update a portable release while preserving its user-owned files."""
import argparse
from pathlib import Path
import shutil
import tempfile
import uuid


RUNTIME_NAMES = {'HarmonicaStudio.exe', '_internal', '使用说明.txt'}


def reject_links(folder):
    for item in (folder, *folder.rglob('*')):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError(f'发布目录不能包含文件系统链接：{item}')


def remove_owned(folder, parent, prefix):
    folder, parent = folder.resolve(), parent.resolve()
    if folder.parent != parent or not folder.name.startswith(prefix):
        raise ValueError('拒绝清理非本次更新的目录。')
    reject_links(folder)
    shutil.rmtree(folder)


def prepare(source, samples):
    source, samples = Path(source).resolve(), Path(samples).resolve()
    if not (source / 'HarmonicaStudio.exe').is_file() or not (source / '_internal').is_dir():
        raise ValueError('打包产物不完整。')
    reject_links(source)
    reject_links(samples)
    shutil.copytree(samples, source / 'samples')
    (source / 'data').mkdir()
    shutil.copy2(Path(__file__).resolve().parents[1] / 'docs/portable-use.txt', source / '使用说明.txt')


def install(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.is_symlink() or (hasattr(destination, 'is_junction') and destination.is_junction()):
        raise ValueError('安装目标不能是文件系统链接。')
    destination = destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('暂存目录和安装目录必须相互独立。')
    for name in ('HarmonicaStudio.exe', '_internal', 'samples', 'data'):
        if not (source / name).exists():
            raise ValueError(f'便携版缺少 {name}。')
    reject_links(source)
    if destination.exists():
        reject_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.portable-install-', dir=destination.parent)).resolve()
    backup = destination.with_name('.portable-backup-' + uuid.uuid4().hex)
    moved_old = False
    try:
        # Runtime is new; every existing non-runtime entry is user-owned.
        for item in source.iterdir():
            if item.name not in RUNTIME_NAMES and (destination / item.name).exists():
                continue
            target = staging / item.name
            shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)
        if destination.exists():
            for item in destination.iterdir():
                if item.name in RUNTIME_NAMES:
                    continue
                target = staging / item.name
                shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)
            # All paths are resolved siblings under the explicit install parent.
            assert backup.parent == destination.parent and backup.name.startswith('.portable-backup-')
            destination.rename(backup)
            moved_old = True
        try:
            staging.rename(destination)
        except BaseException:
            if moved_old:
                backup.rename(destination)
            raise
        if moved_old:
            remove_owned(backup, destination.parent, '.portable-backup-')
    finally:
        if staging.exists():
            remove_owned(staging, destination.parent, '.portable-install-')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('prepare', 'install'))
    parser.add_argument('source')
    parser.add_argument('target')
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.source, args.target)
    else:
        install(args.source, args.target)

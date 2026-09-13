"""Update the four current-version declarations, leaving historical reports alone."""
from pathlib import Path
import re
import sys


def set_version(root, version):
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version):
        raise ValueError('版本号应为三段数字，例如 1.4.7。')
    patterns = {
        'src/harmonica_studio/__init__.py': rb"(?m)^(__version__ = ')[^']+(')",
        'pyproject.toml': rb'(?m)^(version = ")[^"]+(")',
        'README.md': '当前版本：\\*\\*[^*]+\\*\\*'.encode(),
        '快速开始.txt': rb'(?m)^(.*Harmonica Studio )\d+\.\d+\.\d+(\r?$)',
    }
    changes = []
    for name, pattern in patterns.items():
        path = Path(root) / name
        original = path.read_bytes()
        def replacement(match):
            if name == 'README.md':
                return ('当前版本：**' + version + '**').encode()
            return match[1] + version.encode('ascii') + match[2]
        updated, count = re.subn(pattern, replacement, original)
        if count != 1:
            raise ValueError(f'{name} 的版本声明不唯一，未修改任何文件。')
        changes.append((path, original, updated))
    written = []
    try:
        for path, original, updated in changes:
            written.append((path, original))
            path.write_bytes(updated)
    except OSError:
        for path, original in reversed(written):
            path.write_bytes(original)
        raise


if __name__ == '__main__':
    set_version(Path(__file__).resolve().parents[1], sys.argv[1])
    print('版本已统一为 ' + sys.argv[1])

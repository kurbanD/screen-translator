"""Build either distribution, then validate the frozen payload."""
import argparse
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('edition', choices=['lite', 'full'])
    edition = parser.parse_args().edition
    if sys.platform != 'win32' or sys.maxsize <= 2**32:
        raise SystemExit('Use 64-bit Python on Windows.')
    os.chdir(ROOT)
    name = 'ScreenTranslator-' + edition.capitalize()
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed', '--name', name, '--icon', 'ScreenTranslator.ico', '--version-file', 'version_info_' + edition + '.txt']
    if edition == 'full':
        subprocess.run([sys.executable, 'prepare_ocr.py'], check=True)
        args.extend(['--add-data', 'tesseract;tesseract'])
    subprocess.run([sys.executable, '-m', 'py_compile', 'translator.py'], check=True)
    subprocess.run(args + ['translator.py'], check=True)
    exe = ROOT / 'dist' / (name + '.exe')
    subprocess.run([str(exe), '--self-test-' + edition], check=True, timeout=180)
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', ROOT / 'dist' / 'THIRD_PARTY_NOTICES.md')
    print('Build verified:', exe, f'({exe.stat().st_size / 1024**2:.1f} MiB)', flush=True)


if __name__ == '__main__':
    main()

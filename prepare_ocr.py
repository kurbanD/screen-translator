"""Prepare a portable OCR engine for the Full Windows x64 build (no installation)."""
import concurrent.futures
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
DEST = ROOT / 'tesseract'
VERSION = '5.5.3'
INSTALLER = ('https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/'
             'tesseract-ocr-w64-setup-5.5.3.20260724.exe')
SHA256 = 'bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4'
MODELS_URL = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/'
LANGUAGES = 'rus eng ara deu fra spa ita por tur ukr bel kaz aze hin chi_sim chi_tra jpn kor fas heb pol nld ces vie'.split()


def fetch(url, dest, minimum=1, checksum=None):
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + '.part')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ScreenTranslator-build/2.0'}), timeout=90) as response, part.open('wb') as out:
                shutil.copyfileobj(response, out)
            if part.stat().st_size < minimum:
                raise RuntimeError('Incomplete download: ' + url)
            if checksum and hashlib.sha256(part.read_bytes()).hexdigest() != checksum:
                raise RuntimeError('SHA-256 mismatch: ' + url)
            part.replace(dest)
            return
        except Exception:
            part.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def seven_zip():
    for candidate in (shutil.which('7z.exe'), shutil.which('7z'),
                      str(Path(os.environ.get('ProgramW6432', r'C:\Program Files')) / '7-Zip' / '7z.exe'),
                      str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / '7-Zip' / '7z.exe')):
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError('7-Zip not found. Install 64-bit 7-Zip from https://www.7-zip.org/ and retry, or use GitHub Actions.')


def check(directory):
    exe = directory / 'tesseract.exe'
    data = directory / 'tessdata'
    version = subprocess.check_output([str(exe), '--version'], cwd=directory, text=True, errors='replace', timeout=30)
        actual = version.splitlines()[0].strip().removeprefix('tesseract ').removeprefix('v')
    if actual not in (VERSION, VERSION + '.20260724'):
        raise RuntimeError('Unexpected Tesseract version: ' + version)
    langs = subprocess.check_output([str(exe), '--list-langs', '--tessdata-dir', str(data)], cwd=directory, text=True, errors='replace', timeout=30)
    missing = set(LANGUAGES) - set(langs.splitlines())
    if missing:
        raise RuntimeError('Missing OCR models: ' + ', '.join(sorted(missing)))
    # Load every model on a tiny valid PGM, so damaged files fail the build.
    with tempfile.TemporaryDirectory() as tmp:
        pgm = Path(tmp) / 'test.pgm'
        pgm.write_bytes(b'P5\n64 64\n255\n' + b'\xff' * 4096)
        for lang in LANGUAGES:
            subprocess.run([str(exe), str(pgm), 'stdout', '--tessdata-dir', str(data), '-l', lang, '--psm', '6'], cwd=directory, capture_output=True, check=True, timeout=40)
    print(f'Tesseract {VERSION}: {len(LANGUAGES)} models checked.', flush=True)


def main():
    if sys.platform != 'win32':
        raise RuntimeError('Full Windows build requires Windows.')
    stamp = DEST / 'build-source.txt'
    signature = VERSION + '\n' + SHA256 + '\n' + ' '.join(LANGUAGES) + '\n'
    if stamp.is_file() and stamp.read_text(encoding='utf-8') == signature:
        try:
            check(DEST)
            return
        except Exception as exc:
            print('Rebuilding OCR cache:', exc, flush=True)
    with tempfile.TemporaryDirectory(prefix='ocr-build-', dir=ROOT) as tmp:
        tmp = Path(tmp)
        installer = tmp / 'installer.exe'
        extracted = tmp / 'extracted'
        staged = tmp / 'portable'
        staged.mkdir()
        print('Downloading verified Tesseract installer...', flush=True)
        seven = seven_zip()
        fetch(INSTALLER, installer, minimum=1000000, checksum=SHA256)
        subprocess.run([seven, 'x', '-y', '-o' + str(extracted), str(installer)], check=True, timeout=180)
        candidates = [p for p in extracted.rglob('tesseract.exe') if not any(x.startswith('$') for x in p.relative_to(extracted).parts)]
        if len(candidates) != 1:
            raise RuntimeError('Cannot locate Tesseract in installer extraction.')
        origin = candidates[0].parent
        shutil.copy2(candidates[0], staged / 'tesseract.exe')
        for dll in origin.glob('*.dll'):
            shutil.copy2(dll, staged / dll.name)
        for name in ('doc', 'tessdata'):
            if (origin / name).is_dir():
                shutil.copytree(origin / name, staged / name)
        data = staged / 'tessdata'
        data.mkdir(exist_ok=True)
        # Models are acquired independently from the pinned upstream release.
        print('Downloading 24 OCR models...', flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda lang: fetch(MODELS_URL + lang + '.traineddata', data / (lang + '.traineddata'), minimum=10000), LANGUAGES))
        doc = staged / 'doc'
        doc.mkdir(exist_ok=True)
        fetch('https://raw.githubusercontent.com/tesseract-ocr/tesseract/5.5.3/LICENSE', doc / 'Tesseract-LICENSE.txt', minimum=1000)
        fetch(MODELS_URL + 'LICENSE', doc / 'tessdata_fast-LICENSE.txt', minimum=1000)
        shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', doc / 'THIRD_PARTY_NOTICES.md')
        check(staged)
        (staged / 'build-source.txt').write_text(signature, encoding='utf-8')
        if DEST.exists():
            shutil.rmtree(DEST)
        shutil.move(str(staged), str(DEST))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('OCR preparation failed:', exc, file=sys.stderr)
        sys.exit(1)

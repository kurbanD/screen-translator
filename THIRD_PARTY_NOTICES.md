# Third-party components in the Full build

The Full build redistributes Tesseract OCR 5.5.3 and its upstream Windows runtime DLLs, extracted from the unmodified x64 installer published by the Tesseract project. The upstream installer is not executed. Tesseract and tessdata_fast are Apache-2.0 licensed. Their license texts and any upstream documentation copied from the installer are included in `tesseract/doc/` inside the EXE bundle.

- Tesseract sources and release: https://github.com/tesseract-ocr/tesseract/tree/5.5.3
- Windows installer release: https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3
- OCR models (`tessdata_fast`, 4.1.0): https://github.com/tesseract-ocr/tessdata_fast/tree/4.1.0
- Windows build definitions: https://github.com/tesseract-ocr/tesseract/tree/5.5.3/nsis

The upstream runtime DLLs may have additional licenses (including reciprocal source-distribution obligations). Before public redistribution, review the actual DLL set and include corresponding upstream copyright notices, license texts and source offers where required. This notice is not a complete license audit. PyInstaller, Python and Tk also have their own redistribution terms. The app's own license is for the repository owner to select.

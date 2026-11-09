@echo off
chcp 65001 >nul
echo === Сборка ScreenTranslator-Full.exe: со встроенным OCR, Windows-язык не нужен ===
echo.
if not exist translator.pyw goto :nofile

python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :nopip

set "PF=%ProgramFiles%"
if defined ProgramW6432 set "PF=%ProgramW6432%"
set "TESS=%PF%\Tesseract-OCR"

if exist "%TESS%\tesseract.exe" goto :havetess
echo Tesseract не найден, пробую поставить через winget. Подтверди запрос Windows.
winget install --id UB-Mannheim.TesseractOCR -e --silent --accept-package-agreements --accept-source-agreements
if exist "%TESS%\tesseract.exe" goto :havetess
echo.
echo Не получилось поставить Tesseract автоматически.
echo Скачай установщик для Windows: https://github.com/UB-Mannheim/tesseract/wiki
echo Установи в папку по умолчанию и запусти этот файл снова.
pause
exit /b 1

:havetess
echo Копирую Tesseract...
if exist _tess rmdir /s /q _tess
robocopy "%TESS%" _tess /E /NFL /NDL /NJH /NJS /XF unins000.exe unins000.dat >nul
if errorlevel 8 goto :copyfail
if not exist _tess\tessdata mkdir _tess\tessdata

echo Скачиваю данные русского и английского языков...
for %%L in (rus eng) do call :getlang %%L || goto :dlfail

python -m PyInstaller --onefile --noconsole --clean --name ScreenTranslator-Full --add-data "_tess;tesseract" translator.pyw
if errorlevel 1 goto :fail

echo.
echo Готово: dist\ScreenTranslator-Full.exe
echo Он больше, зато работает без языков Windows и без Python.
pause
exit /b 0

:getlang
rem качаем %1.traineddata; если файла нет в ветке main — пробуем master
curl.exe -L --fail -s -o "_tess\tessdata\%1.traineddata" "https://github.com/tesseract-ocr/tessdata_fast/raw/main/%1.traineddata"
if errorlevel 1 curl.exe -L --fail -s -o "_tess\tessdata\%1.traineddata" "https://github.com/tesseract-ocr/tessdata_fast/raw/master/%1.traineddata"
if errorlevel 1 exit /b 1
for %%F in ("_tess\tessdata\%1.traineddata") do if %%~zF LSS 1000000 exit /b 1
exit /b 0

:nofile
echo Положи translator.pyw в эту же папку и запусти снова.
pause
exit /b 1

:nopip
echo Не удалось установить PyInstaller. Проверь, что установлен Python и есть интернет.
pause
exit /b 1

:copyfail
echo Не удалось скопировать Tesseract.
pause
exit /b 1

:dlfail
echo Не удалось скачать данные языков. Проверь интернет и запусти снова.
pause
exit /b 1

:fail
echo Сборка не удалась.
pause
exit /b 1

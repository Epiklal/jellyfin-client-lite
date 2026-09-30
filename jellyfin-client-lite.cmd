@echo off
rem Startet jellyfin-client-lite unter Windows, Doppelklick genuegt.
rem Beim ersten Start richtet es die Python-Umgebung .venv ein.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Erster Start: richte die Python-Umgebung ein, das dauert einen Moment ...
    py -m venv .venv 2>nul || python -m venv .venv || goto fehler
)
.venv\Scripts\python.exe -c "import textual, requests" 2>nul || (
    .venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt || goto fehler
)
.venv\Scripts\python.exe run.py %*
if errorlevel 1 pause
exit /b

:fehler
echo.
echo Einrichtung fehlgeschlagen, siehe Meldung oben.
echo Ist Python installiert? https://www.python.org/downloads/
pause
exit /b 1

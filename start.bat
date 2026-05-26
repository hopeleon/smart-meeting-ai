@echo off
chcp 65001 >nul 2>&1

echo.
echo ===========================================================
echo   Smart Meeting AI - Local Launcher
echo ===========================================================
echo.

:: 1. Check Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Please install Node.js 18+
    echo         Download: https://nodejs.org
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('node -v 2^>^&1') do echo [OK] Node.js %%v

:: 2. Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+
    echo         Download: https://www.python.org
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v

:: 3. Check pip
where pip >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] pip not found
    echo.
    pause
    exit /b 1
)
echo [OK] pip available

:: 4. Read config.json to detect LLM provider
echo.
set "MEETINGSUMMARY_DIR=%~dp0meetingsummary"
set "LLM_PROVIDER="
set "LLM_MODEL="
set "LLM_BASE_URL="

if exist "%MEETINGSUMMARY_DIR%\config.json" (
    echo [INFO] Reading meetingsummary\config.json ...
    for /f "tokens=*" %%v in ('python -c "import json; f=open(r'%MEETINGSUMMARY_DIR%\config.json','r',encoding='utf-8'); d=json.load(f); print(d.get('ollama',{}).get('provider','ollama')); f.close()" 2^>nul') do set "LLM_PROVIDER=%%v"
    for /f "tokens=*" %%v in ('python -c "import json; f=open(r'%MEETINGSUMMARY_DIR%\config.json','r',encoding='utf-8'); d=json.load(f); print(d.get('ollama',{}).get('model','')); f.close()" 2^>nul') do set "LLM_MODEL=%%v"
    for /f "tokens=*" %%v in ('python -c "import json; f=open(r'%MEETINGSUMMARY_DIR%\config.json','r',encoding='utf-8'); d=json.load(f); print(d.get('ollama',{}).get('base_url','')); f.close()" 2^>nul') do set "LLM_BASE_URL=%%v"
) else (
    echo [WARN] config.json not found, using Ollama as default
    if exist "%MEETINGSUMMARY_DIR%\config.example.json" (
        copy "%MEETINGSUMMARY_DIR%\config.example.json" "%MEETINGSUMMARY_DIR%\config.json" >nul 2>&1
        echo        Created config.json from config.example.json
    )
    set "LLM_PROVIDER=ollama"
)

if "%LLM_PROVIDER%"=="" set "LLM_PROVIDER=ollama"

if "%LLM_PROVIDER%"=="ollama" (
    echo        LLM: Ollama (local model)
    where ollama >nul 2>&1
    if %errorlevel% neq 0 (
        echo [WARN] Ollama not installed
        echo         Download: https://ollama.com
        echo         Run: ollama serve
        if not "%LLM_MODEL%"=="" (
            echo         Pull model: ollama pull %LLM_MODEL%
        )
    ) else (
        for /f "tokens=*" %%v in ('ollama --version 2^>^&1') do echo [OK] Ollama %%v installed
        curl -s http://localhost:11434/api/tags >nul 2>&1
        if %errorlevel% neq 0 (
            echo [WARN] Ollama service not running. Run: ollama serve
        ) else (
            echo        Ollama service is running
            if not "%LLM_MODEL%"=="" (
                echo        Model: %LLM_MODEL%
                echo        Pull: ollama pull %LLM_MODEL%
            )
        )
    )
) else (
    echo        LLM: %LLM_PROVIDER% (%LLM_BASE_URL% / %LLM_MODEL%)
)

:: 5. Install backend dependencies
echo.
echo [INFO] Installing backend Python dependencies ...
set "BACKEND_DIR=%~dp0backend"
cd /d "%BACKEND_DIR%"
pip install uvicorn[standard] --quiet --disable-pip-version-check
pip install -r requirements.txt --quiet --disable-pip-version-check
pip install requests --quiet --disable-pip-version-check
echo [OK] Backend dependencies installed

:: 6. Install frontend dependencies
echo.
echo [INFO] Installing frontend npm dependencies ...
set "FRONTEND_DIR=%~dp0frontend"
cd /d "%FRONTEND_DIR%"
if not exist "node_modules" (
    npm install
)
echo [OK] Frontend dependencies installed

:: 7. Start services
echo.
echo ===========================================================
echo   Starting services ...
echo ===========================================================
echo.
echo   Backend API:  http://localhost:8000
echo   API docs:     http://localhost:8000/docs
echo   Frontend:     http://localhost:5173
echo ===========================================================
echo.

:: Start backend with env vars set in the same process
:: (start /k inherits current shell env; setx would persist globally but is async)
cd /d "%BACKEND_DIR%"
start "MeetingAI-Backend" cmd /k "set ENV=local&& set CORS_ORIGINS=["http://localhost","http://localhost:5173","http://127.0.0.1:5173"]&& python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

:: Wait for backend to be ready (poll /health, max 60*3=180 seconds)
echo        Waiting for backend to load models (this may take a few minutes) ...
set "BACKEND_READY=0"
set "WAIT_COUNT=0"
:wait_backend_loop
timeout /t 3 /nobreak >nul
set /a WAIT_COUNT+=1
curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel% equ 0 (
    set "BACKEND_READY=1"
) else (
    if %WAIT_COUNT% lss 60 goto wait_backend_loop
)
if "%BACKEND_READY%"=="1" (
    echo [OK] Backend is ready
    for /f "tokens=*" %%s in ('curl -s http://localhost:8000/health 2^>nul') do echo        Model status: %%s
) else (
    echo [WARN] Backend startup timed out. Check the MeetingAI-Backend window for logs.
)

:: Start frontend
start "MeetingAI-Frontend" cmd /k "cd /d "%FRONTEND_DIR%" && npx vite --host 0.0.0.0 --port 5173"
echo [OK] Frontend started
echo.
echo ===========================================================
echo   All services started!
echo   Open browser: http://localhost:5173
echo ===========================================================
echo.
pause

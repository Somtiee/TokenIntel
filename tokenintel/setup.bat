@echo off
cd /d "%~dp0"

if not exist ".venv" (
    python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if not exist ".env" (
    if exist "..\.env.example" (
        copy ..\.env.example .env
    ) else if exist ".env.example" (
        copy .env.example .env
    )
    echo Created .env - add your API keys before running.
)
if not exist "..\.env" (
    if exist "..\.env.example" (
        copy ..\.env.example ..\.env
        echo Also created D:\TokenIntel\.env from .env.example
    )
)

echo.
echo Setup complete. Run:
echo   .venv\Scripts\activate.bat
echo   python -m streamlit run main.py

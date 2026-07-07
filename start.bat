@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   StockLens 서버 시작
echo ============================================
echo.
echo  이 PC에서 접속:      http://127.0.0.1:8899
echo.
echo  휴대폰/태블릿 접속 주소 (같은 와이파이):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo    http://%%a:8899 (공백 제거)
echo.
echo  종료하려면 이 창을 닫으세요.
echo ============================================
start http://127.0.0.1:8899
python -m uvicorn main:app --host 0.0.0.0 --port 8899
pause

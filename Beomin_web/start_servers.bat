@echo off
REM 안농 - 웹사이트(8000) + 농업뉴스(8001) + 작물점수(8002) 동시 실행
cd /d "%~dp0"
echo [1/3] 웹사이트 서버 시작 (http://localhost:8000)
start "Beomin Web (8000)" cmd /k python -m http.server 8000 --directory "%~dp0"
echo [2/3] 농업 뉴스 서버 시작 (http://localhost:8001)
start "Beomin News (8001)" cmd /k python "%~dp0news_server.py"
echo [3/3] 작물 적합도 점수 서버 시작 (http://localhost:8002)
start "Beomin CropScore (8002)" cmd /k python "%~dp0..\backend\crop_score_server.py"
echo.
echo 세 서버가 각각의 창에서 실행됩니다.
echo 브라우저에서 열기: http://localhost:8000/CropAdvisor.dc.html
echo (창을 닫으면 해당 서버가 종료됩니다.)
timeout /t 3 >nul
start "" "http://localhost:8000/CropAdvisor.dc.html"

@echo off
REM 안농 - 웹사이트(8000) + 농업뉴스(8001) + 작물점수(8002) 동시 실행
cd /d "%~dp0"
echo [1/3] 웹사이트 서버 시작 (http://localhost:8000)
REM --directory "%~dp0" 는 %~dp0 끝의 백슬래시가 닫는 따옴표를 이스케이프(\")해
REM 경로가 깨져 모든 요청이 404가 된다. 위의 cd 로 이동한 현재 폴더를 그대로 서빙한다.
start "Beomin Web (8000)" cmd /k python -m http.server 8000
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

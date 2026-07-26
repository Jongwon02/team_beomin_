@echo off
REM 안농 - 웹사이트(8000) + 농업뉴스(8001) + 작물점수(8002) + 챗봇(8003) 동시 실행
cd /d "%~dp0"
echo [1/4] 웹사이트 서버 시작 (http://localhost:8000)
REM --directory "%~dp0" 는 %~dp0 끝의 백슬래시가 닫는 따옴표를 이스케이프(\")해
REM 경로가 깨져 모든 요청이 404가 된다. 위의 cd 로 이동한 현재 폴더를 그대로 서빙한다.
start "Beomin Web (8000)" cmd /k python -m http.server 8000
echo [2/4] 농업 뉴스 서버 시작 (http://localhost:8001)
start "Beomin News (8001)" cmd /k python "%~dp0news_server.py"
echo [3/4] 작물 적합도 점수 서버 시작 (http://localhost:8002)
start "Beomin CropScore (8002)" cmd /k python "%~dp0..\backend\crop_score_server.py"
echo [4/4] 챗봇 서버 시작 (http://localhost:8003)
REM 챗봇은 .env의 ANTHROPIC_API_KEY가 있어야 동작합니다(없어도 다른 기능은 정상).
start "Beomin Chat (8003)" cmd /k python "%~dp0..\backend\chat_server.py"
echo.
echo 네 서버가 각각의 창에서 실행됩니다.
echo 브라우저에서 열기: http://localhost:8000/CropAdvisor.dc.html
echo (창을 닫으면 해당 서버가 종료됩니다.)
timeout /t 3 >nul
start "" "http://localhost:8000/CropAdvisor.dc.html"

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ===========================================================
echo    GitHub token-oo shalgah
echo  ===========================================================
echo.
echo  GitHub-aas avsan token-oo doosh buulgana uu.
echo  (github_pat_... gej ehelne)
echo.
set /p TOKEN="  Token: "
echo.
echo  Duudlaga ilgeej baina...
echo.
curl.exe -s -o nul -w "  HTTP hariu: %%{http_code}\n" -X POST ^
  -H "Accept: application/vnd.github+json" ^
  -H "Authorization: Bearer %TOKEN%" ^
  -H "X-GitHub-Api-Version: 2022-11-28" ^
  "https://api.github.com/repos/veov6899-bit/comment2tg/actions/workflows/comment2tg.yml/dispatches" ^
  -d "{\"ref\":\"main\"}"
echo.
echo  204 = AMJILTTAI (Actions huudsand shine ajillagaa garna)
echo  401 = token buruu
echo  403 = token-d Actions erh algaa
echo  404 = repo/workflow ner buruu
echo.
pause

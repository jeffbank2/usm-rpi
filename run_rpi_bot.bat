@echo off
setlocal enabledelayedexpansion
title Southern Miss RPI Bot

set BOT_DIR=C:\Users\jeffb\Desktop\SouthernMissRPIBot
set PYTHON=python
set LOG_FILE=%BOT_DIR%\bot_log.txt
set HTML_FILE=index.html
set BRIEF_FILE=daily_brief.txt

echo ============================================
echo  Southern Miss RPI Bot
echo  %date% %time%
echo ============================================
echo.

:: Log header
echo ============================================ >> "%LOG_FILE%"
echo Run started: %date% %time% >> "%LOG_FILE%"
echo ============================================ >> "%LOG_FILE%"

:: Move to bot directory
cd /d "%BOT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not find bot directory: %BOT_DIR%
    echo ERROR: Could not find bot directory >> "%LOG_FILE%"
    goto :fail
)

:: Check Python is available
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Is it installed and on your PATH?
    echo ERROR: Python not found >> "%LOG_FILE%"
    goto :fail
)

:: Check Git is available
git --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git not found. Install from https://git-scm.com
    echo ERROR: Git not found >> "%LOG_FILE%"
    goto :fail
)

:: Pull latest from GitHub first to avoid conflicts
echo [0/4] Syncing with GitHub...
git pull --rebase origin main >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo WARNING: git pull had issues, continuing anyway...
    echo WARNING: git pull issues >> "%LOG_FILE%"
)
echo       Done.

:: Run the bot
echo [1/4] Running RPI bot...
%PYTHON% southern_miss_rpi_bot.py --llm --html "%HTML_FILE%" --no-open > "%BRIEF_FILE%" 2>> "%LOG_FILE%"
if errorlevel 1 (
    echo ERROR: Bot failed. Check bot_log.txt for details.
    echo ERROR: Bot script failed >> "%LOG_FILE%"
    goto :fail
)
echo       Done.
type "%BRIEF_FILE%"
echo.

:: Stage files for Git (db intentionally excluded - Actions owns it)
echo [2/4] Staging files...
git add "%HTML_FILE%" "%BRIEF_FILE%" southern_miss_rpi_bot.py run_rpi_bot.bat >nul 2>&1
if errorlevel 1 (
    echo ERROR: git add failed. Is this folder a Git repo?
    echo ERROR: git add failed >> "%LOG_FILE%"
    goto :fail
)
echo       Done.

:: Commit
echo [3/4] Committing...
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Manual update %date% %time%" >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        echo ERROR: git commit failed. Check bot_log.txt.
        echo ERROR: git commit failed >> "%LOG_FILE%"
        goto :fail
    )
    echo       Committed.
) else (
    echo       Nothing changed since last run, skipping commit.
    echo       Skipped commit - no changes >> "%LOG_FILE%"
)

:: Push to GitHub
echo [4/4] Pushing to GitHub...
git push origin main >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo ERROR: git push failed. Check your internet connection or GitHub credentials.
    echo ERROR: git push failed >> "%LOG_FILE%"
    goto :fail
)
echo       Done.
echo.
echo ============================================
echo  SUCCESS
echo  Dashboard live at:
echo  https://jeffbank2.github.io/usm-rpi
echo ============================================
echo Success: %date% %time% >> "%LOG_FILE%"
goto :end

:fail
echo.
echo ============================================
echo  FAILED - Check bot_log.txt for details
echo ============================================
echo FAILED: %date% %time% >> "%LOG_FILE%"
exit /b 1

:end
echo Run complete: %date% %time% >> "%LOG_FILE%"
exit /b 0

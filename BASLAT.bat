@echo off
chcp 65001 >nul
title HALIC ANTIK MEZAT SISTEMI

cd /d "%~dp0"

echo =======================================================
echo           HALIC ANTIK MEZAT - TEK TIK CANLI YAYIN MOTORU
echo =======================================================
echo.
echo [*] Sistemler baslatiliyor, lutfen bekleyin...
echo.

:: 1. Mezat Chrome Kontrolu (Bagimsiz profil ile 9222 portunu garanti acar)
netstat -ano | findstr :9222 >nul
if errorlevel 1 (
    echo [*] Mezat Chrome Penceresi Port 9222 ile Baslatiliyor...
    if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
        start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0fb_profile" --remote-allow-origins=* --no-first-run --no-default-browser-check "https://www.facebook.com" "https://www.instagram.com"
    ) else (
        start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0fb_profile" --remote-allow-origins=* --no-first-run --no-default-browser-check "https://www.facebook.com" "https://www.instagram.com"
    )
    timeout /t 6 >nul
) else (
    echo [OK] Mezat Chrome Portu Zaten Calisiyor, Mevcut Sayfalar Korundu.
)

:: 2. Flask Web Sunucusu (Port 5000)
echo [1/4] Flask Sunucu Baslatiliyor...
start "1. FLASK SUNUCU" cmd /k "python app.py"
timeout /t 3 >nul

:: 3. Ana Ses ve Pey Motoru (Port 5005)
echo [2/4] Ana Ses ve Pey Motoru Baslatiliyor...
start "2. CANLI BOT (AHMET USTA)" cmd /k "python canli_bot.py halicantikmezat34"
timeout /t 3 >nul

:: 4. Facebook Koprusu
echo [3/4] Facebook Dinleyici Baslatiliyor...
start "3. FACEBOOK BOTU" cmd /k "python facebook_bridge.py"
timeout /t 2 >nul

:: 5. Instagram Koprusu
echo [4/4] Instagram Koprusu Baslatiliyor...
if exist "instagram_bridge.py" (
    start "4. INSTAGRAM KOPRUSU" cmd /k "python instagram_bridge.py"
)

echo.
echo =======================================================
echo           TUM SISTEMLER BASARIYLA DEVREYE ALINDI!
echo =======================================================
timeout /t 3 >nul
exit
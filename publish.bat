@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title BeamHub - публикация на GitHub

where git >nul 2>nul
if errorlevel 1 (
  echo Git не найден. Установите: winget install --id Git.Git -e
  pause
  exit /b 1
)

if "%~1"=="" (
  set /p "REPO=Ссылка на репозиторий (https://github.com/ВАШ_НИК/beamhub.git): "
) else (
  set "REPO=%~1"
)
if "!REPO!"=="" (
  echo Ссылка не задана.
  pause
  exit /b 1
)

echo.
echo [1/5] Настраиваю автора коммитов...
git config user.name >nul 2>nul || git config user.name "BeamHub"
git config user.email >nul 2>nul || git config user.email "beamhub@users.noreply.github.com"

echo [2/5] Инициализирую репозиторий...
if not exist .git git init -q
git add -A
git commit -q -m "BeamHub 0.7.1" 2>nul
git branch -M main

echo [3/5] Добавляю remote...
git remote remove origin >nul 2>nul
git remote add origin "!REPO!"

echo [4/5] Пушу (GitHub попросит войти в браузере)...
git push -u origin main
if errorlevel 1 (
  echo.
  echo Пуш не удался. Проверьте ссылку и вход в GitHub.
  pause
  exit /b 1
)

echo [5/5] Ставлю тег v0.7.1 — он запустит сборку релиза в Actions...
git tag -f v0.7.1
git push -f origin v0.7.1

echo.
echo Готово. Следите за сборкой: !REPO! /actions
echo После релиза впишите репозиторий в настройках BeamHub для автообновления.
pause

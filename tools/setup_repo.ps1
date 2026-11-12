# Настройка репозитория BeamHub: описание, темы, домашняя ссылка, переименование.
# Токен спрашивается локально (ввод скрыт) и никуда не сохраняется.
#
#   powershell -ExecutionPolicy Bypass -File tools\setup_repo.ps1
#   powershell -ExecutionPolicy Bypass -File tools\setup_repo.ps1 -DryRun
#
# Нужен fine-grained token с доступом к этому репозиторию и правом
# Administration: Read and write. Срок — 1 день, после — отозвать.

param(
    [switch]$DryRun,
    [switch]$SkipRename
)

$ErrorActionPreference = "Stop"
$repo = "Owiom/beamhub"
$newName = "beamhub"
$description = "Каталог модов для BeamNG.drive: 2791 мод с четырёх сайтов, установка в один клик, библиотека, обновления. Windows, .exe"
$homepage = "https://github.com/Owiom/beamhub/releases/latest"
$topics = @(
    "beamng", "beamngdrive", "beamng-drive", "mods", "mod-manager", "launcher",
    "mod-catalog", "gaming-tools", "python", "tkinter", "windows", "pyinstaller",
    "desktop-app", "modding", "beamng-mods"
)

Write-Host "Репозиторий: $repo" -ForegroundColor Cyan
Write-Host "Описание:    $description"
Write-Host "Homepage:    $homepage"
Write-Host "Темы:        $($topics -join ', ')"
if (-not $SkipRename) { Write-Host "Новое имя:   $newName (старые ссылки будут редиректиться)" }
Write-Host ""

if ($DryRun) {
    Write-Host "DryRun: ничего не меняю. Убери -DryRun, чтобы применить." -ForegroundColor Yellow
    exit 0
}

$secure = Read-Host "Вставь GitHub-токен (ввод не отображается)" -AsSecureString
$ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}

if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Host "Токен пустой — выходим." -ForegroundColor Red
    exit 1
}

$headers = @{
    Authorization          = "Bearer $token"
    Accept                 = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent"           = "BeamHub-setup"
}

function Invoke-GitHub {
    param([string]$Method, [string]$Uri, [hashtable]$Body)
    $json = if ($Body) { $Body | ConvertTo-Json -Depth 5 } else { $null }
    try {
        return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $headers -Body $json `
            -ContentType "application/json; charset=utf-8" -TimeoutSec 30
    } catch {
        $detail = $_.ErrorDetails.Message
        Write-Host "  ошибка: $($_.Exception.Message)" -ForegroundColor Red
        if ($detail) { Write-Host "  ответ GitHub: $detail" -ForegroundColor DarkRed }
        return $null
    }
}

Write-Host "[1/3] Описание и домашняя ссылка…"
$patched = Invoke-GitHub -Method Patch -Uri "https://api.github.com/repos/$repo" -Body @{
    description = $description
    homepage    = $homepage
}
if ($patched) { Write-Host "  ок" -ForegroundColor Green }

Write-Host "[2/3] Темы…"
$okTopics = Invoke-GitHub -Method Put -Uri "https://api.github.com/repos/$repo/topics" -Body @{ names = $topics }
if ($okTopics) { Write-Host "  ок: $($okTopics.names -join ', ')" -ForegroundColor Green }

if (-not $SkipRename) {
    Write-Host "[3/3] Переименование в $newName…"
    $renamed = Invoke-GitHub -Method Patch -Uri "https://api.github.com/repos/$repo" -Body @{ name = $newName }
    if ($renamed) {
        Write-Host "  ок: $($renamed.full_name)" -ForegroundColor Green
        Write-Host "  не забудь обновить ссылки в README и в настройках BeamHub (repo: $($renamed.full_name))" -ForegroundColor Yellow
    }
} else {
    Write-Host "[3/3] Переименование пропущено"
}

$token = $null
$headers = $null
Write-Host ""
Write-Host "Готово. Теперь:" -ForegroundColor Cyan
Write-Host "  1. Settings -> Social preview -> загрузи docs/banner.png (только через сайт)"
Write-Host "  2. Отзови токен: github.com/settings/tokens"
Write-Host "  3. Если переименовал — поправь update_repo в настройках BeamHub"

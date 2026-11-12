# Разворачивает приёмник обратной связи BeamHub в Cloudflare Workers.
# Токен спрашивается локально (ввод скрыт) и никуда не сохраняется.
#
#   powershell -ExecutionPolicy Bypass -File worker\deploy.ps1
#   powershell -ExecutionPolicy Bypass -File worker\deploy.ps1 -DryRun
#
# Нужен API-токен Cloudflare с правами:
#   Account → Workers Scripts: Edit
#   Account → Account Settings: Read
# Создать: dash.cloudflare.com → My Profile → API Tokens → Create Token.

param(
    [string]$Name = "beamhub-feedback",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$entry = Join-Path $PSScriptRoot "index.js"
if (-not (Test-Path $entry)) { Write-Host "Нет файла index.js рядом со скриптом." -ForegroundColor Red; exit 1 }

Write-Host "Воркер:   $Name"
Write-Host "Код:      $entry"
Write-Host "Адрес:    https://$Name.<аккаунт>.workers.dev"
Write-Host ""

if ($DryRun) {
    Write-Host "DryRun: ничего не отправляю. Убери -DryRun, чтобы развернуть." -ForegroundColor Yellow
    exit 0
}

$secure = Read-Host "Вставь токен Cloudflare (ввод скрыт)" -AsSecureString
$ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
if ([string]::IsNullOrWhiteSpace($token)) { Write-Host "Пустой токен." -ForegroundColor Red; exit 1 }

$headers = @{ Authorization = "Bearer $token" }

Write-Host "[1/4] Ищу аккаунт Cloudflare…"
try {
    $accounts = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/accounts" -Headers $headers -TimeoutSec 30
} catch {
    Write-Host "  токен не принят: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  проверь права: Workers Scripts: Edit и Account Settings: Read" -ForegroundColor Yellow
    exit 1
}
if (-not $accounts.result -or $accounts.result.Count -eq 0) {
    Write-Host "  аккаунтов не найдено — токен без доступа к аккаунту" -ForegroundColor Red
    exit 1
}
$accountId = $accounts.result[0].id
Write-Host "  аккаунт: $($accounts.result[0].name) ($accountId)" -ForegroundColor Green
if ($accounts.result.Count -gt 1) {
    Write-Host "  найдено аккаунтов: $($accounts.result.Count), беру первый" -ForegroundColor Yellow
}

Write-Host "[2/4] Загружаю код воркера…"
$meta = Join-Path $env:TEMP "beamhub-worker-meta.json"
Set-Content -Path $meta -Value '{"main_module":"index.js","compatibility_date":"2025-01-01"}' -Encoding utf8
$uri = "https://api.cloudflare.com/client/v4/accounts/$accountId/workers/scripts/$Name"
$raw = & curl.exe -s -X PUT $uri `
    -H "Authorization: Bearer $token" `
    -F "metadata=@$meta;type=application/json" `
    -F "index.js=@$entry;type=application/javascript;filename=index.js"
Remove-Item $meta -Force -ErrorAction SilentlyContinue
try { $upload = $raw | ConvertFrom-Json } catch { $upload = $null }
if (-not $upload -or -not $upload.success) {
    Write-Host "  не удалось: $raw" -ForegroundColor Red
    Write-Host "  проверь, что токен с правом Workers Scripts: Edit" -ForegroundColor Yellow
    exit 1
}
Write-Host "  загружено" -ForegroundColor Green

function Set-Secret {
    param([string]$Key, [string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $body = @{ name = $Key; text = $Value; type = "secret_text" } | ConvertTo-Json
    try {
        Invoke-RestMethod -Method Put -Uri "$uri/secrets" -Headers $headers `
            -ContentType "application/json" -Body $body -TimeoutSec 30 | Out-Null
        Write-Host "  секрет $Key установлен" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  секрет $Key не установился: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

Write-Host "[3/4] Секреты доставки…"
Write-Host "  Можно настроить почту (Resend) — 3 поля, или пропустить и добавить позже в Dashboard."
$resendSecure = Read-Host "  RESEND_KEY (ввод скрыт, Enter — пропустить)" -AsSecureString
$rptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($resendSecure)
try { $resend = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($rptr) } finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($rptr) }
$from = Read-Host "  FROM_EMAIL (Enter — onboarding@resend.dev)"
if ([string]::IsNullOrWhiteSpace($from)) { $from = "onboarding@resend.dev" }
$to = Read-Host "  TO_EMAIL (Enter — sparco56@gmail.com)"
if ([string]::IsNullOrWhiteSpace($to)) { $to = "sparco56@gmail.com" }

$any = $false
if (Set-Secret -Key "RESEND_KEY" -Value $resend) { $any = $true }
if ($resend) {
    Set-Secret -Key "FROM_EMAIL" -Value $from | Out-Null
    Set-Secret -Key "TO_EMAIL" -Value $to | Out-Null
}
if (-not $any) {
    Write-Host "  ни один канал не настроен — отчёты будут отклоняться, пока не добавишь секреты" -ForegroundColor Yellow
}

Write-Host "[4/4] Адрес воркера…"
try {
    $sub = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/accounts/$accountId/workers/subdomain" -Headers $headers -TimeoutSec 20
    $workerUrl = "https://$Name.$($sub.result.subdomain).workers.dev"
    Write-Host ""
    Write-Host "ГОТОВО. Приёмник: $workerUrl" -ForegroundColor Cyan
    Write-Host "Вставь этот адрес в BeamHub: Настройки → «Приёмник обратной связи»."
} catch {
    Write-Host "  адрес не получен: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  открой dash.cloudflare.com → Workers & Pages → $Name и скопируй URL оттуда"
}

$token = $null; $headers = $null

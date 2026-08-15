<#
.SYNOPSIS
  Run the ReciFi test suite under real MicroPython in Docker.
.DESCRIPTION
  Uses the micropython/unix image so MicroPython-only gaps (no int.bit_length,
  unordered dicts, frozen-module paths) surface here instead of on hardware.
  Add -Qr to also cross-check the QR encoder against CPython's qrcode library.
#>
param(
    [switch]$Qr,
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

# Liquid evaluates {{ ... }} even inside fenced code blocks, so any of ReciFi's
# own template placeholders in the docs must sit inside {% raw %} tags or they
# publish as blanks. Cheap to check, easy to forget.
Write-Host "== Docs placeholder check ==" -ForegroundColor Cyan
$docsBad = @()
foreach ($file in Get-ChildItem (Join-Path $repo "docs") -Filter *.md) {
    $text = Get-Content $file.FullName -Raw
    $stripped = [regex]::Replace($text, '(?s)\{%\s*raw\s*%\}.*?\{%\s*endraw\s*%\}', '')
    $bad = [regex]::Matches($stripped, '\{\{(.*?)\}\}') |
        ForEach-Object { $_.Groups[1].Value.Trim() } |
        Where-Object { $_ -notmatch '^site\.' }
    if ($bad) { $docsBad += "$($file.Name): $($bad -join ', ')" }
}
if ($docsBad) {
    $docsBad | ForEach-Object { Write-Host "  unguarded placeholder -- $_" -ForegroundColor Red }
    throw "docs contain placeholders Liquid will eat; wrap them in {% raw %} tags"
}
Write-Host "  ok"

Write-Host "`n== MicroPython test suite ==" -ForegroundColor Cyan
docker run --rm -v "${repo}:/app" -w /app/tests `
    -e MICROPYPATH=".frozen:/app/device:/app/tests" `
    micropython/unix:latest micropython run.py
if ($LASTEXITCODE -ne 0) { throw "MicroPython tests failed" }

if ($Qr) {
    Write-Host "`n== QR cross-check against the reference library ==" -ForegroundColor Cyan
    docker run --rm -v "${repo}:/app" -w /app python:3.12-slim sh -lc `
        "pip install -q qrcode opencv-python-headless numpy 2>/dev/null; python tools/qrcheck.py"
    if ($LASTEXITCODE -ne 0) { throw "QR cross-check failed" }
}

if ($Preview) {
    $out = Join-Path $repo "preview"
    if (-not (Test-Path $out)) { New-Item -ItemType Directory $out | Out-Null }
    Write-Host "`n== Sample labels ==" -ForegroundColor Cyan
    docker run --rm -v "${repo}:/app" -v "${out}:/out" -w /app/device `
        -e MICROPYPATH=".frozen:/app/device" `
        micropython/unix:latest micropython /app/tools/preview.py /out
    Write-Host "wrote .bmp files to $out"
}

Write-Host "`nAll good." -ForegroundColor Green

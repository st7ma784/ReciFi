<#
.SYNOPSIS
  Install MicroPython on a Pico 2 W in BOOTSEL mode.
.DESCRIPTION
  Downloads the RPI_PICO2_W UF2 (cached under tools\firmware) and copies it to
  the board's bootloader drive. The board reboots into MicroPython by itself.

  WARNING: this replaces the board's entire flash contents.

  Hold BOOTSEL while plugging the board in to get the drive to appear.
.EXAMPLE
  .\tools\flash.ps1 -WhatIf
  .\tools\flash.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Version = "20260406-v1.28.0",
    [switch]$RiscV
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$cache = Join-Path $PSScriptRoot "firmware"

$name = if ($RiscV) { "RPI_PICO2_W-RISCV-$Version.uf2" } else { "RPI_PICO2_W-$Version.uf2" }
$url = "https://micropython.org/resources/firmware/$name"
$local = Join-Path $cache $name

# ------------------------------------------------- find the BOOTSEL drive

$drive = Get-Volume | Where-Object {
    $_.DriveType -eq "Removable" -and $_.DriveLetter -and
    (Test-Path (Join-Path "$($_.DriveLetter):\" "INFO_UF2.TXT"))
} | Select-Object -First 1

if (-not $drive) {
    Write-Host "No RP2 bootloader drive found." -ForegroundColor Red
    Write-Host "Unplug the board, hold the BOOTSEL button, plug it back in, and retry."
    exit 1
}

$root = "$($drive.DriveLetter):\"
$info = Get-Content (Join-Path $root "INFO_UF2.TXT") -Raw
if ($info -notmatch "RP2350") {
    Write-Host "Drive $root does not look like an RP2350 board:" -ForegroundColor Red
    Write-Host $info
    exit 1
}
Write-Host "Found bootloader on $root" -ForegroundColor Cyan
Write-Host ($info.Trim())

# ------------------------------------------------------------- download

if (-not (Test-Path $cache)) { New-Item -ItemType Directory $cache | Out-Null }
if (Test-Path $local) {
    Write-Host "Using cached $name"
} else {
    Write-Host "Downloading $url"
    $previous = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"   # a progress bar makes this 10x slower
    try {
        Invoke-WebRequest -Uri $url -OutFile $local -UseBasicParsing
    } finally {
        $ProgressPreference = $previous
    }
}

$size = (Get-Item $local).Length
if ($size -lt 300KB) { Remove-Item $local; throw "downloaded file is only $size bytes -- not a UF2" }
Write-Host ("Firmware {0} ({1:N0} bytes)" -f $name, $size)

# ---------------------------------------------------------------- flash

if ($PSCmdlet.ShouldProcess($root, "overwrite the board's flash with $name")) {
    Copy-Item $local (Join-Path $root $name)
    Write-Host "Copied. The board will reboot into MicroPython." -ForegroundColor Green

    Write-Host "Waiting for the USB serial port to appear..."
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        $port = @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "\(COM\d+\)" -and $_.DeviceID -match "VID_2E8A" })
        if ($port.Count -gt 0) {
            Write-Host "  $($port[0].Name)" -ForegroundColor Green
            Write-Host "`nNext: .\tools\deploy.ps1 -Ssid MANDER84 -Password '<password>'"
            exit 0
        }
    }
    Write-Host "No serial port appeared within 30s -- check Device Manager." -ForegroundColor Yellow
}

<#
.SYNOPSIS
  Copy the ReciFi source onto a Pico running MicroPython, over the USB REPL.
.DESCRIPTION
  Does what `mpremote fs cp` does, but in pure PowerShell -- this machine has no
  Python to install mpremote with, and Docker on Windows cannot pass a COM port
  through to a container.

  Files go across as base64 through the raw REPL, which avoids every quoting and
  binary-safety problem that sending them as literals would create.

.EXAMPLE
  .\tools\deploy.ps1 -List
  .\tools\deploy.ps1
  .\tools\deploy.ps1 -Port COM7 -Ssid MANDER84 -Password 'secret'
#>
param(
    [string]$Port,
    [switch]$List,
    [string]$Ssid,
    [string]$Password,
    [string]$Hostname = "recifi",
    [switch]$InstallBle,
    [switch]$NoReset,
    # Copy only these paths (relative to device\) instead of everything --
    # a full copy is ~120KB over a 115200 baud REPL, which is slow to iterate on.
    [string[]]$Files
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$root = Join-Path $repo "device"

# --------------------------------------------------------------- port

function Get-PicoPorts {
    $ports = @()
    Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "\(COM(\d+)\)" } |
        ForEach-Object {
            $name = $_.Name
            $com = ([regex]::Match($name, "\(COM(\d+)\)")).Groups[0].Value.Trim("()")
            # 2E8A is the Raspberry Pi vendor id; MicroPython enumerates as CDC.
            $isPico = $_.DeviceID -match "VID_2E8A" -or $name -match "MicroPython|Pico|Board in FS mode"
            $ports += [pscustomobject]@{ Port = $com; Name = $name; Likely = $isPico }
        }
    return $ports
}

if ($List) {
    Get-PicoPorts | Sort-Object -Property Likely -Descending | Format-Table -AutoSize
    return
}

if (-not $Port) {
    $candidates = @(Get-PicoPorts | Where-Object { $_.Likely })
    if ($candidates.Count -eq 0) {
        Write-Host "No Pico found on a COM port." -ForegroundColor Red
        Write-Host "If the board shows as a drive (RP2350), it is still in BOOTSEL --"
        Write-Host "run tools\flash.ps1 first to install MicroPython."
        Write-Host "Otherwise: .\tools\deploy.ps1 -List"
        exit 1
    }
    if ($candidates.Count -gt 1) {
        Write-Host "More than one candidate; pick with -Port:" -ForegroundColor Yellow
        $candidates | Format-Table -AutoSize
        exit 1
    }
    $Port = $candidates[0].Port
    Write-Host "Using $Port ($($candidates[0].Name))"
}

# ----------------------------------------------------------- raw REPL

$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, "None", 8, "One"
$serial.ReadTimeout = 4000
$serial.WriteTimeout = 4000
$serial.DtrEnable = $true
$serial.Open()

function Read-Until([string]$marker, [int]$timeoutMs = 8000) {
    $buffer = ""
    $deadline = (Get-Date).AddMilliseconds($timeoutMs)
    while ((Get-Date) -lt $deadline) {
        try { $buffer += $serial.ReadExisting() } catch {}
        if ($buffer.Contains($marker)) { return $buffer }
        Start-Sleep -Milliseconds 15
    }
    throw "timed out waiting for '$marker'; got: $($buffer -replace '[^\x20-\x7e]', '.')"
}

function Enter-RawRepl {
    $serial.Write([char]3 + "")       # interrupt anything running
    Start-Sleep -Milliseconds 120
    $serial.Write([char]3 + "")
    Start-Sleep -Milliseconds 120
    $serial.DiscardInBuffer()
    $serial.Write([char]1 + "")       # ctrl-A: raw REPL
    # One read only: the banner and the prompt arrive together, and a second
    # Read-Until would wait forever for a '>' the first call already consumed.
    $banner = Read-Until ">"
    if (-not $banner.Contains("raw REPL")) {
        throw "did not get a raw REPL prompt; got: $($banner -replace '[^\x20-\x7e]', '.')"
    }
}

function Invoke-Mp([string]$code, [int]$timeoutMs = 8000) {
    $serial.Write($code)
    $serial.Write([char]4 + "")       # ctrl-D: execute
    $response = Read-Until ([string][char]4 + ">") $timeoutMs
    # Raw REPL answers OK<stdout>\x04<stderr>\x04>
    $parts = $response -split [string][char]4
    if ($parts.Count -ge 2 -and $parts[1].Trim()) {
        throw "device error running code:`n$($parts[1].Trim())"
    }
    return ($parts[0] -replace "^OK", "").Trim()
}

function Copy-ToDevice([string]$localPath, [string]$remotePath) {
    $bytes = [System.IO.File]::ReadAllBytes($localPath)
    Invoke-Mp "import binascii`nf=open('$remotePath','wb')" | Out-Null
    $chunk = 512
    for ($offset = 0; $offset -lt $bytes.Length; $offset += $chunk) {
        $take = [Math]::Min($chunk, $bytes.Length - $offset)
        $slice = New-Object byte[] $take
        [Array]::Copy($bytes, $offset, $slice, 0, $take)
        $b64 = [Convert]::ToBase64String($slice)
        Invoke-Mp "f.write(binascii.a2b_base64('$b64'))" | Out-Null
    }
    Invoke-Mp "f.close()" | Out-Null
    return $bytes.Length
}

try {
    Write-Host "Entering raw REPL on $Port..."
    Enter-RawRepl
    $version = Invoke-Mp "import sys;print(sys.implementation)"
    Write-Host "  $version"

    Invoke-Mp "import os`nfor d in ('recifi','recifi/printers','www'):`n    try: os.mkdir(d)`n    except OSError: pass" | Out-Null

    $files = if ($Files) { $Files } else { @(
        "main.py",
        "recifi/__init__.py", "recifi/api.py", "recifi/config.py", "recifi/qr.py",
        "recifi/render.py", "recifi/server.py", "recifi/store.py", "recifi/wifi.py",
        "recifi/printers/__init__.py", "recifi/printers/protocols.py",
        "recifi/printers/transports.py",
        "www/index.html", "www/app.js", "www/style.css"
    ) }

    $total = 0
    foreach ($file in $files) {
        $local = Join-Path $root $file
        if (-not (Test-Path $local)) { throw "missing $local" }
        $size = Copy-ToDevice $local $file
        $total += $size
        Write-Host ("  {0,-34} {1,6} bytes" -f $file, $size)
    }
    Write-Host "Copied $($files.Count) files, $total bytes."

    if ($Ssid) {
        Write-Host "Writing WiFi credentials for '$Ssid'..."
        # Built on-device so the password never lands in a shell history file.
        $code = @"
import json, binascii
try:
    cfg = json.load(open('config.json'))
except Exception:
    cfg = {}
cfg.setdefault('wifi', {})
cfg['wifi']['ssid'] = binascii.a2b_base64('$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Ssid)))').decode()
cfg['wifi']['password'] = binascii.a2b_base64('$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Password)))').decode()
cfg['wifi']['hostname'] = '$Hostname'
json.dump(cfg, open('config.json','w'))
print('wifi configured for', cfg['wifi']['ssid'])
"@
        $result = Invoke-Mp $code
        Write-Host "  $result"
    }

    if ($InstallBle) {
        # aioble is not in the firmware; it has to be fetched over the network,
        # so the board needs WiFi working before this can succeed.
        Write-Host "Installing aioble on the board (needs WiFi)..."
        $code = @"
import network, time, json, mip
cfg = json.load(open('config.json'))
w = network.WLAN(network.STA_IF)
w.active(True)
if not w.isconnected():
    w.connect(cfg['wifi']['ssid'], cfg['wifi']['password'])
for _ in range(40):
    if w.isconnected():
        break
    time.sleep(0.5)
if not w.isconnected():
    raise OSError('wifi did not come up, cannot fetch aioble')
print('ip', w.ifconfig()[0])
mip.install('aioble')
print('aioble installed')
"@
        Write-Host "  $(Invoke-Mp $code 60000)"
    }

    if (-not $NoReset) {
        Write-Host "Resetting the board..."
        $serial.Write([char]2 + "")   # back to the friendly REPL
        Start-Sleep -Milliseconds 100
        $serial.Write([char]4 + "")   # soft reset -> main.py runs
        Start-Sleep -Seconds 3
        $boot = ""
        try { $boot = $serial.ReadExisting() } catch {}
        if ($boot) { Write-Host "--- board output ---`n$boot" }
    }
}
finally {
    if ($serial.IsOpen) { $serial.Close() }
}

Write-Host "`nDone. Watch the board's output with tools\console.ps1 to see its IP." -ForegroundColor Green

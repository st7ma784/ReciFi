<#
.SYNOPSIS
  Run a snippet on the board and print what it says.
.DESCRIPTION
  Interrupts main.py, executes the code in the raw REPL, and resets afterwards
  unless -NoReset is given. Handy for poking at hardware the API doesn't expose.
.EXAMPLE
  .\tools\repl.ps1 -Code "import gc; gc.collect(); print(gc.mem_free())"
  .\tools\repl.ps1 -File tools\snippets\gatt.py -TimeoutSec 60
#>
param(
    [string]$Port,
    [string]$Code,
    [string]$File,
    [int]$TimeoutSec = 30,
    [switch]$NoReset
)

$ErrorActionPreference = "Stop"

if ($File) { $Code = Get-Content -Raw $File }
if (-not $Code) { throw "pass -Code or -File" }

if (-not $Port) {
    $found = @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "\(COM\d+\)" -and $_.DeviceID -match "VID_2E8A" })
    if ($found.Count -ne 1) { throw "specify -Port; found $($found.Count) candidates" }
    $Port = ([regex]::Match($found[0].Name, "\(COM(\d+)\)")).Groups[0].Value.Trim("()")
}

$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, "None", 8, "One"
$serial.DtrEnable = $true
$serial.Open()

function Read-Until([string]$marker, [int]$timeoutMs) {
    $buffer = ""
    $deadline = (Get-Date).AddMilliseconds($timeoutMs)
    while ((Get-Date) -lt $deadline) {
        try { $buffer += $serial.ReadExisting() } catch {}
        if ($buffer.Contains($marker)) { return $buffer }
        Start-Sleep -Milliseconds 15
    }
    throw "timed out waiting for '$marker'; got: $($buffer -replace '[^\x20-\x7e\r\n]', '.')"
}

try {
    $serial.Write([char]3 + ""); Start-Sleep -Milliseconds 150
    $serial.Write([char]3 + ""); Start-Sleep -Milliseconds 150
    $serial.DiscardInBuffer()
    $serial.Write([char]1 + "")
    $banner = Read-Until ">" 8000
    if (-not $banner.Contains("raw REPL")) { throw "no raw REPL prompt" }

    $serial.Write($Code)
    $serial.Write([char]4 + "")
    $response = Read-Until ([string][char]4 + ">") ($TimeoutSec * 1000)

    $parts = $response -split [string][char]4
    Write-Host ($parts[0] -replace "^OK", "")
    if ($parts.Count -ge 2 -and $parts[1].Trim()) {
        Write-Host "--- error ---" -ForegroundColor Red
        Write-Host $parts[1].Trim() -ForegroundColor Red
    }

    if (-not $NoReset) {
        $serial.Write([char]2 + ""); Start-Sleep -Milliseconds 100
        $serial.Write([char]4 + "")
    }
}
finally {
    if ($serial.IsOpen) { $serial.Close() }
}

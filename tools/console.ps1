<#
.SYNOPSIS
  Watch the Pico's serial output (its boot log prints the IP it got).
.DESCRIPTION
  Ctrl-C to stop. Pass -Interrupt to stop main.py and drop to the REPL, which is
  what you want before poking at the board by hand.
#>
param(
    [string]$Port,
    [switch]$Interrupt,
    [switch]$Reset
)

$ErrorActionPreference = "Stop"

if (-not $Port) {
    $found = @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "\(COM\d+\)" -and
                      ($_.DeviceID -match "VID_2E8A" -or $_.Name -match "MicroPython|Pico") })
    if ($found.Count -ne 1) {
        Write-Host "Specify -Port; candidates:" -ForegroundColor Yellow
        $found | Select-Object Name | Format-Table -AutoSize
        exit 1
    }
    $Port = ([regex]::Match($found[0].Name, "\(COM(\d+)\)")).Groups[0].Value.Trim("()")
}

$serial = New-Object System.IO.Ports.SerialPort $Port, 115200, "None", 8, "One"
$serial.DtrEnable = $true
$serial.Open()
Write-Host "Connected to $Port. Ctrl-C to stop." -ForegroundColor Cyan

try {
    if ($Interrupt) { $serial.Write([char]3 + ""); Start-Sleep -Milliseconds 200 }
    if ($Reset) { $serial.Write([char]4 + ""); Start-Sleep -Milliseconds 200 }
    while ($true) {
        $text = $serial.ReadExisting()
        if ($text) { Write-Host -NoNewline $text }
        Start-Sleep -Milliseconds 60
    }
}
finally {
    if ($serial.IsOpen) { $serial.Close() }
}

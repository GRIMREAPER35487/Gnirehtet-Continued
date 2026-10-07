<#
.SYNOPSIS
    Enables Windows Internet Connection Sharing (ICS) from PC Internet (Wi-Fi) to Quest USB (Ethernet 4).
    Must be run from an elevated session (e.g. via setup_ics.cmd).
#>

param(
    [string]$InternetName = "Wi-Fi",
    [string]$HeadsetName = "Ethernet 4"
)

# Check Administrator privileges (no recursive loop!)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This script requires Administrator rights." -ForegroundColor Red
    Write-Host "Please launch 'setup_ics.cmd' by right-clicking it and selecting 'Run as administrator'." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Windows Internet Connection Sharing (ICS) Setup        " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Register hnetcfg.dll COM library
regsvr32 /s hnetcfg.dll

# 2. Instantiate NetSharingManager COM object
$netShare = New-Object -ComObject HNetCfg.HNetShare

# 3. Locate connections
$pubConn = $null
$privConn = $null

foreach ($conn in $netShare.EnumEveryConnection) {
    try {
        $props = $netShare.NetConnectionProps.Invoke($conn)
        if ($props.Name -eq $InternetName) {
            $pubConn = $conn
        }
        if ($props.Name -eq $HeadsetName -or $props.Name -like "*UsbNcm*") {
            $privConn = $conn
        }
    } catch {}
}

# Auto-detect fallback if not found by exact name
if (-not $pubConn) {
    $defRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($defRoute) {
        $pubName = $defRoute.InterfaceAlias
        Write-Host "Auto-detected active internet connection: '$pubName'" -ForegroundColor Yellow
        foreach ($conn in $netShare.EnumEveryConnection) {
            try {
                if ($netShare.NetConnectionProps.Invoke($conn).Name -eq $pubName) {
                    $pubConn = $conn
                    break
                }
            } catch {}
        }
    }
}

if (-not $privConn) {
    $ncm = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -match "UsbNcm|NCM" } | Select-Object -First 1
    if ($ncm) {
        $privName = $ncm.Name
        Write-Host "Auto-detected UsbNcm adapter: '$privName'" -ForegroundColor Yellow
        foreach ($conn in $netShare.EnumEveryConnection) {
            try {
                if ($netShare.NetConnectionProps.Invoke($conn).Name -eq $privName) {
                    $privConn = $conn
                    break
                }
            } catch {}
        }
    }
}

if (-not $pubConn) {
    Write-Host "[ERROR] Could not find internet adapter ($InternetName)." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not $privConn) {
    Write-Host "[ERROR] Could not find UsbNcm headset adapter ($HeadsetName)." -ForegroundColor Red
    Write-Host "Make sure the headset is plugged into USB." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

$pubName = $netShare.NetConnectionProps.Invoke($pubConn).Name
$privName = $netShare.NetConnectionProps.Invoke($privConn).Name

Write-Host "Sharing Internet from: '$pubName'" -ForegroundColor Green
Write-Host "Target Headset adapter: '$privName'" -ForegroundColor Green

# 4. Disable existing sharing to avoid COM state conflicts
foreach ($conn in $netShare.EnumEveryConnection) {
    try {
        $cfg = $netShare.INetSharingConfigurationForINetConnection.Invoke($conn)
        if ($cfg.SharingEnabled) {
            $cfg.DisableSharing()
        }
    } catch {}
}

Start-Sleep -Milliseconds 800

# 5. Enable sharing (0 = Public / Internet, 1 = Private / Local Headset)
try {
    $pubCfg = $netShare.INetSharingConfigurationForINetConnection.Invoke($pubConn)
    $privCfg = $netShare.INetSharingConfigurationForINetConnection.Invoke($privConn)

    $pubCfg.EnableSharing(0)
    $privCfg.EnableSharing(1)
} catch {
    Write-Host "[WARN] Exception while applying COM sharing: $($_.Exception.Message)" -ForegroundColor Yellow
}

# 6. Ensure Windows SharedAccess (ICS) service is enabled and running
Set-Service -Name SharedAccess -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name SharedAccess -ErrorAction SilentlyContinue

# 7. Enable reboot persistence in registry
$regPath = "HKLM:\Software\Microsoft\Windows\CurrentVersion\SharedAccess"
if (Test-Path $regPath) {
    Set-ItemProperty -Path $regPath -Name "EnableRebootPersistConnection" -Value 1 -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "[SUCCESS] Windows Internet Connection Sharing (ICS) is active!" -ForegroundColor Green
Write-Host "Your Quest will now receive direct, full-speed internet over the USB cable." -ForegroundColor Green
Write-Host ""

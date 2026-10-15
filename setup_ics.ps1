<#
.SYNOPSIS
    Enables Windows Internet Connection Sharing (ICS) from PC Internet (Wi-Fi) to Quest USB (Ethernet 4).
    Must be run from an elevated session (e.g. via setup_ics.cmd).
#>

param(
    [string]$InternetName = "Wi-Fi",
    [string]$HeadsetName = "Ethernet 4",
    # Disable sharing on other connections without asking for confirmation
    [switch]$Force
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

# 4. Windows allows only one shared (public) / home (private) pair: sharing must be disabled on any
#    other connection. Never touch other connections silently.
$pubCfg = $netShare.INetSharingConfigurationForINetConnection.Invoke($pubConn)
$privCfg = $netShare.INetSharingConfigurationForINetConnection.Invoke($privConn)

$otherShared = @()
foreach ($conn in $netShare.EnumEveryConnection) {
    try {
        $name = $netShare.NetConnectionProps.Invoke($conn).Name
        if ($name -eq $pubName -or $name -eq $privName) {
            continue
        }
        $cfg = $netShare.INetSharingConfigurationForINetConnection.Invoke($conn)
        if ($cfg.SharingEnabled) {
            $otherShared += [pscustomobject]@{ Name = $name; Config = $cfg }
        }
    } catch {}
}

if ($otherShared.Count -gt 0) {
    Write-Host ""
    Write-Host "[WARN] Internet Connection Sharing is currently enabled on other connection(s):" -ForegroundColor Yellow
    foreach ($other in $otherShared) {
        Write-Host "         - '$($other.Name)'" -ForegroundColor Yellow
    }
    Write-Host "       Windows supports only one shared connection pair, so sharing will be DISABLED on them." -ForegroundColor Yellow
    if (-not $Force) {
        $answer = Read-Host "Continue? [y/N]"
        if ($answer -notmatch '^(y|yes)$') {
            Write-Host "Aborted, nothing was changed." -ForegroundColor Yellow
            exit 1
        }
    }
    foreach ($other in $otherShared) {
        try {
            $other.Config.DisableSharing()
            Write-Host "Disabled sharing on '$($other.Name)'." -ForegroundColor Yellow
        } catch {
            Write-Host "[ERROR] Could not disable sharing on '$($other.Name)': $($_.Exception.Message)" -ForegroundColor Red
            Read-Host "Press Enter to exit"
            exit 1
        }
    }
}

# Our own two connections: reset them only if they are not already shared the right way
# (SharingConnectionType: 0 = Public / Internet, 1 = Private / Local Headset)
$pubOk = $pubCfg.SharingEnabled -and $pubCfg.SharingConnectionType -eq 0
$privOk = $privCfg.SharingEnabled -and $privCfg.SharingConnectionType -eq 1
foreach ($cfg in @(@{ Cfg = $pubCfg; Ok = $pubOk }, @{ Cfg = $privCfg; Ok = $privOk })) {
    if ($cfg.Cfg.SharingEnabled -and -not $cfg.Ok) {
        try { $cfg.Cfg.DisableSharing() } catch {}
    }
}

Start-Sleep -Milliseconds 800

# 5. Enable sharing (0 = Public / Internet, 1 = Private / Local Headset)
try {
    if (-not $pubOk) {
        $pubCfg.EnableSharing(0)
    }
    if (-not $privOk) {
        $privCfg.EnableSharing(1)
    }
} catch {
    Write-Host "[ERROR] Failed to enable Internet Connection Sharing: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
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

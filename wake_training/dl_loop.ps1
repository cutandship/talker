# Watchdog loop for HF downloads under DPI throttling: each connection is
# fast for the first ~1-2 GB, then gets choked. Download -> when growth stops,
# kill the process -> relaunch (hf_hub resumes from byte offset). Exit when
# download_features.py reports DOWNLOADS DONE.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
$env:HF_HUB_DISABLE_XET = "1"
$py = "python"   # на PATH; при необходимости укажи полный путь к python.exe
New-Item -ItemType Directory -Force logs | Out-Null

function DirSize {
    $s = (Get-ChildItem data -Recurse -File -ErrorAction SilentlyContinue |
          Measure-Object Length -Sum).Sum
    if ($null -eq $s) { 0 } else { $s }
}

for ($cycle = 1; $cycle -le 600; $cycle++) {
    # Alternate sources: direct HF vs hf-mirror (different IP space - DPI
    # throttle state does not carry over). Cooldown between cycles helps too.
    if ($cycle % 2 -eq 0) {
        $env:HF_ENDPOINT = "https://hf-mirror.com"
        $src = "mirror"
    } else {
        Remove-Item Env:HF_ENDPOINT -ErrorAction SilentlyContinue
        $src = "direct"
    }
    "[{0:HH:mm:ss}] cycle {1} ({2}), on disk {3} MB" -f (Get-Date), $cycle, $src, [math]::Round((DirSize)/1MB)
    $log = "logs\dl_cycle$cycle.log"
    $proc = Start-Process -FilePath $py -ArgumentList "download_features.py" `
        -WorkingDirectory $PSScriptRoot -PassThru -NoNewWindow `
        -RedirectStandardOutput $log -RedirectStandardError "logs\dl_err$cycle.log"
    $prev = DirSize
    $stall = 0
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 20
        $cur = DirSize
        if ($cur -le $prev) { $stall++ } else { $stall = 0 }
        $prev = $cur
        if ($stall -ge 3) {
            "[{0:HH:mm:ss}] growth stalled 60s - reconnect" -f (Get-Date)
            Stop-Process -Id $proc.Id -Force -Confirm:$false
            break
        }
    }
    if ($proc.HasExited -and $proc.ExitCode -eq 0 -and
        (Select-String -Path $log -Pattern "DOWNLOADS DONE" -Quiet)) {
        "[{0:HH:mm:ss}] DOWNLOADS DONE after {1} cycles" -f (Get-Date), $cycle
        exit 0
    }
    # Cooldown: let the DPI throttle window expire before reconnecting.
    Start-Sleep -Seconds 75
}
"60 cycles exhausted"
exit 1

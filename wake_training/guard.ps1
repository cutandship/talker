# Watchdog: keeps the training pipeline alive independently of any harness
# task or chat session. Every 3 min: if no run_night.py / train.py process is
# running AND out/REPORT.md says not all models are done, relaunch run_night
# (idempotent — finished stages are skipped). Stops when all 4 .onnx exist.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = "python"   # на PATH; при необходимости укажи полный путь к python.exe
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force logs | Out-Null
$MODELS = @("ey_talker", "stop_stop", "talker_stop", "stop_da")

function AllDone {
    $n = 0
    foreach ($m in $MODELS) { if (Test-Path "out\final\$m.onnx") { $n++ } }
    return $n -eq $MODELS.Count
}
function PipelineAlive {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -match "run_night|train\.py|gen_samples|prepare_data" }
    return [bool]$p
}

"[{0:HH:mm:ss}] guard started" -f (Get-Date) | Tee-Object -Append logs\guard.log
for ($tick = 1; $tick -le 480; $tick++) {     # 480 * 3 min = 24h ceiling
    if (AllDone) {
        "[{0:HH:mm:ss}] ALL 4 MODELS DONE - guard exits" -f (Get-Date) |
            Tee-Object -Append logs\guard.log
        exit 0
    }
    if (-not (PipelineAlive)) {
        "[{0:HH:mm:ss}] pipeline DOWN - relaunching run_night" -f (Get-Date) |
            Tee-Object -Append logs\guard.log
        Start-Process -FilePath $py -ArgumentList "-u","run_night.py" `
            -WorkingDirectory $PSScriptRoot -WindowStyle Minimized `
            -RedirectStandardOutput "logs\run_night_guard.log" `
            -RedirectStandardError "logs\run_night_guard_err.log"
        Start-Sleep -Seconds 20
    }
    Start-Sleep -Seconds 180
}
"[{0:HH:mm:ss}] guard ceiling reached" -f (Get-Date) | Tee-Object -Append logs\guard.log

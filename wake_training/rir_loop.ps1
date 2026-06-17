# Overnight resumable download of RIRS_NOISES (openslr/28) via built-in
# curl.exe (-C - resumes from byte offset). Slow shaped channel is fine:
# steady 64 KB/s gets the 1.3 GB zip done in ~6 hours.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force data | Out-Null
$dst = "data\rirs_noises.zip"
$urls = @(
    "https://www.openslr.org/resources/28/rirs_noises.zip",
    "https://openslr.elda.org/resources/28/rirs_noises.zip"
)
for ($i = 1; $i -le 400; $i++) {
    $u = $urls[$i % $urls.Count]
    "[{0:HH:mm:ss}] attempt {1}: {2}" -f (Get-Date), $i, $u
    & curl.exe -C - -L --max-time 900 --speed-limit 1024 --speed-time 90 -o $dst $u
    if ($LASTEXITCODE -eq 0) {
        "[{0:HH:mm:ss}] download complete: {1:N0} MB" -f (Get-Date), ((Get-Item $dst).Length/1MB)
        exit 0
    }
    Start-Sleep -Seconds 30
}
exit 1

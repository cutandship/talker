# Finish the venv under a shaped channel: big wheels via curl -C - (resume),
# small ones via pip, acoustics replaced by a local shim.
# Input: urls.txt ("<filename> <url>" per line, from fetch_wheel_urls.py)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = ".\venv\Scripts\python.exe"
New-Item -ItemType Directory -Force wheels | Out-Null

# 1) download every wheel with resume until done
$lines = Get-Content urls.txt | Where-Object { $_ -match "\S+\s+\S+" }
foreach ($line in $lines) {
    $parts = $line -split "\s+", 2
    $fname = $parts[0]; $url = $parts[1]
    $dst = "wheels\$fname"
    if (Test-Path $dst) {
        # already complete? curl will no-op if sizes match; just try resume
    }
    for ($i = 1; $i -le 200; $i++) {
        & curl.exe -C - -L --max-time 600 --speed-limit 2048 --speed-time 60 -s -o $dst $url
        if ($LASTEXITCODE -eq 0) { "DONE  $fname"; break }
        "retry $i  $fname (exit $LASTEXITCODE)"
        Start-Sleep -Seconds 20
    }
    if ($LASTEXITCODE -ne 0) { "GAVE UP on $fname"; exit 1 }
}

# 2) install local wheels in dependency order (torchaudio: --no-deps so pip
#    does not try to re-download torch)
& $py -m pip install --no-index --find-links wheels (Get-ChildItem wheels\llvmlite*.whl).FullName
& $py -m pip install --no-index --find-links wheels (Get-ChildItem wheels\numba*.whl).FullName
foreach ($w in @("soxr", "sentencepiece")) {
    $f = Get-ChildItem "wheels\$w*.whl" -ErrorAction SilentlyContinue
    if ($f) { & $py -m pip install --no-index --find-links wheels $f.FullName }
}
$ta = Get-ChildItem wheels\torchaudio*.whl -ErrorAction SilentlyContinue
if ($ta) { & $py -m pip install --no-deps $ta.FullName }

# 3) small packages from the index (retries; each is < 1 MB)
foreach ($p in @("audioread", "pooch", "lazy_loader", "msgpack", "decorator",
                 "joblib", "librosa<0.12", "audiomentations",
                 "torch-audiomentations", "hyperpyyaml", "pronouncing",
                 "speechbrain==0.5.14")) {
    $ok = $false
    for ($i = 1; $i -le 6 -and -not $ok; $i++) {
        & $py -m pip install --timeout 120 --retries 10 --quiet $p
        if ($LASTEXITCODE -eq 0) { $ok = $true; "OK  $p" }
        else { "retry($i)  $p"; Start-Sleep -Seconds 10 }
    }
    if (-not $ok) { "FAIL $p"; exit 1 }
}

# 4) acoustics shim + final import check (incl. openwakeword.data!)
& $py make_acoustics_shim.py
& $py -c "import acoustics, audiomentations, torch_audiomentations, speechbrain, torchaudio, pronouncing, librosa; import openwakeword.data; print('ENV-READY')"
exit $LASTEXITCODE

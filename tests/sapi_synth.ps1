param([string]$ListFile, [string]$OutDir)
# Локальный синтез через Windows SAPI (Irina, ru-RU) — без сети.
# Вход: TSV-файл "idx<TAB>текст" (UTF-8). Выход: $OutDir\idx.wav на каждую строку.
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $synth.SelectVoice('Microsoft Irina Desktop') } catch { }
$synth.Rate = 0
$n = 0
foreach ($line in [System.IO.File]::ReadAllLines($ListFile, [System.Text.Encoding]::UTF8)) {
    if (-not $line) { continue }
    $tab = $line.IndexOf("`t")
    if ($tab -lt 1) { continue }
    $idx = $line.Substring(0, $tab)
    $text = $line.Substring($tab + 1)
    $wav = Join-Path $OutDir "$idx.wav"
    $synth.SetOutputToWaveFile($wav)
    $synth.Speak($text)
    $n++
}
$synth.SetOutputToNull()
$synth.Dispose()
Write-Output "synth_done $n"

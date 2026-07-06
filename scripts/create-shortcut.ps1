# デスクトップに「窓なし起動」ショートカットを確実に生成する。
# 手作りだと .lnk のリンク先/引数の分割を誤りやすいので、TargetPath/Arguments/
# WorkingDirectory を明示セットして作る。
$ErrorActionPreference = "Stop"

$runScript = Join-Path $PSScriptRoot "run-vac.ps1"
if (-not (Test-Path $runScript)) { throw "run-vac.ps1 が見つかりません: $runScript" }
$repo = Split-Path -Parent $PSScriptRoot

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Voice Activate Claude.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $powershell
# -File の値はスペースを含み得るのでクォートで囲む(Arguments 欄内のクォートは "" で表す)
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`""
$lnk.WorkingDirectory = $repo
$lnk.WindowStyle = 7   # 7 = 最小化(念のため。実体は run-vac.ps1 が窓なしで動く)
$lnk.Description = "voice-activate-claude を窓なしで起動"
$lnk.Save()

Write-Host "作成しました: $lnkPath"
Write-Host "ダブルクリックで窓なし起動します"

# ログオン時に voice-activate-claude を窓なしで自動起動するタスクを登録する。
# 通常権限で実行(トレイ常駐・マイク・Claude Desktop操作には対話ユーザーセッションが必要。
# 管理者/最上位権限だとセッション分離でトレイが見えず失敗する)。
$ErrorActionPreference = "Stop"
$taskName = "VoiceActivateClaude"
$runScript = Join-Path $PSScriptRoot "run-vac.ps1"
if (-not (Test-Path $runScript)) { throw "run-vac.ps1 が見つかりません: $runScript" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# 対話セッションで通常権限。ネットワーク不問・バッテリーでも動かす。
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force

Write-Host "登録しました: タスク '$taskName'(次回ログオンから自動起動)"
Write-Host "今すぐ起動: schtasks /run /tn $taskName"

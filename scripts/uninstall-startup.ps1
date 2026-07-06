# 自動起動タスクを削除する。
$ErrorActionPreference = "Stop"
$taskName = "VoiceActivateClaude"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "削除しました: タスク '$taskName'"

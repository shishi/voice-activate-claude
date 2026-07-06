# voice-activate-claude を起動する。リポジトリの場所はこのスクリプトから相対解決するので
# どこにクローンしても動く。uv は PATH に無い場合があるため絶対パスも探す。
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot   # scripts/ の親 = リポジトリルート
Set-Location $repo

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    foreach ($candidate in @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe",
        "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    )) {
        if (Test-Path $candidate) { $uv = $candidate; break }
    }
}
if (-not $uv) { throw "uv が見つかりません。https://docs.astral.sh/uv/ を参照してインストールしてください" }

& $uv run python -m vac

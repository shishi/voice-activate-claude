# 起動方法 — windowless 起動 & ログオン自動起動

## 前提

- [uv](https://docs.astral.sh/uv/) インストール済み
- 初回モデルDL済み:
  ```
  uv run python -m vac.check wake
  uv run python -m vac.check whisper
  ```
- 必要に応じて `~/.config/voice-activate-claude/config.toml` の `input_device` を設定済み

---

## 方法 1 — Explorerからダブルクリックで窓なし起動(ショートカット)

コンソールウィンドウを一切出さずに起動するショートカットを作成します。

**推奨: 生成スクリプトで作る**(手作りは .lnk のリンク先/引数の分割ミスで起動しないことがある)。
リポジトリのフォルダで PowerShell から一度だけ実行:
```
powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1
```
リポジトリ直下に **Voice Activate Claude.lnk** ショートカットができる。ダブルクリックで
タスクバーに黒窓が残らず、トレイにアイコンだけ表示される(必要ならデスクトップ等へコピー可)。

<details><summary>手作りする場合(非推奨)</summary>

1. デスクトップを右クリック → **新規作成** → **ショートカット**
2. 「項目の場所」に以下を貼り付け(`<repo>` は実際のパスに変更):
   ```
   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<repo>\scripts\run-vac.ps1"
   ```
3. 名前を **Voice Activate Claude** に設定して完了。

うまく起動しない場合は生成スクリプト(上)を使うこと。
</details>

---

## 方法 2 — ログオン時の自動起動(推奨)

Task Scheduler を使ってログオン時に自動起動します。窓は一切出ません。

### 登録

PowerShell を開き、リポジトリ直下で以下を実行:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-startup.ps1
```

> 初回実行時に実行ポリシーの確認が出る場合は `Bypass` を指定しているため通常はそのまま進みます。

登録が完了すると、次回ログオンから自動起動します。

### 今すぐ起動

```
schtasks /run /tn VoiceActivateClaude
```

### 停止

トレイアイコンを右クリック → **終了**

### 自動起動の削除

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall-startup.ps1
```

---

## 確認方法

- **タスクスケジューラ**: `taskschd.msc` を実行 → タスク スケジューラ ライブラリ に `VoiceActivateClaude` が表示されていれば登録済み
- **ログ**: `~/.config/voice-activate-claude/vac.log`

---

## 注記

自動起動(方法 2)を設定した場合、手動ショートカット(方法 1)と併用すると二重起動になります。
起動中はトレイで「終了」してから別の方法で起動してください。

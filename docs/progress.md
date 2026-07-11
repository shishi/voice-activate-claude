# 進行状況と判断の経緯(セッション引き継ぎ用)

最終更新: 2026-07-11。長い開発セッションの状態を次回に引き継ぐためのメモ。
開発は WSL2、実行は Windows ネイティブ(別クローン)。テストは `uv run pytest`(現在 68 passed)。

## いまの一番の課題(未解決・ここから再開)

**注入が chat/Cowork トグルの取得で失敗する。**

- 症状: `vac.check inject` が `DeliveryError: controls not found or not ready: ['chat_mode']` で落ちる。`resolve chat-mode` が10秒 timeout を使い切って諦める。
- 確定した事実:
  - `vac.check tree` では `RadioButton name='チャット'` と `name='Cowork'` が**見えている**(存在はする)。
  - でも `_pick`(driver)は `is_visible() and is_enabled()` を要求しており、そこで弾かれている疑いが濃厚。tree は ready 判定をしないので見えるだけ。
  - 過去に注入成功していたのは**旧UIの「Chat」ボタン**。新UIの**チャット/Cowork RadioButton は一度も成功していない**=この ready 判定は未検証だった。
- 直前に入れた診断(commit `1ce66c2`): `_pick` が name+control_type 一致したのに ready で弾いたとき、`matched 'チャット'/RadioButton but not ready: visible=... enabled=...` または `ready-check raised ...` をログ出力する。
- **次にやること**: shishi が実機で `uv run python -m vac.check inject "診断テスト"` を1回実行 → 上記ログの visible/enabled の実値を見る。
  - `visible=False`/`enabled=False`(Electron の RadioButton がそう報告) → ready 判定を緩める(name+型一致だけで返す、または片方だけ見る、または click_input を試して失敗なら次へ)。
  - `ready-check raised` → is_visible/is_enabled 呼び出しが例外。try で握って通す。
  - 名前一致すらしない(ログが全く出ない) → name が「チャット」以外で露出。実名を調べる。
- **重要な自戒**: ready 判定はこれで何度も憶測パッチして失敗している(systematic-debugging の「3回直して同じ問題=アーキテクチャを疑え」に該当)。**必ず診断ログの実データを見てから直す**こと。

## 速度について(調査済み・結論)

注入は現状 20〜30秒台で「遅い」。内訳(実機計測):
- `find_window`: 約4秒
- `raise_foreground`: 約8秒(最小化→復元+settle×2)
- `descendants()` 1回: **約4.5秒**(要素数に無関係=Electron 巨大UIAツリーの全走査コスト)。注入で3〜4回呼ぶ。

確定した結論:
- **COMアパートメントモード(coinit_flags)は速度に無関係**。`--com-mode default/mta/sta` 全てで descendants は同じ4.5秒(Task 27 実験で確定)。この線は死んでいる。`vac.check inject --com-mode` は診断として残置。
- **バッチ化(1回スナップショットで全要素取得)は不可**。各操作(モード切替/新規チャット)でUIが再描画され、事前取得した wrapper が stale 化する(Codex が3回指摘)。→ 現状は「使う直前に1個ずつ resolve」= descendants を複数回、が正解。
- 残る速度レバー(未着手): `raise_foreground` の8秒短縮(最小化→復元のアニメ待ち見直し)、`find_window` のキャッシュ。descendants 自体は pywinauto/UIA の構造コストで削りにくい。

## 注入手順(現在の実装 = src/vac/adapters/claude_driver.py `_inject`)

Claude Desktop 新UI(Home/Code タブ、入力欄に チャット/Cowork トグル)向け:
1. `_raise_foreground` で前面化(set_focus→最小化復元→AttachThreadInput の多段、fail-closed)
2. チャットモード RadioButton を resolve→クリック(Cowork だと新規ボタンが「新しいタスク」になるため必須)
3. 新規チャット Button を resolve→クリック(**「新しいタスク」は絶対押さない**。無ければ fail-closed 中止)
4. 入力欄 Edit を resolve→クリック
5. Ctrl+A→Delete で前回の未送信テキストをクリア(各破壊キー直前に前面再確認)
6. クリップボード貼り付け(ClipboardGuard で退避復元)→ deliver で Enter 送信
- 各クリック/キー送信の直前に `_assert_foreground`(前面でなければ中止=誤爆防止)
- **Home クリックは除去済み**(commit `5a752ac`。Home クリックするとトグルが消える画面に飛ぶため)

## ユーザー(shishi)の確定要望

- 送信先は**常にチャットモードの「新規チャット」**(新しい会話)。「新しいタスク」には絶対送らない。
- 他アプリ作業中でも音声で送れる(=注入時に Claude を自動で前面化する)。
- マイクはトレイからいつでも切替(実装済み Task 19)。
- 起動は setup.cmd ダブルクリック / タスクスケジューラ自動起動(実装済み Task 20)。

## 実機で確認済みの環境事実

- マイク: 既定は無音の LARK(ワイヤレス)。実際使うのは **Logicool BRIO**。`config.toml` の `input_device` かトレイで指定。BRIO は MME/DirectSound/WASAPI で同名複数個 → index/ホストAPIで区別(トレイはラベルにAPI付き)。
- STT: faster-whisper でBRIO音声の日本語書き起こしは実機OK。
- ウェイク: hey_jarvis は score 1.00 で検知OK。hey_claude カスタムモデル(`models/hey_claude.onnx`, 単一ファイル.onnx)は**英語発音なら score 0.85 で発火、日本語発音だと拾わない**。日本語発音対応は録音した声(`voice_samples/`)で再学習が必要(未完)。
- Claude Desktop は Electron。入力欄は唯一の Edit。UI はアップデートで変わる(Chat/Cowork/Code → Home/Code へ変化した実績あり)。要素は id(base-ui-_r_...)が毎回変わるので **name+control_type で掴む**。

## 環境の落とし穴(再発しやすい)

- WSL2 で作るファイルの改行/エンコーディング: `.ps1` は UTF-8 **BOM必須**(PS5.1のShift-JIS読み回避)、`.cmd` は **BOM不可・CRLF必須**(`.gitattributes` で `*.cmd -text` にして literal CRLF を保存済み)。
- Windows uv は WSL の `.venv` を使えない(OS間で venv 非互換)。Windows 側に別クローン+別 venv。
- `.python-version` は 3.12(Nix Python 3.14 だと numpy が libstdc++ で死ぬため)。
- PowerShell に渡す `python -c` の長い一行はペースト時に折れて壊れる → 診断は `vac.check` サブコマンド化して渡す。

## 未完のタスク

- **Task 15**: hey_claude を日本語発音で拾う再学習(`voice_samples/` の実声を positive に混ぜて Colab or CoreWorxLab で再学習 → `models/hey_claude.onnx` 差し替え)。今は英語発音で運用可 or hey_jarvis に戻せる(`config.wake_model`)。
- **注入 chat_mode 失敗**(上記・最優先)。
- 速度の追い込み(raise_foreground 8秒 等、任意)。

## 開発の進め方(このプロジェクトの流儀)

- 変更は小さくコミット(Conventional Commits)。実装はサブエージェントに投げ、spec準拠レビュー→品質レビュー、マイルストーンや複数ファイル変更で Codex ゲート(`codex exec review --dangerously-bypass-approvals-and-sandbox --base <sha> --title ...`)を review→fix→re-review でクリーンまで回す。
- Windows専用コード(pywinauto/sounddevice/pystray/win32*)は WSL で import 不可 → `uv run pytest`(68) + `ast.parse` + `vac.check --help` で検証、実機確認は shishi。
- 診断は `vac.check` に足す(sound/mic/wake/vad/whisper/devices/tree/record/inject。inject は --settle, --com-mode, --exe, --device)。

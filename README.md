# voice-activate-claude

「hey claude」と話しかけるだけで、WindowsのClaude Desktopに音声で指示を送る常駐アプリ。

## 仕組み

ウェイクワード検知(openWakeWord)→ 発話録音(Silero VADで終話判定)→
文字起こし(faster-whisper, ローカル)→ Claude Desktopへ注入・自動送信(UI Automation)。
すべてローカル処理で、音声が外部に送られることはない。

## セットアップ(Windows)

1. [uv](https://docs.astral.sh/uv/) をインストール
2. `git clone` してリポジトリ直下で `uv sync`
3. 初回のみモデルをダウンロード:
   `uv run python -m vac.check wake` と `uv run python -m vac.check whisper` を一度ずつ実行
   (これを飛ばして常駐起動すると5秒毎のエラー音ループになるので注意)
4. (任意)`config.example.toml` を `~/.config/voice-activate-claude/config.toml` にコピーして調整
5. `uv run python -m vac` で常駐開始(タスクトレイにアイコンが出る)

> [!IMPORTANT]
> 設定ファイルは `~/.config/voice-activate-claude/config.toml` **のみ**。
> リポジトリ直下に `config.toml` を置いても読まれない(起動時にログへ警告が出る)。

## 動作確認

各コンポーネントを単体で診断できる:

    uv run python -m vac.check sound    # 効果音
    uv run python -m vac.check devices  # 入力デバイス一覧(indexとホストAPIを確認)
    uv run python -m vac.check mic      # マイク入力レベル
    uv run python -m vac.check wake     # ウェイクワード検知
    uv run python -m vac.check vad      # 発話検知
    uv run python -m vac.check whisper  # 文字起こし(5秒録音→表示)
    uv run python -m vac.check inject "テスト"  # Claude Desktopへの注入

### デバイス指定(--device)

OS既定のマイクが使いたいデバイスでない場合は、`devices` で一覧を確認して
config の `input_device`(名前の部分一致またはindex)で指定するか、
各 check コマンドに `--device` オプションを渡す:

    uv run python -m vac.check mic --device "BRIO"   # 名前の部分一致
    uv run python -m vac.check mic --device 21       # index 指定

同名デバイスが複数出る場合(例: BRIO は MME/DirectSound/WASAPI で1つずつ)は
index で指定する。トレイの「マイク」からもいつでも切り替えられる
(選択は config に保存される)。

### モデル指定(--model / config)

`wake` の `--model` は組み込みモデル名(既定 `hey_jarvis`)か `.onnx` ファイルの
パスを受け付ける:

    uv run python -m vac.check wake --model hey_jarvis
    uv run python -m vac.check wake --model models/hey_claude.onnx

常駐時のモデルは config で指定する: `wake_model`(既定 `hey_jarvis`)、
`whisper_model`(既定 `small`)。

### 注入のオプションと UI 診断

    uv run python -m vac.check inject "テスト" --settle 0.2               # 各操作後の待機秒(既定0.3)
    uv run python -m vac.check inject "テスト" --exe "C:\path\claude.exe"  # Claude の場所を明示
    uv run python -m vac.check tree    # UIAコントロール一覧(UIが変わったときの調査用)
    uv run python -m vac.check probe   # UIA走査の速度計測

注入が失敗するときはログを見る: `resolve failed ... dumping candidates`
(Claude の UI 変更でコントロール名/型が変わった)、`title matched but exe
rejected` / `window exe lookup failed`(ウィンドウ同一性の検証で弾いている)。

### ウェイクワード学習用の録音

    uv run python -m vac.check record --device 21 --phrase "ヘイ クロード"

`voice_samples/` に既定40本の wav を録音する(カスタムモデル再学習の素材)。

実機での通し確認は `docs/e2e-checklist.md` を参照。

## 自動起動 / Explorerから起動

窓なしショートカットや、ログオン時の自動起動(Task Scheduler)の設定方法は [`docs/startup.md`](docs/startup.md) を参照。

## 開発

コアロジックはWSL2/Linuxでもテストできる: `uv run pytest`

- 設計: `docs/superpowers/specs/2026-06-11-voice-activate-claude-design.md`
- 実装プラン: `docs/superpowers/plans/2026-06-11-voice-activate-claude.md`
- ログ: `~/.config/voice-activate-claude/vac.log`

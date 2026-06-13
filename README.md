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

## 動作確認

各コンポーネントを単体で診断できる:

    uv run python -m vac.check sound    # 効果音
    uv run python -m vac.check devices  # 入力デバイス一覧
    uv run python -m vac.check mic      # マイク入力
    uv run python -m vac.check wake     # ウェイクワード検知
    uv run python -m vac.check vad      # 発話検知
    uv run python -m vac.check whisper  # 文字起こし
    uv run python -m vac.check inject "テスト"  # Claude Desktopへの注入

OS既定のマイクが使いたいデバイスでない場合は、`devices` で一覧を確認して
config の `input_device` (名前の部分一致またはindex)で指定するか、
各 check コマンドに `--device` オプションを渡す。

実機での通し確認は `docs/e2e-checklist.md` を参照。

## 開発

コアロジックはWSL2/Linuxでもテストできる: `uv run pytest`

- 設計: `docs/superpowers/specs/2026-06-11-voice-activate-claude-design.md`
- 実装プラン: `docs/superpowers/plans/2026-06-11-voice-activate-claude.md`
- ログ: `~/.config/voice-activate-claude/vac.log`

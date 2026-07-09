# voice-activate-claude 設計ドキュメント

- 日付: 2026-06-11
- ステータス: ドラフト(ユーザーレビュー待ち)

## 1. 目的

Windows上で「hey claude」と話しかけるだけで、Claude Desktop(チャットアプリ)に
音声で指示を送れるようにする。具体的には:

1. ウェイクワード「hey claude」を常時待ち受ける
2. 検知後の発話を録音し、日本語テキストに変換する
3. Claude Desktop を探して(なければ起動して)テキストを入力欄に注入し、自動送信する
4. 返答はユーザーが画面で読む(読み上げはしない)

## 2. 決定事項

| 項目 | 決定 | 理由 |
|---|---|---|
| 対象環境 | Windows ネイティブのみ | ユーザー環境に合わせて限定。WSL2のマイク問題を回避 |
| 対象アプリ | Claude Desktop(チャットアプリ) | ユーザー指定 |
| 返答の受け取り | 画面で読む(TTSなし) | シンプル優先 |
| STT | faster-whisper(ローカル) | 無料・オフライン可・制御確実。Aqua Voiceは外部制御APIがなくホットキー擬似送信は失敗率の報告があるため不採用 |
| ウェイクワード検知 | openWakeWord | 完全OSS・登録不要 |
| ウェイクワード | 「hey claude」(カスタム学習) | 英語ワードなら公式の合成音声学習パイプラインで現実的。完成までは既製「hey jarvis」で代用 |
| 送信挙動 | 自動送信(フルハンズフリー) | ユーザー希望。誤検知対策は閾値調整で行う |
| テキスト注入方式 | ウィンドウ検出 + UI Automation(pywinauto) | ショートカット設定に依存せず、「未起動なら起動する」を自然に組み込める |

## 3. アーキテクチャ

Windowsネイティブで動くPython製の常駐タスクトレイアプリ(単一プロセス)。

ヘキサゴナル構成の縮小版を採る。コアの `Orchestrator` は副作用(マイク・Win32 API・
ファイル)を直接触らず、`ports.py` のインターフェース越しに各アダプタを使う。
これにより、コアのフロー制御は WSL2 上でフェイク実装を使って TDD できる。
Windows 依存はアダプタ層に閉じ込める。

### コンポーネント

| コンポーネント | 役割 | 主な依存 |
|---|---|---|
| `AudioListener` | マイクを常時ストリーミングし、音声フレームを下流に供給 | sounddevice |
| `WakeWordDetector` | フレームを監視して「hey claude」を検知 | openwakeword(.onnxモデル) |
| `CommandRecorder` | 検知後の発話を録音し、無音で喋り終わりを判定 | silero-vad |
| `Transcriber` | 録音を日本語テキスト化 | faster-whisper |
| `ClaudeDesktopDriver` | ウィンドウ探索→起動→前面化→テキスト注入→Enter | pywinauto(UIA) |
| `FeedbackPlayer` | 効果音で状態通知(開始♪/完了♪/エラー♪) | winsound |
| `Orchestrator` | 状態機械 `IDLE → RECORDING → TRANSCRIBING → DELIVERING → IDLE` | 各ポート |

### プロジェクト構成

```
voice-activate-claude/
├── src/vac/
│   ├── orchestrator.py      # 状態機械(コア、純粋ロジック)
│   ├── ports.py             # 各コンポーネントのインターフェース定義
│   ├── adapters/            # mic / wakeword / vad / whisper / claude_driver / sound
│   ├── config.py
│   ├── check.py             # 診断サブコマンド(python -m vac.check ...)
│   └── tray.py              # エントリポイント(pystray)
├── models/                  # hey_claude.onnx(学習後に追加)
├── tests/                   # ユニットテスト(WSL2でも実行可)
└── docs/
```

## 4. データフロー(正常系)

```
[常時] AudioListener → 80ms毎のフレーム → WakeWordDetector
  │
  ├─ スコアが閾値超え → Orchestrator: IDLE→RECORDING、開始音♪
  │
[録音] CommandRecorder がフレームを蓄積
  │     silero-vad で「1.5秒の無音」を検知したら録音終了
  │     (最大録音時間 30秒 でタイムアウト保護)
  │
[変換] Transcriber (faster-whisper, language=ja) → テキスト
  │
[注入] ClaudeDesktopDriver:
  │     1. EnumWindows で Claude Desktop のウィンドウを探索
  │     2. なければ claude.exe を起動し、ウィンドウ出現を最大15秒待機
  │     3. SetForegroundWindow で前面化
  │     4. UIA で入力欄を特定してフォーカス
  │     5. テキスト注入: UIA ValuePattern を試行、
  │        不可ならクリップボード経由 Ctrl+V にフォールバック
  │        (クリップボードの元の中身は退避→復元)
  │     6. Enter 送信
  │
[完了] 完了音♪ → IDLE に戻る
```

注入ステップ5の補足: Electron アプリのチャット入力欄は contenteditable のことが
多く、UIA ValuePattern が効かない可能性がある。クリップボード貼り付けは回避策では
なく当初からの設計要素とする。どちらが効くかは実機検証の最優先項目。

## 5. エラーハンドリング

方針: どの段階で失敗してもエラー音♪とログ記録を行い IDLE に復帰する。
常駐アプリはクラッシュしない。

| 失敗ケース | 挙動 |
|---|---|
| 録音が無音/認識結果が空 | エラー音。何も送らない |
| claude.exe が見つからない | エラー音+ログ。実行パスは設定で上書き可能 |
| 起動後15秒待ってもウィンドウが出ない | エラー音+ログ、IDLE 復帰 |
| 入力欄が見つからない/注入失敗 | エラー音+ログ。認識テキストはログに残す(発話内容を消失させない) |
| マイクデバイス消失 | 5秒間隔で再接続リトライ。トレイアイコンで異常表示 |

誤起動対策: ウェイクワード検知の閾値は設定ファイルで調整可能にする。
自動送信前提のため、誤検知による誤送信は閾値チューニングで抑える。

## 6. 設定ファイル(TOML)

最低限、以下を設定可能にする:

- ウェイクワードモデルのパスと検知閾値
- Whisper モデルサイズ(既定: small)と言語(既定: ja)
- 終話判定の無音秒数(既定: 1.5)と最大録音秒数(既定: 30)
- claude.exe のパス(既定: 標準インストール先を自動探索)
- 効果音の有効/無効

## 7. 技術スタック

- Python 3.11+ / uv(Windows 側にインストール)
- sounddevice / openwakeword / silero-vad / faster-whisper / pywinauto / pystray
- Whisper は small から開始。CPU で数秒程度の遅延を許容し、必要なら設定で変更

## 8. テスト戦略

CLAUDE.md の TDD 方針(Red → Green → Refactor)に従う。

1. **ユニットテスト(WSL2 で実行可、開発の主戦場)**
   - Orchestrator の状態遷移(各フェーズの成功・失敗・タイムアウト)
   - 終話判定ロジック(無音継続時間の計測)
   - 設定の読み込みとバリデーション
   - クリップボード退避・復元のロジック
   - すべてポートのフェイク実装で行う
2. **実機での単体検証(Windows)**
   - 診断サブコマンドを用意: `python -m vac.check mic` / `wake` / `inject "テスト"` 等
   - アダプタごとに切り分けて動作確認できるようにする
3. **E2E 手動シナリオ**
   - 「hey claude」→ 発話 → Claude Desktop に届いて送信される、をチェックリスト化して docs に置く

## 9. スコープ

### MVP(この順で作る)

1. 既製モデル「hey jarvis」で動くフルパイプライン(最大の不確実性 = UIA 注入の実機検証を最速で行う)
2. 「hey claude」カスタムモデルの学習(openWakeWord 公式の合成音声パイプライン、一回きりの作業)と差し替え
3. トレイ常駐・設定ファイル・効果音の仕上げ

### やらないこと(YAGNI)

- 返答の TTS 読み上げ
- 連続会話モード(ウェイクワードなしの追撃質問)
- GUI 設定画面(TOML 直編集で足りる)
- インストーラ/自動アップデート(uv コマンド起動で足りる)
- Windows 以外のサポート

## 10. リスクと実機検証ポイント

| リスク | 対処 |
|---|---|
| Electron の UIA ツリーで入力欄が特定できない | クリップボード+Ctrl+V フォールバックを最初から実装。最優先で実機検証 |
| Claude Desktop のウィンドウクラス名/実行パスがアップデートで変わる | 設定で上書き可能にし、探索条件を一箇所に集約 |
| 「hey claude」カスタムモデルの精度不足 | 閾値調整+既製モデルへの切り替えを設定で常時可能にしておく |
| CPU での Whisper が遅い | small で開始し、体感が悪ければモデル/ハードを見直す |
| SetForegroundWindow の制約(Windows はバックグラウンドからの前面化を制限する場合がある) | ドライバが `_raise_foreground()` で積極的に前面化する: まず pywinauto の `set_focus()`、失敗時は SW_MINIMIZE→SW_RESTORE、さらに前面スレッドへの AttachThreadInput + SetForegroundWindow を順に試みる。それでも前面化できない場合は既存の `_assert_foreground()` fail-closed ガードが注入を中止しエラー音で通知する。 |

- 実機検証: Claude DesktopはElectronで入力欄はChatタブの唯一のEditとして露出。UIA ValuePatternは不可のためクリップボード貼り付けに一本化し、注入前に必ずChatタブへ切り替える。
- 新UI対応(2026-07): Claude Desktop更新でタブがHome/Codeに、入力欄にチャット/Coworkトグル追加。注入は Home→チャットモード選択→新規チャット→Edit貼付→Enter。「新しいタスク」は使わない(fail-closed)。

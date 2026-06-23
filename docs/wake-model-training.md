# カスタムウェイクワード「hey claude」の学習手順

openWakeWord は Google のフリーズした Speech Embedding を土台に、合成音声(Piper TTS)
だけで小さな分類ヘッドを学習する。マイク録音は不要で、Colab(Linux)で完結する。

## 前提・要点

- 「hey claude」は英語フレーズなので openWakeWord の公式対応範囲(英語のみ)。
- 学習は Colab(無料 T4 GPU)で約1時間。合成クリップ生成は数十分。
- 自動学習スクリプトは Piper の都合で Linux 専用 → Colab は Linux なので問題なし
  (手元の Windows / WSL では学習しない。学習は Colab、利用は Windows)。
- 出力は **.onnx** を使う(本アプリは onnxruntime で読む。tflite は未使用)。

## 学習手順(Colab)

1. 公式ノートブックを Colab で開く:
   <https://github.com/dscripka/openWakeWord> の
   `notebooks/automatic_model_training.ipynb` を "Open in Colab"。
   ランタイムを **GPU(T4)** に設定する。
2. YAML 設定セルで `target_phrase` を `"hey claude"` にする。
   - ポジティブ(合成クリップ)は最低でも数千。多いほど精度が上がる。
   - ネガティブデータ(speech / noise / music)も使う。
   - **似た響きのネガティブは入れない**(例: "hey cloud" 等は逆効果。
     "hello" / "alexa" など明確に違う語を使う)。
3. ノートブックを上から全実行 → クリップ生成 → 学習。
4. 生成された hey_claude モデル(**.onnx**)をダウンロードする。

## 本アプリへの組み込み

1. ダウンロードした .onnx を Windows 側リポジトリの `models/hey_claude.onnx` に置く。
2. まず単体検証(BRIO 指定で):

   ```
   uv run python -m vac.check wake --model models/hey_claude.onnx --device BRIO
   ```

   → 「hey claude」で score が上がって `<<< WAKE!` が出るか、雑談で誤検知しないかを確認。
3. 良ければ config に設定。`~/.config/voice-activate-claude/config.toml` の
   `wake_model` を次のようにする:

   ```toml
   wake_model = "models/hey_claude.onnx"
   ```

   検知が渋い/過敏なら `wake_threshold` を調整(0.3〜0.5 あたり)。
4. `uv run python -m vac` で常駐起動 → 「hey claude」でフルパイプライン。

## 精度が出ないとき

- ポジティブクリップ数を増やす / 学習エポックを増やして再学習。
- それでも実用に耐えなければ、既製 "hey jarvis" 運用に戻す
  (config の `wake_model = "hey_jarvis"` に戻すだけ。いつでも切替可能)。

## 参考

- openWakeWord 本体: <https://github.com/dscripka/openWakeWord>
- 自動学習ノートブック: `notebooks/automatic_model_training.ipynb`
- Colab が不安定なときの代替ローカル学習: <https://github.com/CoreWorxLab/openwakeword-training>

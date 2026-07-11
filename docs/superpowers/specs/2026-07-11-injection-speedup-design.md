# 注入高速化(約33秒 → 目標5秒以下)設計

日付: 2026-07-11 / 状態: 承認済み(shishi)

## 問題

`vac.check inject` の全体所要が約33秒で、音声起動の実用に耐えない(shishi の要件:
何倍もの短縮が必要、目標5秒前後)。

## root cause(実機 `vac.check probe` で確定)

driver が保持する `window` は pywinauto の **WindowSpecification**(遅延解決の検索
条件オブジェクト)であり、メソッド呼び出しのたびにタイトル正規表現による全トップ
ウィンドウ検索(実測 約4.3秒)を再実行していた。

実測の分解(probe, 2026-07-11):

| 操作 | 実測 |
|---|---|
| タイトル regex でのウィンドウ検索(`wrapper_object()`) | 4.235s |
| `descendants()`(wrapper に対して直接) | 0.19s(511要素) |
| 生COM `FindAll`(TrueCondition) | 0.015〜0.031s |
| 生COM `FindFirst`(name+型条件) | 0.016s |
| `set_focus`(wrapper に対して直接) | 0.000s |
| `GetFocusedElement` | 0.000s |

これにより既存の観測が全て説明される:
- 「`descendants()` 4.5秒が要素数に無関係」= 検索4.3秒 + 実走査0.2秒
- 「`raise_foreground` 8秒」= `set_focus`(検索4.3秒)+ `is_active`(検索4.3秒)
- 「COM アパートメントモード無関係」= ボトルネックは COM でなく検索

## 設計

fail-closed(前面検証・名前+型の完全一致・掴めなければ中止)は一切変更しない。
変更は「ウィンドウの解決を何回やるか」だけ。

1. **ウィンドウ解決を deliver あたり最大1回にする**
   - `_find_window` は WindowSpecification ではなく実体の **UIAWrapper** を返す。
   - 以降の全操作(`set_focus` / `is_active` / `descendants`)はこの wrapper に
     対して行う(probe で実測済み: いずれも0.2秒以下)。
   - 子コントロールを使う直前に1個ずつ resolve する現行方針は維持(stale 対策。
     1回あたり4.5秒→約0.2秒になる)。トップウィンドウの wrapper は UI 再描画でも
     stale にならない(死ぬのは子要素)。
2. **最初のウィンドウ検索を Win32 列挙にする**
   - `win32gui.EnumWindows` + `GetWindowText` + `WINDOW_TITLE_RE` + `IsWindowVisible`
     で候補 hwnd を集め、`UIAElementInfo(handle=hwnd)` から wrapper を構築する。
     UIA 経由の全ウィンドウ照合(4.3秒)を回避する(見込み 0.1秒以下)。
   - **ウィンドウ同一性の検証(adversarial review 指摘の採用)**: タイトル regex
     だけでは "Claude ..." を名乗る他アプリ(ブラウザタブ等)を誤マッチしうるため、
     `GetWindowThreadProcessId` → プロセス実行ファイル名が Claude Desktop
     (claude.exe)であることも検証する。regex のみだった現行より厳しくなる方向の
     変更であり、fail-closed 性を強める。
   - **複数候補時の決定性**: exe 検証を通った候補が複数ある場合は EnumWindows の
     列挙順(Z順=手前が先)の先頭を採用する。現行の UIA 検索は複数マッチ時の
     挙動が非決定的だったため、これも改善方向。
   - 注記: `IsWindowVisible` は WS_VISIBLE を見るだけで**最小化ウィンドウでも
     TRUE** を返すため、最小化中の Claude は従来どおり見つかる。トレイ格納等で
     非表示(WS_VISIBLE 無し)の場合は見つからず `_launch` に進むが、Electron の
     単一インスタンス制御で既存ウィンドウが再表示される(現行と同挙動)。
3. **hwnd をインスタンス内でキャッシュする**
   - デーモン(常駐 orchestrator)は同じ driver インスタンスで繰り返し注入する。
     hwnd を保持し、次回は `IsWindow` + タイトル regex + プロセス exe の3点で
     生存・同一性を検証、どれか欠けたら破棄して再列挙する(hwnd は OS に
     再利用されるため liveness だけでは不十分 — adversarial review 指摘の採用)。
   - 注入が DeliveryError で失敗したときもキャッシュを破棄する(次回はフレッシュに
     列挙し直す)。

## 期待値

検索 ~0.1秒 + 前面化 ~0.5秒 + resolve 4回×0.2秒 + settle 0.3秒×4 + 貼付・送信
≈ **3秒前後**(初回起動の `_launch` 経路を除く)。

## 変更しないこと(YAGNI)

- キーボードショートカット(Ctrl+N)への置き換え: 文脈依存(タブ/モードで挙動が
  変わる)と実機確認されており不要になった。
- quick entry 小窓: パーソナル設定が反映されないため不採用。
- 生COM `FindFirst` への置き換え: descendants 0.2秒で十分。pywinauto wrapper の
  click_input 等の利便を維持する。
- settle 秒数の削減、`--com-mode` の削除: 本件のスコープ外。
- `send_keys` の TOCTOU(`_assert_foreground` とキー送信の間のフォーカス変化):
  現行と同一の既知の限界。UIA ValuePattern が使えない(実機確認済み)ため
  グローバルキー送信自体は避けられず、`type_keys` も内部は同じ。低頻度・
  既存リスクであり本設計では変更しない(adversarial review で指摘、スコープ外と
  判断)。

## エラーハンドリング

- hwnd キャッシュ無効(ウィンドウ閉鎖・再起動)→ 再列挙 → 見つからなければ
  現行どおり `_launch` → `_wait_for_window`。
- 前面化失敗・コントロール不在は現行どおり fail-closed(DeliveryError)。
- 失敗時の候補ダンプ(`_log_resolve_failure`)は温存。

## テスト・検証

- Windows 専用コードのため WSL では `uv run pytest`(68)+ `ast.parse` +
  `vac.check --help` で回帰確認(現行方針どおり)。
- 実機検証(shishi): `vac.check inject "診断テスト"` を Code タブ開始で実行し、
  (a) 正しくチャットモードの新規チャットに送信されること、
  (b) ログの各ステップ時計で合計5秒以下であることを確認する。
- probe コマンドは残置(将来の UI 変化・性能退行の切り分け用)。

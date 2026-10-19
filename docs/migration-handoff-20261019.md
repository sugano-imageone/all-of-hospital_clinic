# 2026-10-19 終業時の引継ぎ

ユーザー指示: 今日は終了し、残りは明日（2026-10-20、日本時間）に再開。
これを理由に本番切替、削除、課金変更、自動ジョブの停止/変更は行わない。

## 対象と保護

- sugano-imageone/all-of-hospital_clinic のみ。すガコン等の別システム/アカウントに触れない。
- 本番mainは8022e8e。実公開PagesとSupabase jnoukmkrtmpgchdpcskcのmaintenance=trueを確認済み。
- 移行用ブランチ: migration/turso-firebase。ドラフトPR: https://github.com/sugano-imageone/all-of-hospital_clinic/pull/5
- PR未マージ。現行PagesはSupabase版のまま。移行先へ医療データ・会員を投入していない。
- 利用者/権限/ログイン履歴、既存Supabaseのデータは削除していない。有料化していない。

## ユーザーが設定した状態（画面確認）

1. Turso: Organization sugano-imageone、Free、DB imageone-medical、東京。
   URL: libsql://imageone-medical-sugano-imageone.aws-ap-northeast-1.turso.io
   空DB、使用量0。tokenの作成は案内を保留している。
2. Firebase: project ID imageone-medical（コンソールURLでも確認）、Spark。
   Authentication開始済み、メール/パスワードが有効。
   承認済みドメインに sugano-imageone.github.io をCustomとして追加済み。
   Googleログインを勧めるオレンジ色の「有効にする」はGoogle用で、今回は不要と案内済み。
3. 最後に案内した操作: Firebaseの「プロジェクトの概要」を開き、次にWebアプリを登録する。
   Webアプリ登録はまだ画面確認していない。次回はここから、一段階ずつ実際の項目名で案内する。
4. Cloudflareはまだ未作成。接続シークレットも未設定。

認証情報をチャットやスクリーンショットへ貼らせない。DB tokenはブラウザのソースへ入れない。
Firebase署名検証だけなら管理サービスアカウントの秘密鍵を作らせる必要はない。
将来のTurso read-only接続確認は対象repoのActions secret TURSO_AUTH_TOKENと、必要ならvariable TURSO_DATABASE_URLで実施できるようにした。
worker/smoke-remote.mjsはSELECTのみ。migrationブランチのpush時だけ実行し、secret未設定ならskip。接続成功をまだ確認していない。

## 決まった保持方針（旧案を上書き）

- 新データの検証/採用後に前月の施設・届出データを削除する。
- ユーザーは「変更履歴・病床履歴も当月分だけ保持」を明示選択。
- 利用者・権限・アクセス/ログイン履歴は保持。
- 日本時間の更新サイクル月で区別し、公表基準日が古いという理由で採用中データを消さない。
- 失敗・未取得の局/県は前回分を残す。20日成功分を25日の再試行で消さない。
- 旧版/履歴の削除は復元アーカイブ照合後、新版採用と同じトランザクションで実施する検証コードを追加済み。
- 実際の本番削除はまだ行わない。移行先が検証完了するまで旧Supabaseを維持する。

## 実装と実測

- Firebase IDトークンの署名/プロジェクト/期限を検証し、WorkerでDBの承認・3権限・無効化を毎回確認する基盤。
- カバレッジ付き未届出検索、分割出力、更新中の出力中止、ログイン履歴、採用/退役/再試行のローカル検証。
- 前環境の原本がなかったため8局から再取得。47県、95,650施設、原本1,192,798明細、最新1,003,929組、未解決0を再現。
- 全国施設97,461、現在病床97,457、変更履歴195,698、病床履歴1,467,415、出典144をSQLiteへ実変換。
- 全履歴は出典の取得月による分類ですべて2026-10。履歴のNULL/出典孤立も調査し、対象行は全て出典へ紐付いている。
- 新方針の採用中1版＋当月履歴は通常2,280,357,888 bytes、最大局staging込み2,586,300,416 bytes。
- 大きな変更履歴のみ圧縮する比較案は通常1,889,751,040 bytes、ピーク2,195,693,568 bytes。
- 全国現存96,734施設のCSV/XLSXを生成・読戻し照合。分割圧縮でNode最大RSSを約1.38百万KiBから約609千KiBへ改善。実ブラウザは未確認。
- 無料枠: Turso公式5GB/月5億行読取り/1千万行書込み、Workers Free 10万req/日・CPU10ms・128MB、Firebase Spark。
- 容量だけなら収まる見込みだが、初回移行＋同月全更新＋全量再試行は書込み枠を超えうる。実使用量・停止閾値・同一内容スキップ・再開を検証してからスケジュール有効化する。

詳細: docs/turso-firebase-migration.md、migration/validation-summary.json。
原本/URL/SHA256/索引/容量試験/ログ: work/production-release/（git対象外、環境が変わると消失しうる）。
主要ファイル: medical-notifications.sqlite、medical-public.sqlite、notifications-current-only.sqlite、notifications-two-versions.sqlite、notifications-largest-staging.sqlite、history-compact-mixed.sqlite、各report.json。

## 検証と残作業

- 初回コミットはPostgreSQL付きGitHub CIで成功。
- 追加の保持方針はSQLiteテスト10件、届出Python29件、Worker17件、Firebaseクライアント4件が通過。最新CIは再開時に確認。
- 未完了: 実接続、Pages全画面統合、ユーザー作成/移行と旧ログ移行、全国出力の実ブラウザ、Worker CPU・Turso実課金行数・復元/削除の実環境検証。
- mainの既存notifications-monthly.ymlはJST20日/25日09:20のSupabase向け設定が残っている。変更・停止はしていない。明日（10/20）09:20が次の予定で、移行作業の再開前に動く可能性がある。次回は実行結果を読取確認する。
- 新規スケジュールやバックグラウンドの移行処理は作っていない。明日まで自律実行を継続しない。

# メンテナンスの切替

対象は `sugano-imageone/all-of-hospital_clinic` と Supabase project
`jnoukmkrtmpgchdpcskc`。会員の承認状態や権限、医療機関データは変更しない。

## 画面からのログイン停止

`web/site-settings.json` の `maintenance` を `true` にして Pages を公開すると、
ログインフォームを非表示にし「メンテナンス中」と表示する。公開クライアントは
認証・更新・データ取得を開始せずに停止する。通常画面へ戻すには `false` にして再公開する。

これは公開画面の切替であり、開いたままの古いクライアントや Supabase API への
直接アクセスの停止を保証しない。これらも制限する場合は、以下の DB 側の切替を併用する。
SQL ファイルをリポジトリに追加しただけでは本番 DB へは適用されない。

## DB 側も停止する場合

1. 対象プロジェクトを確認し、Management API の SQL エンドポイントで
   `sql/017_maintenance.sql` を適用する。初回適用時の状態は停止していない。
2. service_role で `POST /rest/v1/rpc/set_maintenance` に
   `{"p_active":true}` を送信する。匿名の `maintenance_status` RPC で
   `{"active":true}` を確認する。
3. `web/site-settings.json` の `maintenance` を `true` にして main に反映する。
   Pages 公開後、ログインフォームが非表示で「メンテナンス中」と表示される。

サーバー側では既存の承認判定と行レベル権限によりアプリケーションの利用を
停止する。既にページを開いている利用者も、次のデータアクセスから制限される。
Supabase Auth 自体のアカウントは変更しない。

## 再開

1. `web/site-settings.json` の `maintenance` を `false` にし、Pages の公開完了を確認する。
   サーバーの停止フラグが有効な間はログインできない。
2. service_role で `set_maintenance` に `{"p_active":false}` を送信する。
3. `maintenance_status` が `{"active":false}` であることと通常のログイン画面を確認する。

管理接続情報は環境のシークレットで設定する。トークンや秘密キーをリポジトリ、
ブラウザ、ログ、チャットへ出力しない。

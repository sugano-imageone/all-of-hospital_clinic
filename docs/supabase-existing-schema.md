# 公開後のSupabase調査（2026-10-15 JST）

クラウド環境の公開後、URL・publishable key・取込用secretの準備状態がreadyになり、
実際にSupabaseのREST APIへ接続できました。秘密値はログ・リポジトリへ保存していません。

以下の既存5テーブルがあり、サービス権限でのcount取得はいずれも0件でした。

| テーブル | 実際に確認した定義の特徴 |
| --- | --- |
| `medical_institutions` | UUID主キー、`insurance_medical_code`、`institution_name`、`telephone`、`operator_name`、`institution_type`、状態の既定値`active`。`source_import_id`はbigint |
| `institution_beds` | 施設UUIDが主キーの横持ち形式。`long_term_care_beds`、`infectious_disease_beds`、`clinic_bed_type`、`psychiatric_category` |
| `source_imports` | 主キーbigint、`publication_reference_date`、`retrieved_date`、`import_status`の既定値`pending` |
| `institution_changes` | 主キーbigint、旧新コード・`change_type`・`effective_date`・`review_status`・取込ID |
| `app_members` | Auth UUID、`role`の既定値`viewer`、`enabled`の既定値true |

これは初期実装のSQLと互換ではありません。**`001_initial.sql`は本番へ適用しません。**
リモートDBに書き込み・テーブル削除・再作成・データ登録は行っていません。

APIスキーマには列・型・必須項目・主キー・外部キーの情報はありますが、
CHECK制約・全インデックス・RLSのポリシー定義は含まれません。
既存の制約や権限を保持した追加移行を作るため、次にSQL Editorで
[`sql/004_inspect_existing.sql`](../sql/004_inspect_existing.sql) を実行し、単一JSON結果を確認します。
このSQLは読み取り専用で、個票・ユーザー情報・キーの値を取得しません。

環境公開と接続準備は完了していますが、本番のアプリ互換移行・ログイン・取込はまだ未完了です。
この追記が、以前の「キー未反映・既存テーブル未確認」という検証状況を更新します。

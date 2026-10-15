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

その後、ユーザーから読み取りSQLの結果を受領し、CHECK制約・全インデックス・RLS・トリガーまで確認しました。
確認結果の定義だけを `tests/fixtures/existing_schema.json` に記録しています。
この定義を隔離DBへ復元して、既存データを保持する移行SQLを作成・検証しました。
本番で実行するのは [`sql/010_upgrade_existing.sql`](../sql/010_upgrade_existing.sql) の全文です。
内容と検証は [既存Supabaseへの移行](upgrade-existing.md) を参照してください。

環境公開と接続準備は完了していますが、本番のアプリ互換移行・ログイン・取込はまだ未完了です。
この追記が、以前の「キー未反映・既存テーブル未確認」という検証状況を更新します。

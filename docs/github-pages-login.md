# GitHub Pages ログイン版

公開先: https://sugano-imageone.github.io/all-of-hospital_clinic/

## 初回設定（2026-10-15に適用・配置・公開済み）

1. Supabase SQL Editor で [015_master_role.sql](../sql/015_master_role.sql) の全文を実行します。対象アカウントは `sugano@imageone.co.jp`。結果が `master / true` になることを確認します。既存データは削除しません。
2. Supabase の Edge Functions → Deploy a new function → Via Editor で関数名を `admin-users` とし、[index.ts](../supabase/functions/admin-users/index.ts) の全文を貼り付けて Deploy します。関数の設定で **Verify JWT を OFF** にします。この関数自身が Supabase Auth と DB の現在の承認・権限を照合します。Supabase 標準の環境変数を使い、HTML に秘密キーを追加する必要はありません。
3. [GitHub Settings → Pages](https://github.com/sugano-imageone/all-of-hospital_clinic/settings/pages) の Build and deployment → Source を **GitHub Actions** にします。[Publish authenticated directory](https://github.com/sugano-imageone/all-of-hospital_clinic/actions/workflows/pages.yml) の Run workflow を実行します。

初回設定は利用者が管理画面で完了しました。このクラウド環境の GitHub 連携は Pages 設定 API に対して 403 を返します。また Supabase の REST 用秘密キーでは SQL の変更・Edge Function のデプロイはできません。このため初回の上記設定は各サービスの管理画面で行います。

## 権限

| 権限 | 操作 |
| --- | --- |
| マスター | 検索・集計・ダウンロード、ユーザー追加・承認・権限変更、サイト設定・ソース変更 |
| 管理者 | 検索・集計・ダウンロード、ユーザー追加・承認・管理者／閲覧者への権限変更 |
| 閲覧者 | 検索・集計の閲覧・ダウンロード |

マスターは指定した本人の UUID のみに制限します。管理者はマスターを降格・停止できません。利用者は自分の権限を変更できません。

マスターも共通ログインページでメールアドレスとパスワードを使います。パスワードは Supabase Auth が管理し、HTML・リポジトリには保存しません。ログイン後の「パスワード変更」でご自身のパスワードに変更できます。新しいパスワードは12文字以上、72バイト以下です。

サイト設定はマスターに表示される「サイト設定を変更（GitHub）」から [web/site-settings.json](../web/site-settings.json) を編集します。画面の構成・機能の変更はリポジトリの該当コードを編集します。GitHub への変更には別途 GitHub アカウントの書込み権限が必要です。main への保存後に GitHub Actions が公開を更新します。サイトのマスター認証が GitHub の権限を付与することはありません。

## 公開する情報とアクセス制御

GitHub Pages は HTML / JavaScript / CSS と Supabase の公開用 URL・publishable key のみを公開します。医療機関データ、利用者一覧、パスワード、service role / secret key は配信ファイルに含めません。医療機関データはログイン・承認後、Supabase の RLS を通じて取得します。ユーザー追加は認証・承認済みのマスター／管理者だけがサーバー関数を通じて実行します。

自己登録は用意せず、管理者がログイン後にメールアドレスをIDとしてユーザーを追加します。登録成功後にログインURL・ID・仮パスワード・権限を含む送付用メモを表示します。「メモをコピー」または「テキスト保存」で取り出し、管理者からメールで送信してください。仮パスワードの自動生成も可能です。追加先に通知メールは自動送信しません。メモは画面上で一時的に作成し、サーバーのログやリポジトリへ保存しません。利用者はログイン後に自分のパスワードを変更できます。

## ローカルビルド・検証

```sh
npm ci --prefix web --ignore-scripts --no-audit --no-fund
python -m scripts.build_pages
node --check web/pages.js
node --test web/admin-users.test.mjs
```

生成先は `artifacts/pages/`。データベースを複製したファイルは生成しません。権限移行の試験は `tests/test_master_role.py` で本番相当の使い捨て PostgreSQL スキーマに対して行います。

## 本番での確認結果（2026-10-15）

GitHub Pages 公開ワークフローの成功と、本人のマスター承認を確認しました。配置後の実際の `admin-users` 関数に対して、匿名アクセスの拒否、管理者によるメール付き利用者一覧取得、新規閲覧者の作成・承認・ログイン、閲覧者によるユーザー管理の拒否、管理者によるマスター降格の拒否を確認しました。試験用の2アカウントとその試験ログは削除済みです。メモ出力・パスワード変更の実ブラウザ試験は [検証状況](validation.md) に記載しています。

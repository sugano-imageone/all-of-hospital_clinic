# 全国医療機関データベース（イメージワン版）

FastAPI / Supabase Auth・PostgreSQL / 日本語Web画面 / Microsoft 365用Excel出力。
公開リポジトリのコードと、認証・承認が必要なデータ閲覧を分離します。

**保険医療機関の通常医科主一覧47都道府県・97,457件を本番Supabaseに登録し、公式PDF・Excel・DBの10桁コード一致を確認済みです。現存は95,959件（病院7,902・診療所88,057）。本番移行・Auth連携・権限・検索・出力も検証済みです。公開Webホスティングと定期実行の有効化は未実施です。**

収録範囲は[保険医療機関（医科）](docs/survey-scope.md)です。非保険施設を含み得る医療施設動態調査の総数とは区別します。
実データの収集範囲・件数・残件は [検証状況](docs/validation.md) を参照してください。

## 機能

- Supabase Authのパスワードログイン、HttpOnlyセッションCookie、トークン更新・ログアウト。
- 承認済み利用者のみ検索・集計・出力。管理者による利用者承認。サーバー側確認とDBのRLSを併用。
- 病院／診療所を起点に、厚生局・複数都道府県・市区町村・名称・コード・開設者／主体・病床種別／範囲・精神科分類を検索。
- 病院選択時は20〜200床が初期値。病床種別の範囲と施設区分は独立。50件ずつのページ送り、並べ替え、正確な件数表示。
- 既定は「現存のみ」。休止は任意で追加。廃止・辞退・取消・確認中は通常検索・出力から除外。
- 取得済み主一覧のダッシュボードと月次差分。収集県・除外件数・元資料の日付を表示。
- CSV・検索結果Excel・取得済み県のマスターExcel。6シート・フィルター・文字列コード・数式による検索フォーム。
- 公式ページの現在の医科全件リンク解決、ZIP/Excel解析、SHA-256、元行保存、個票連番／重複／日付／県の検証。
- 県単位のトランザクション取込・再実行時の重複防止・20%超の件数減少防止・消失候補の記録・元データ維持。
- コード変更関連・根拠付きの管理者判定を保持。新コードを純新規開業と決めつけません。
- JST毎月20〜25日の毎時実行・同日再試行のActions設定。運用前のためスケジュールは既定で無効。

## ローカル開発

Python 3.12以上を使用します。クラウドでは既存のチェックアウトを使ってください。
各タスクは隔離済みなので、ユーザーの指定がなければGit worktreeを作成しません。

```bash
cd /workspace/all-of-hospital_clinic
python -m venv /workspace/.venvs/hospital
/workspace/.venvs/hospital/bin/python -m pip install -r requirements.lock
/workspace/.venvs/hospital/bin/python -m pip check
```

環境変数の名前は `.env.example` にあります。クラウド環境では環境設定で値を登録します。
ローカルの `.env` を使う場合はコピーして本人が安全に編集し、信頼できる本人作成のファイルだけを読み込みます。

```bash
cp .env.example .env    # 初回のみ。既存ファイルを上書きしない
# .env を安全に編集した後:
set -a
. ./.env
set +a
# HTTPでの本人のローカル検証時のみfalse。本番HTTPSではtrue。
APP_SECURE_COOKIES=false /workspace/.venvs/hospital/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`GET /health` はプロセス生存と設定有無だけを返します。DB接続・ログイン・本番準備完了の証明には使いません。
未設定でもログイン画面を表示できますが、ログイン操作は503になります。
本番はHTTPSを終端するホスティング先で起動し、`APP_SECURE_COOKIES=true` を維持してください。
アプリを公開ホスティングへデプロイする設定はまだありません。

## Supabaseの設定（プロジェクト jnoukmkrtmpgchdpcskc）

1. このプロジェクトの既存5テーブルの列・型・CHECK制約・RLSを確認済みです。接続情報と環境公開も完了しています。
2. **対象プロジェクトへの `sql/010_upgrade_existing.sql` 適用は完了しました。再実行は不要です。** [移行の内容と検証](docs/upgrade-existing.md)を参照してください。
   `001_initial.sql` は適用しません。データ0件と空スキーマは別です。既存テーブルを削除して通す操作はしないでください。
   010〜013の本番適用は完了しています。47都道府県の医科主一覧97,457件を登録済みです。再適用は不要です。現存診療所88,057件と動態調査105,702件は対象範囲が異なります。[件数差の調査](docs/statistical-reconciliation.md)を参照してください。
3. 別の空スキーマの場合だけ `001_initial.sql` → `002_import.sql` → `003_update_jobs.sql` → `005_reporting.sql` を順に実行します。
   `tests/bootstrap.sql` はローカルテスト専用のAuthスタブです。Supabaseには絶対に適用しません。
4. Authenticationで管理者のアカウントを作成・招待します。公開の新規登録は無効にします。
5. 最初の管理者は、作成されたAuthアカウントとユーザー指定のID・メールを照合し、承認・昇格済みです。次のSQLは別環境での初期設定用です。

```sql
select id,email from auth.users;
-- 次のUUIDは実際の管理者のUUIDに置き換える。一般利用者にSQL Editor権限を渡さない。
update public.app_members
set role='admin',approved=true
where user_id='YOUR-ADMIN-AUTH-UUID'::uuid;
```

6. 環境設定に以下を登録します。秘密の値をチャットやGitHubのファイルに書かないでください。

| 名前 | 設定先・用途 |
| --- | --- |
| `SUPABASE_URL` | `https://jnoukmkrtmpgchdpcskc.supabase.co` |
| `SUPABASE_PUBLISHABLE_KEY` | Project Settings → API Keysのpublishable（またはlegacy anon）キー。Webサーバー用 |
| `SUPABASE_SECRET_KEY` | 同画面のsecret（またはlegacy service_role）キー。取込ジョブ専用の安全なSecretsへ |
| `APP_SECURE_COOKIES` | 本番は`true`。サーバーは既定でtrue |

Webアプリにservice_roleキーは不要です。ログインした利用者のトークンでDBへアクセスします。
取込CLIだけがservice_roleを使います。Webアプリと取込ジョブの認証情報は運用先で分離してください。
Authユーザー作成時は利用者レコードが未承認で追加されます。管理者は「利用者管理」で承認できます。
自分自身の権限変更はRPCで拒否します。アクセス履歴は管理者だけが閲覧できます。

## 九州8県の提供ZIP

社内確認用の検索デモ・Excel・説明資料は [レビュー用パッケージ](docs/review-package.md) を参照してください。
県別の取込順、同日再試行、5時間30分予算は [同日更新の設計](docs/same-day-updates.md) にまとめています。

```bash
/workspace/.venvs/hospital/bin/python -m app.importers.inspect --input /workspace/attachments
/workspace/.venvs/hospital/bin/python -m app.importers.cli --input /workspace/attachments --output data/validated
```

後者はローカルJSON・集計・Excelを生成します。DBへは書きません。
医科8ファイルと歯科8ファイルを内容確認し、薬局ファイルはユーザー指定により解析対象外です。
医科の補足行にある括弧付き歯科コードを、新規の医科個票として扱いません。
歯科を含む診療科目を理由に、医科の病院・診療所を除外しません。

DB移行と承認設定を確認した後だけ、取込ジョブ用の環境変数で実行します。

```bash
/workspace/.venvs/hospital/bin/python -m app.importers.cli --input /workspace/attachments --output data/validated --apply
```

## 全国の公式主一覧

```bash
# 公式ページの候補リンク調査（読み取り専用）
/workspace/.venvs/hospital/bin/python -m app.importers.discover
# 各厚生局の現在の医科全件リンクを解決し、配布物を取得・内容検証・Excel生成
/workspace/.venvs/hospital/bin/python -m app.importers.national --fetch --export
# 有効な取得物を再利用して解析・出力を再生成する場合
/workspace/.venvs/hospital/bin/python -m app.importers.national --export
```

結果は `data/national/report.json`、県別JSON、`data/national/全国医療機関マスター.xlsx` に出力します。
配布物・医療機関の個票・ExcelはGit管理対象外です。ファイルはアプリの静的公開パスにも配置しません。
**このCLIはローカル検証です。本番への一括投入はしません。** `monthly --apply` は検証済みの厚生局のみ登録できます。
解析に失敗した別表がある場合は報告し、スケジュール更新ではその厚生局の登録を止めます。
`national` の終了コード0は解析レポートを生成した意味であり、`errors` が空・全国完成を意味しません。

### 北海道の確定ルール（2026-10-15ユーザー指定）

- 総病床数空欄は0床・無床診療所。
- 総病床数20床以上は病院、0〜19床は診療所。名称による判定はしない。
- 一覧掲載施設は全件「現存」。
- 元の空欄と、運用ルールによる判定根拠を `raw` に保持する。
- 病床種別・診療科目・指定日など、資料にない情報はNULL／不明のまま。

これは北海道専用のルールです。他地域の空欄を根拠なく0床にはしません。

### 分類・差分の注意

- 病床の種類別件数は複数行の値を集約します。未解決の病床表記がある場合、総病床数はNULL・状態は確認中にします。
- 病院／診療所の明示と病床数が矛盾する場合も確認中にします。
- 精神病床のみ・80%以上・混合型は、病院で総病床数・精神病床数が判明している場合に算出します。精神科という診療科名だけでは断定しません。
- 初回取込はbaselineです。新コードだけでは新規指定・純新規開業にしません。
- 前回基準日より後の指定日と元資料の変更区分で裏付けられる追加は新規指定として記録します。法人化・承継・純新規の判定は根拠付き管理者確認待ちです。
- 原因区分が「組織変更」でも自動的に法人化にはしません。
- 同一資料のパーサー／運用ルール変更はreclassifiedとし、公式資料に基づく月次変更件数から外します。
- 一覧から消えた施設はmissing_candidateとして記録し、廃止状態へ変更・物理削除しません。
- 開設主体は文字列の法人種別から粗く分類します。公式の詳細な開設主体コードの確定ではなく、判定不能は「未分類」です。

## Excel

6シート：検索・抽出フォーム／集計ダッシュボード／病院／診療所／精神科病院／取得元・更新情報。
精神科病院は精神病床数が判明し1床以上の病院に限る部分集合です。
Microsoft 365の `LET / VSTACK / CHOOSECOLS / FILTER` を使い、地域・区分・病床種別・上下限・開設者で抽出する数式とドロップダウンを生成します。
先頭ゼロを維持し、元文字列がExcel数式として実行されないように処理します。

**Excel本体での動的配列再計算は未検証です。** この環境ではファイル構造・式・シート・フィルター・文字列コードの検査と出力まで確認しました。
実際のMicrosoft 365で条件変更・再計算・該当件数を受入確認するまでは、Excel内動的検索を完成扱いにしません。
CSVはUTF-8 BOMです。ExcelでCSVを直接開く際のコード列の自動数値化は、文字列列を指定したインポートで防いでください。

## 月次更新

`.github/workflows/monthly.yml` のUTC `0 19 19-24 * *` はJST20〜25日の04:00です。
GitHubのスケジュールは遅延する場合があります。
対象月・厚生局別の成功状態を `update_jobs` に保存し、同月の再実行時は成功済み厚生局をスキップします。
失敗した厚生局の中で、すでに正常取込できた県はSHA-256とパーサー版による再実行で重複しません。
県単位のトランザクションなので、別県の正常な取込まで巻き戻しません。

1. 上記Supabase設定、残る別表の確認、本番ログイン・取込検証を先に完了します。
2. GitHub → Settings → Secrets and variables → Actionsで、Variable `SUPABASE_URL`、Secrets `SUPABASE_PUBLISHABLE_KEY` / `SUPABASE_SECRET_KEY` を登録します。
3. Actionsから手動実行し、まず`apply=false`で内容検証します。解決していない厚生局は失敗として報告されます。
4. 検証後に`apply=true`で対象DBへ登録し、件数・履歴・RLSを確認します。
5. すべて完了してからVariable `ENABLE_MEDICAL_MONTHLY=true` を設定してスケジュールを有効化します。

検証CLI：`python -m app.importers.monthly`。本番登録：`python -m app.importers.monthly --apply`。
`--scheduled` はJST20〜25日以外には処理しません。
失敗時の要約はActions artifactとDBの更新ジョブ履歴に残ります。外部への通知連携は未実装です。

## テスト

```bash
python -m pytest -q
node --check app/static/app.js
python -m pip check
```

PostgreSQL統合テストは `TEST_DATABASE_URL` が必要です。ない場合はDBテストが明示的にskipされます。
CIではPostgreSQL 17を起動してAuthスタブとSQLを適用し、DB統合テスト・権限検査も実行します。
テストDBは必ず使い捨ての隔離DBにし、`tests/bootstrap.sql` を本番へ適用しないでください。

```bash
psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f tests/bootstrap.sql
psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/001_initial.sql -f sql/002_import.sql -f sql/003_update_jobs.sql -f sql/005_reporting.sql
python -m pytest -q
psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f tests/security.sql
```

クラウドでのSQL検証には隔離Docker DBを使っています。実際のSupabase Auth認証を代替するものではありません。
既存構造への移行テストには、Authスタブだけを適用した別の空DBを `TEST_EXISTING_DATABASE_URL` に指定します。
CIは両方のDBを作成して検証します。定義の復元・移行・テスト行は各テストのトランザクションでロールバックします。
共通のドメイン・取込処理は `app/domain.py` と `app/importers/` に分離しています。
将来の別ブランド・別Supabaseへの移植時も、実データや認証情報は引き継がず独立構築してください。

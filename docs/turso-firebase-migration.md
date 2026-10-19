# Turso + Firebase 移行検証（2026-10-19）

対象は **sugano-imageone/all-of-hospital_clinic** と Supabase **jnoukmkrtmpgchdpcskc** のみ。
これは移行検証の追加実装。main・Pages・Supabaseの変更、メンテナンス解除、課金変更は実施していない。
旧 `production-release-20261021.md` の Supabase 継続方針と無期限保存方針は、今回のユーザー指示で見直す。旧SQL 016〜019は取り消さない。

## 本番と作業の確認

- GitHub main: `8022e8edbdfde9dc60ea21faac9e01f476d627df`。PR #3はマージ済み。
- GitHub Actions の同コミットの Pages 公開とテストは success。
- 実際の公開HTML: HTTP 200、埋込みconfigの `maintenance: true`。
- Supabase: read_only 管理APIで `application_maintenance.active = true`。
- DB実容量: 1,337,439,923 bytes。届出を除く施設・履歴全体も移行対象。
- 採用済み: 北海道・東北。関東信越・東海北陸・近畿・中国四国はstaging。
- 旧作業ブランチは保存。最新mainから `migration/turso-firebase` を作成。
- 前環境の work/production-release 原本・索引は存在せず、公式8局から再取得した。
- ユーザーから3サービス未作成との回答後、Tursoの登録画面を確認。Organization `sugano-imageone` / `Free`、DBはまだ0件。Firebase/Cloudflareは未作成。環境への接続設定は未完了。

再取得結果: 47都道府県、95,650施設、原本1,192,798行、最新1,003,929組、最新判定未解決0。
URL・SHA256・取得時刻・ZIPメンバー・シート・原本行番号を work/production-release に保持。

## 構成と責任分界

| 層 | 配置・役割 |
|---|---|
| 画面 | 既存GitHub Pagesを継続。現在のSupabase版を維持し、移行版部品を別ファイルで検証 |
| 認証 | Firebase Authentication のメール/パスワード。Spark。SMS、Blaze、Identity Platformへの変更は行わない |
| 認可 | WorkerがIDトークンを検証した後、Tursoの承認・role・disabled・tokens_valid_afterを毎回確認 |
| データ | Tursoに全国施設・病床・履歴・採用済み届出を置く。病院だけに限定しない |
| API候補 | Cloudflare Workers Free。公開DBキー・任意SQL実行APIを提供しない |
| 月次 | GitHub Actions等のバッチ実行環境で取得・Excel解析・検証・転送。Worker内でExcel解析しない |
| 出力 | 同一検索条件でAPIからページ取得し、ブラウザでCSV/XLSX生成。更新中はrevision不一致で中止 |

`worker/wrangler.toml` はメンテナンス有効、接続先未設定。自動デプロイは追加していない。
Firebaseの公開Web APIキーと秘密の管理用キーは別物。IDトークンの署名検証に管理秘密鍵は不要。

## 実装済みの検証部品

- `migration/schema.sql`: SQLite/libSQL専用。初期メンテナンスON、承認/3権限/無効化、ログイン履歴、管理操作の追記記録、届出スナップショット、採用ポインタ、月次状態。
- `worker/src/auth.mjs`: Google公開鍵でRS256署名検証。aud/iss/sub/exp/iat/auth_time、メール確認済み、passwordプロバイダを検証。他プロジェクト、custom token、tenantを拒否。
- `worker/src/index.mjs`: 承認済み施設検索、届出メタデータ、出力ページ、本人情報、ログイン記録、管理者の閲覧者ログイン履歴・会員権限変更、マスタだけの収集ログ取得。
- 自己権限変更、マスタの変更・追加をAPI/DBで拒否。管理者は従来同様user/adminを管理できる。マスタ初期割当ては所有者確認後の別手順。
- ログイン履歴は署名検証済みUIDとauth_timeを使用。更新トークンで同じログインを重複記録しない。FirebaseにはSupabaseと同じsession_idがないため、同一UID・同一秒のサインインは1記録になる制約がある。過去履歴は別のlegacy session IDとして保存し、捏造しない。
- `web/firebase-client.mjs`: 未承認ログイン時のセッション破棄、更新、API接続、同条件の分割取得、版変更検出、CSVとXLSX。現行Pagesビルドにはまだ組み込んでいない。
- `scripts/migration/export_public.py`: 既存施設・病床・変更履歴・出典をread_onlyでSQLiteへ変換。主キー順・全件数・ローカル整合性・ダイジェストを記録。Auth、app_members、操作履歴をこの公開医療データ容量試験に混在させない。
- `convert_notifications.py`: 公式原本/展開ファイルを再ハッシュし、最新選択のversions・備考・原本参照を保持。重複する施設属性のみ除く。既存のpayload欠落を継承しない。
- `snapshots.py`: 全県範囲・件数・基準日・未解決・FK・元ポインタを確認後、トランザクションで採用。重複実行と基準日後退を拒否。20日は全局、25日は当月失敗局のみ。20日の状態がなければ25日は停止。
- `compact_history.py`: 大きな変更履歴を行単位zlib圧縮し、検索キーとSHA256を別保持。小さい病床履歴は通常の型・キーのまま保持するmixed方式を追加。全行を復元/チェックサム検証。元DB・元SQLiteは変更しない。
- `benchmark.py`: 実際のAPI生成SQLを全国データに実行し、CSV/Excelを生成・読み戻し件数照合。

## 未届出と原本保持

未届出は `validated` な採用版で、同じ局の施設カバレッジと対象項目カバレッジが両方存在する場合の非掲載だけ。
未取得の局・施設・項目を未届出にしない。stagingは通常検索から見えない。
既存の施設マスタを母集団とし、届出名簿だけの施設を勝手に施設マスタへ追加しない。

原本ZIP/XLSXはURLとSHA256を保持し、展開XLSXのSHA256も保存。基準日は県別に保持。
届出の原文の受理記号・受理番号・受理日・算定開始日・個別有効開始日、備考見出し/値、ファイル/シート/行を保持。
受理番号を日付の代用にしない。同日複数受理は複数versionsとして残す。

## 容量・利用枠の判定方法

PostgreSQLのサイズをTursoへ流用しない。計測ファイルは次のとおり。

1. `medical-public.sqlite`: 全国施設・現在病床・出典・変更履歴・病床履歴の全行。索引を追加しローカルVACUUM後に計測。
2. `notifications-two-versions.sqlite`: 全国最新データを別々のsnapshot IDへ2回投入し、索引込みの新旧2版相当を実測。**同一原本の複製試験であり、未到来の次月版を検証したものではない**。2版で2,303,930,368 bytes。
3. `history-compressed.sqlite`: 同じ全履歴をロスレス圧縮。主キー・施設・出典の索引込み。
4. `benchmark/benchmark-report.json`: 検索実行計画・件数・ローカル時間、全国CSV/XLSXの生成・読戻し。

実測集計は `migration/validation-summary.json` に記録する。
全国施設97,461件、現在病床97,457件、変更履歴195,698件、病床履歴1,467,415件、出典144件を変換した。
施設・全履歴は索引込み1,128,329,216 bytes、届出2版相当は2,303,930,368 bytes、最大局stagingは305,942,528 bytes。
合計の通常保持は約3.432GB、更新ピークは約3.738GB。mixed履歴方式は全履歴524,869,632 bytesで、通常約3.042GB、ピーク約3.348GB。
全行を一律圧縮してWITHOUT ROWIDに格納した試行は1.571GBに増えたため採用しない。圧縮payloadだけを見ず、キー・索引・SQLiteページの実容量で比較した。

書込みの計画値は初回約407万行、通常月次約179万行、全量再試行1回込み約358万行。実課金の観測値ではなく、既存データ全件・病床5種・全局更新を仮定したテーブル行数である。
COUNT/JOINの実読取り量は未計測なので「返す50件=50行読取り」と判断しない。DB別/組織全体のテレメトリで、通常利用・全国出力・月次検証の合算を確認する。

全国現存施設96,734件（病院7,935、診療所88,799）のCSV/XLSXを生成・読戻し検証した。
最初のXLSX方式のNode最大RSSは1,376,856KiBだったため、200行ずつXMLを圧縮するZIP生成へ変更。再測定は `validation-summary.json` のlocal_benchmarkを参照。ブラウザの実メモリは別途確認する。
SQLiteローカルで収まっても、Turso本番の使用量、転送、認証、Workers CPUを検証するまで成立確定にしない。
ローカルVACUUMは測定用。Turso公式の現行説明ではクラウド上のVACUUMは無効で、削除して即課金対象容量が縮むと仮定しない。

| サービス | 公式確認値・無料継続の条件 |
|---|---|
| Turso Free | 5GB、月5億行読取り、1,000万行書込み、sync 3GB。組織全体の他DB分も含めて確認。Overagesを有効化しない |
| Workers Free | 10万リクエスト/日、CPU 10ms/リクエスト、128MBメモリ。署名検証・JSON処理込みの実デプロイ計測が必須 |
| Firebase Spark | メール/パスワード認証を利用。メール送信制限（パスワード再設定150通/日等）を守る。Identity Platformを使う場合はSparkの3,000 DAU制限にも注意 |
| GitHub Actions | リポジトリの可視性・枠・artifact保管量を確認。90日artifactを唯一の復旧原本にしない |

読取り課金は返却件数だけではなくスキャン・JOIN・COUNTも対象。全国COUNTを各出力ページで繰り返さず、2ページ目以降はinclude_total=falseを使う。
初回移行・通常更新・失敗再試行・索引作成を別集計し、Tursoの実テレメトリで照合する。失敗/rollbackした書込みも課金上の行書込みに含まれうる。
月次届出は1,003,929明細+95,650施設+項目/メタデータが最低書込み量。施設更新・病床履歴追加・検索ログ・再試行を足して予算化する。

## 保持方針案（削除は未実施）

- 採用中と直前の届出版をオンライン保持。更新候補は局単位で作り、全国3版を一時的に重複させない。
- 第3版を削除できるのは、旧版の復旧用原本・manifest・parser版・正確な復元手順を利用者管理の保管先に退避し、復元試験が通った後だけ。
- 過去履歴は行単位圧縮または別の復旧可能な保管先に置く。検索に必要な施設・出典キーは索引を残す。将来の履歴は変更差分中心とし、同じ全文を毎月無期限に増やさない。
- 利用者・アクセス/ログイン履歴・既存Supabaseの履歴は、この保持方針を理由に無断削除しない。既存分は全件保持する移行手順を別途作る。
- 最終保管先は未決定。無料かつ無期限・無制限のバックアップは約束しない。枠不足時は更新を停止し前回採用版を保持する。
- 容量20%を運用余裕として確保する目標。最大局のstaging、失敗再試行、履歴増分を加え、5GBを超える構成を自動実行しない。

## アカウント設定は一段階ずつ

現在の段階では、秘密情報をチャットへ貼らない。すガコン用アカウント/プロジェクトを使わない。
作成済みサービスがあれば重複作成せず、非秘密のプロジェクト名/IDと所有者だけ確認する。

**最初の操作: Tursoアカウントの確認/作成のみ。**
`https://app.turso.tech/` → `Sign up`（既存なら `Log in`）。GitHubで続行する場合、表示されるGitHubアカウントが本件のイメージワン側であることを確認。`Free` を維持し、支払情報の追加・`Overages`・有料プランを選ばない。完了の報告にキーやtokenは不要。

続く段階は完了を確認してから案内する:

1. Turso: 正しいOrganizationを確認して `Create Database`。DB名と選択できるリージョンを確認。API用と更新用で権限を分け、URL/tokenを公開設定に入れない。
   今回の画面では `New Database`、`Name: imageone-medical` を候補とし、LocationにTokyoがあれば選ぶ。`Run this database on TursoDB...` はOFFのまま。現段階のSQL/SDK検証はSQLite/libSQL向けなので、別エンジンへの変更や組織のconcurrent writes有効化は行わない。
2. Firebase Console: `プロジェクトを作成` → 本件専用のプロジェクトIDを確認。`Spark` のまま。`構築` → `Authentication` → `始める` → `ログイン方法` → `メール/パスワード` のみ有効化。
3. `Authentication` → `設定` → `承認済みドメイン` に `sugano-imageone.github.io`。`プロジェクトの設定` → `全般` → Webアプリを登録。公開firebaseConfigと管理秘密鍵を区別する。画面表記が異なる場合はその時点で確認する。
4. Cloudflare: `Workers & Pages` → `Create application` → Workerを本件専用名で作成。Freeで検証。`Settings` → `Variables and Secrets` に環境値とsecretを分けて設定。
5. Worker秘密値: `TURSO_AUTH_TOKEN` を `Secret` または `wrangler secret put TURSO_AUTH_TOKEN` の非表示入力で設定。`TURSO_DATABASE_URL`、`FIREBASE_PROJECT_ID`、`ALLOWED_ORIGIN` は非秘密設定、`MAINTENANCE=true` を維持。Firebase JWT検証だけならサービスアカウント秘密鍵は作らない。
6. 月次バッチ用の別tokenが必要なら、GitHubの対象リポジトリ → `Settings` → `Secrets and variables` → `Actions` → `New repository secret` に保存。tokenをコマンド引数・ソース・ログ・チャットに貼らない。

既存ユーザーはメールアドレス・承認・role・旧UIDを対応付け、Firebase UIDを確定してから紐付ける。
ブラウザからの初回ログインだけで同じメールの権限を自動付与しない。
パスワードハッシュ互換性は未確認のため、既存パスワード移植を前提にしない。本人確認済みメールの引継ぎとパスワード再設定を基本案にする。
移行でメールを送る実操作はまだ行っていない。

## 本番切替前に残る検証

- 正しい3アカウントのFreeプラン・所有者・接続先確認。
- APIを隔離環境へデプロイし、Turso実データとFirebaseの実IDトークンで検証。署名鍵取得、トークン更新、失効・無効化・権限変更を通す。API側のdisabled操作をFirebase側無効化と必ず連携する（公開鍵検証だけではFirebaseの無効化を即時検出しない）。
- 新transportをPagesの全画面へ統合。ダッシュボード、ソース日付、管理者全列/全状態出力、全県マスター、管理者のユーザー作成、履歴の日付フィルタ等は現行互換の追加統合が必要。**今回の部品をそのまま本番の完成版として配信しない**。
- 利用者・権限・過去ログの安全な一括移行。メール確認状態と旧UID対応を照合。マスタは所有者承認済みの本人だけ。
- 実ブラウザで全国Excel生成のメモリ/待ち時間を確認。ローカルNodeの結果をブラウザの保証にしない。
- Tursoの最大局staging時ピーク、月次書込み量、再試行の二重投入防止、使用量の停止閾値、復旧を実環境で確認。
- 現在のmainには旧Supabase向け `notifications-monthly.yml` がJST20日/25日09:20に残る。まだ変更していない。移行版の同時実行や二重採用を起こさない切替手順を確認する。
- 初期容量試験は複数read_onlyクエリ。全表一貫の切替用バックアップではない。切替直前は更新を凍結し、整合した再エクスポートと件数/ハッシュ/FK検証が必要。
- 元の6届出スナップショットと途中投入内容はSupabaseに保存したまま。原本のハッシュ一致をpayload完全性の証拠としない。既存スナップショットの完全バックアップと分類を切替前に行う。
- ロールバックはPagesを旧版に保ち、旧Supabaseを削除せず停止維持。新認証ユーザーだけを作った段階でも旧ユーザーを削除しない。

## 公式確認先

- https://turso.tech/pricing
- https://docs.turso.tech/help/usage-and-billing
- https://developers.cloudflare.com/workers/platform/limits/
- https://firebase.google.com/pricing
- https://firebase.google.com/docs/auth/limits
- https://firebase.google.com/docs/auth/admin/verify-id-tokens

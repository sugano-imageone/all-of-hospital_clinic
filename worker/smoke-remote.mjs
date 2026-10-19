// Optional, read-only check using a masked GitHub Actions secret. No schema/data writes.
import {execute} from './src/db.mjs';
const expected='libsql://imageone-medical-sugano-imageone.aws-ap-northeast-1.turso.io';
if(!process.env.TURSO_AUTH_TOKEN) {
 process.stdout.write('Turso connection check skipped: repository secret is not configured.\n');
} else {
 try {
  if(process.env.TURSO_DATABASE_URL && process.env.TURSO_DATABASE_URL!==expected)throw Error('Project mismatch');
  const env={TURSO_DATABASE_URL:expected,TURSO_AUTH_TOKEN:process.env.TURSO_AUTH_TOKEN};
  const result=await execute(env,'SELECT 1 AS connected');
  if(result[0]?.connected!==1)throw Error('Unexpected result');
  const tables=await execute(env,"SELECT count(*) AS tables FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'");
  process.stdout.write(JSON.stringify({turso_read_only_connection:'ok',application_table_count:tables[0].tables,schema_or_data_written:false})+'\n');
 } catch {
  process.stderr.write('Turso read-only connection check failed. Verify the intended database and repository secret; no credential or provider response is logged.\n');
  process.exitCode=1;
 }
}

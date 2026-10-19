const JWKS = 'https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com';
let cache = null;
const deny = () => { throw Object.assign(new Error('Unauthorized'), {status:401}); };
function decode(value) {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) deny();
  return Uint8Array.from(atob(value.replaceAll('-', '+').replaceAll('_', '/') + '='.repeat((4-value.length%4)%4)), c => c.charCodeAt(0));
}
async function publicKeys() {
  if (cache && cache.expires > Date.now()) return cache.keys;
  const r = await fetch(JWKS, {redirect:'error'});
  if (!r.ok) throw Object.assign(new Error('Identity provider unavailable'), {status:503});
  const {keys} = await r.json();
  if (!Array.isArray(keys)) throw new Error('Invalid public keys');
  const age = Math.min(3600, Number(r.headers.get('cache-control')?.match(/max-age=(\d+)/)?.[1] || 300));
  cache = {keys, expires:Date.now()+age*1000};
  return keys;
}

export async function verifyIdToken(token, projectId, getKeys=publicKeys, now=Math.floor(Date.now()/1000)) {
  try {
    if (!projectId || typeof token !== 'string' || token.length > 16384) deny();
    const parts=token.split('.');
    if(parts.length!==3) deny();
    const header=JSON.parse(new TextDecoder().decode(decode(parts[0])));
    const claims=JSON.parse(new TextDecoder().decode(decode(parts[1])));
    if(header.alg!=='RS256' || typeof header.kid!=='string' || header.crit) deny();
    if(claims.aud!==projectId || claims.iss!==`https://securetoken.google.com/${projectId}` ||
       typeof claims.sub!=='string' || !claims.sub.length || claims.sub.length>128 ||
       !Number.isInteger(claims.exp) || claims.exp<=now || !Number.isInteger(claims.iat) || claims.iat>now ||
       !Number.isInteger(claims.auth_time) || claims.auth_time>now || claims.auth_time>claims.iat ||
       claims.iat>=claims.exp || claims.email_verified!==true || typeof claims.email!=='string' ||
       claims.firebase?.sign_in_provider!=='password' || claims.firebase?.tenant) deny();
    const jwk=(await getKeys()).find(k=>k.kid===header.kid && k.kty==='RSA' && (!k.alg||k.alg==='RS256'));
    if(!jwk) deny();
    const key=await crypto.subtle.importKey('jwk',jwk,{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['verify']);
    if(!await crypto.subtle.verify('RSASSA-PKCS1-v1_5',key,decode(parts[2]),new TextEncoder().encode(parts[0]+'.'+parts[1]))) deny();
    return claims;
  } catch(e) {
    if(e.status===503) throw e;
    deny();
  }
}

export function requireMember(claims, member, minimum='user') {
  const levels={user:0,admin:1,master:2};
  if(!member || member.firebase_uid!==claims.sub || Number(member.approved)!==1 || Number(member.disabled)!==0 ||
     !Object.hasOwn(levels,member.role) || !Object.hasOwn(levels,minimum) || levels[member.role]<levels[minimum] ||
     member.email.toLowerCase()!==claims.email.toLowerCase() || claims.auth_time<Number(member.tokens_valid_after)) {
    throw Object.assign(new Error('Approval or permission required'),{status:403});
  }
  return member;
}

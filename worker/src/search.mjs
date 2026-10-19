const bad=()=>{throw Object.assign(new Error('Invalid search conditions'),{status:400});};
export const PUBLIC_FIELDS=['id','code','prefecture_code','prefecture','bureau','municipality','name','postal_code','address','phone','owner','owner_type','manager','departments','kind','clinic_beds','total_beds','general_beds','medical_beds','psychiatric_beds','tuberculosis_beds','infectious_beds','psychiatric_class','needs_review','designation_date','renewal_date','change_reason','opening_classification','status','reference_date','file_created_date','retrieved_at','source_file'];
export function buildSearch(q={}) {
  if(!q || typeof q!=='object' || Array.isArray(q)) bad();
  const where=[],args=[];
  const eq=(key,value)=>{where.push('m.'+key+'=?');args.push(value);};
  if(q.kind) {if(!['病院','診療所'].includes(q.kind))bad();eq('kind',q.kind);}
  where.push(q.include_suspended===true?"m.status IN ('現存','休止')":"m.status='現存'");
  if(q.prefectures?.length) {
    if(!Array.isArray(q.prefectures)||q.prefectures.length>47||q.prefectures.some(v=>typeof v!=='string'||v.length>10))bad();
    where.push('m.prefecture IN ('+q.prefectures.map(()=>'?').join(',')+')');args.push(...q.prefectures);
  }
  for(const k of ['bureau','owner_type','clinic_beds','psychiatric_class','code']) if(q[k]) {if(typeof q[k]!=='string'||q[k].length>100)bad();eq(k,q[k]);}
  for(const k of ['municipality','name','owner']) if(q[k]) {
    if(typeof q[k]!=='string'||q[k].length>200)bad();
    where.push(`m.${k} LIKE ? ESCAPE '\\'`);args.push('%'+q[k].replace(/[\\%_]/g,v=>'\\'+v)+'%');
  }
  if(q.clinic_beds==='無床')where.push('m.total_beds=0');
  if(q.clinic_beds==='有床')where.push('m.total_beds BETWEEN 1 AND 19');
  const bed=['total','general','medical','psychiatric','tuberculosis','infectious'].includes(q.bed_type)?q.bed_type:'total';
  for(const [key,op] of [['minimum','>='],['maximum','<=']])if(q[key]!=null) {
    if(!Number.isInteger(q[key])||q[key]<0||q[key]>100000)bad();where.push(`m.${bed}_beds${op}?`);args.push(q[key]);
  }
  if(q.minimum!=null&&q.maximum!=null&&q.minimum>q.maximum)bad();
  if(q.use_notifications===true) {
    if(typeof q.notification_item!=='string'||!q.notification_item.trim()||q.notification_item.length>300||!['filed','unfiled'].includes(q.notification_status))bad();
    const item=q.notification_item.normalize('NFKC').replace(/\s/g,'');
    where.push(`EXISTS (SELECT 1 FROM notification_current nc
      JOIN notification_snapshots ns ON ns.id=nc.snapshot_id AND ns.state='validated'
      JOIN notification_facilities nf ON nf.snapshot_id=nc.snapshot_id AND nf.code=m.code
      JOIN notification_items ni ON ni.snapshot_id=nc.snapshot_id AND ni.item_key=?
      WHERE nc.bureau=m.bureau AND ${q.notification_status==='unfiled'?'NOT ':''}EXISTS
      (SELECT 1 FROM notification_filings n WHERE n.snapshot_id=nc.snapshot_id AND n.code=m.code AND n.item_key=ni.item_key))`);
    args.push(item);
    // Match the existing notification search semantics even with suspended selected.
    where.push("m.status='現存'");
  }
  const page=q.page??1,limit=q.page_size??50;
  if(!Number.isInteger(page)||page<1||page>100000||!Number.isInteger(limit)||limit<1||limit>1000)bad();
  const sort=['code','name','prefecture','total_beds','reference_date'].includes(q.sort)?q.sort:'code';
  const direction=q.direction==='desc'?'DESC':'ASC';
  const from=' FROM medical_institutions m WHERE '+where.join(' AND ');
  let itemFrom=from,itemArgs=[...args],offset=(page-1)*limit;
  if(q.after_code!=null) {
    if(sort!=='code'||direction!=='ASC'||typeof q.after_code!=='string'||!/^\d{10}$/.test(q.after_code))bad();
    itemFrom+=' AND m.code>?';itemArgs.push(q.after_code);offset=0;
  }
  const order=sort==='code'?'m.code '+direction:`m.${sort} IS NULL,m.${sort} ${direction},m.code ASC`;
  return {sql:'SELECT '+PUBLIC_FIELDS.map(k=>'m.'+k).join(',')+itemFrom+` ORDER BY ${order} LIMIT ? OFFSET ?`,
    args:[...itemArgs,limit,offset], countSql:'SELECT count(*) AS total'+from,countArgs:args,page,page_size:limit};
}

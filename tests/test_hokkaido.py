from app.importers.hokkaido import parse_hokkaido

def test_approved_user_policy_keeps_source_blank_and_bed_types_unknown():
    rows=[[None]*34 for _ in range(9)]
    rows[1][14]='郡市区別医療機関一覧表';rows[3][3]='令和8年10月1日現在 医科';rows[3][24]='令和8年10月1日作成'
    for i,beds in [(7,None),(8,'20')]:
        r=rows[i];r[2]=str(i-6);r[6]='01,1021,0' if i==7 else '01,1274,5';r[10]='名称に病院を含む例';r[13]='〒064－0807札幌市中央区';r[27]='医';r[30]=beds
    records,sources=parse_hokkaido(b'content','test.xlsx','F450A',rows)
    assert records[0]['total_beds']==0 and records[0]['kind']=='診療所' and records[0]['clinic_beds']=='無床'
    assert records[0]['raw']['source_total_beds'] is None
    assert records[0]['raw']['total_beds_origin']=='user_rule_blank_as_zero'
    assert records[1]['total_beds']==20 and records[1]['kind']=='病院'
    assert all(r['status']=='現存' and r['psychiatric_beds'] is None for r in records)

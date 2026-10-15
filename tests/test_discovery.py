import pytest
from app.importers.discover import current_links,official_url

def test_full_medical_scope_not_facility_standards_or_dental():
    html='''<table><caption>コード内容別医療機関一覧表</caption><tr><td>医 科</td><td><a href="/shikoku/full.zip">ZIP</a></td></tr><tr><td>歯科</td><td><a href="/shikoku/dental.zip">ZIP</a></td></tr></table><table><caption>施設基準</caption><tr><td>医 科</td><td><a href="/shikoku/standards.zip">ZIP</a></td></tr></table>'''
    assert current_links('四国厚生支局',html)==['https://kouseikyoku.mhlw.go.jp/shikoku/full.zip']

def test_ambiguous_latest_link_fails_closed():
    html='<a href="/tohoku/shitei-touhoku-ika-r0810.xlsx">医科</a><a href="/tohoku/shitei-touhoku-ika-r0809.xlsx">医科</a>'
    with pytest.raises(ValueError):current_links('東北厚生局',html)

def test_only_official_https_destinations():
    for url in ['http://kouseikyoku.mhlw.go.jp/a','https://other.example/a','https://user@kouseikyoku.mhlw.go.jp/a','https://kouseikyoku.mhlw.go.jp:444/a']:
        assert not official_url(url)

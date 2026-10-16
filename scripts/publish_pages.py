"""Publish only the built, data-free static site to gh-pages without a checkout."""
from pathlib import Path
import os,subprocess,tempfile

def git(*args,env=None):return subprocess.check_output(['git',*args],env=env,text=True).strip()
def main():
 root=Path.cwd();site=root/'artifacts/pages';assert (site/'index.html').is_file()
 allowed={'index.html','.nojekyll','JSZip-LICENSE.txt','static/app.js','static/pages.js','static/jszip.min.js','static/style.css','static/user-guide.html'}
 assert {str(p.relative_to(site)) for p in site.rglob('*') if p.is_file()}==allowed
 secret=os.getenv('SUPABASE_SECRET_KEY','');assert secret and all(secret not in p.read_text() for p in site.rglob('*') if p.is_file())
 remote=git('ls-remote','--heads','origin','gh-pages');parent=None
 if remote:
  subprocess.run(['git','fetch','origin','gh-pages:refs/remotes/origin/gh-pages'],check=True);parent=git('rev-parse','refs/remotes/origin/gh-pages')
 with tempfile.TemporaryDirectory(prefix='hospital-pages-index-') as temp:
  env={**os.environ,'GIT_INDEX_FILE':str(Path(temp)/'index'),'GIT_WORK_TREE':str(site),'GIT_DIR':str(root/'.git')}
  git('read-tree','--empty',env=env);git('add','-A',env=env);tree=git('write-tree',env=env)
  if parent and tree==git('rev-parse',parent+'^{tree}'):print('gh-pages already matches the validated build');return
  commit=git('commit-tree',tree,*(['-p',parent] if parent else []),'-m','Publish authenticated medical directory',env=env)
  subprocess.run(['git','push','origin',commit+':refs/heads/gh-pages'],check=True)
 print('Published the built client branch; Pages source must be gh-pages / root')
if __name__=='__main__':main()

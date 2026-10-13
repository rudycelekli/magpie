"""Run mandatory native gates on immutable source in a temporary HOME."""
import argparse,datetime,hashlib,json,os,platform,shutil,stat,subprocess,tempfile,sys
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['baseline','fixed']);parser.add_argument('source',type=Path);parser.add_argument('proof',type=Path)
a=parser.parse_args();source=a.source.resolve();proof=a.proof.resolve();proof.mkdir(parents=True,exist_ok=True)
harness=Path(__file__).resolve().parent;manifest=json.loads((harness/'manifest.json').read_text());expected=manifest['phases'][a.phase]
commands=[];before=None;home=None;env=dict(os.environ)

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def git(*args):return subprocess.check_output(['git',*args],cwd=source).decode().strip()
def working_bytes(name):
 file=source/name
 return os.fsencode(os.readlink(file)) if file.is_symlink() else file.read_bytes()
def hashes():
 names=subprocess.check_output(['git','ls-files','-z'],cwd=source).decode().split('\0')
 return {n:hashlib.sha256(working_bytes(n)).hexdigest() for n in names if n}
def verify_git_blobs(label):
 entries=[]
 for raw in subprocess.check_output(['git','ls-tree','-r','-z','HEAD'],cwd=source).split(b'\0'):
  if not raw:continue
  metadata,name=raw.split(b'\t',1);mode,kind,oid=metadata.decode().split()
  if kind!='blob':raise RuntimeError('Unsupported non-file tree entry')
  entries.append((mode,oid,name.decode()))
 payload=subprocess.check_output(['git','cat-file','--batch'],cwd=source,input=''.join(oid+'\n' for _,oid,_ in entries).encode())
 cursor=0;checks={}
 for mode,oid,name in entries:
  end=payload.index(b'\n',cursor);actual_oid,kind,size=payload[cursor:end].decode().split();cursor=end+1;size=int(size)
  expected_bytes=payload[cursor:cursor+size];cursor+=size
  if actual_oid!=oid or kind!='blob' or payload[cursor:cursor+1]!=b'\n':raise RuntimeError('Git batch receipt mismatch')
  cursor+=1
  file=source/name;actual_bytes=working_bytes(name)
  check={'mode':mode,'git_blob':oid,'expected_sha256':hashlib.sha256(expected_bytes).hexdigest(),'actual_sha256':hashlib.sha256(actual_bytes).hexdigest(),'storage':'symlink' if file.is_symlink() else 'file'}
  checks[name]=check
  if actual_bytes!=expected_bytes or (mode!='120000' and file.is_symlink()):
   (proof/(label+'.json')).write_text(json.dumps(checks,indent=2));raise RuntimeError('Working bytes differ from Git blob: '+name)
 (proof/(label+'.json')).write_text(json.dumps(checks,indent=2))
def run(label,argv,expected_exit=0,extra=None):
 start=now();process_env=dict(env);process_env.update(extra or {})
 with (proof/(label+'.stdout')).open('wb') as out,(proof/(label+'.stderr')).open('wb') as err:
  try:
   r=subprocess.run(argv,cwd=source,env=process_env,stdout=out,stderr=err,timeout=7200)
   code=r.returncode
  except subprocess.TimeoutExpired:
   code=None
 commands.append({'label':label,'argv':argv,'start':start,'end':now(),'exit':code,'expected_exit':expected_exit,'extra_env':extra or {},'stdout_sha256':hashlib.sha256((proof/(label+'.stdout')).read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256((proof/(label+'.stderr')).read_bytes()).hexdigest()})
 (proof/'commands.json').write_text(json.dumps(commands,indent=2))
 if code!=expected_exit:raise RuntimeError(f'{label}: exit {code}, expected {expected_exit}')
 return (proof/(label+'.stdout')).read_text()

try:
 system=platform.system();runner=os.environ.get('RUNNER_OS')
 if {'Linux':'Linux','Windows':'Windows','macOS':'Darwin'}.get(runner)!=system:raise RuntimeError('Native OS identity mismatch')
 go_version=subprocess.check_output(['go','version'],text=True).strip();bun_version=subprocess.check_output(['bun','--version'],text=True).strip()
 (proof/'runtime-probe.json').write_text(json.dumps({'go_version':go_version,'bun_version':bun_version},indent=2))
 if go_version.split()[2]!=manifest['go_version'] or bun_version!=manifest['bun_version']:raise RuntimeError('Pinned runtime mismatch')
 go_env=json.loads(subprocess.check_output(['go','env','-json'],text=True));head=git('rev-parse','HEAD');tree=git('rev-parse','HEAD^{tree}')
 environment={'system':system,'platform':platform.platform(),'machine':platform.machine(),'runner_os':runner,'go_version':go_version,'bun_version':bun_version,'head':head,'tree':tree,'go_env':go_env,'harness_head':os.environ.get('GITHUB_SHA'),'source':str(source),'proof':str(proof),'harness':str(harness),'python_version':sys.version,'autocrlf':git('config','--get','core.autocrlf')}
 (proof/'environment.json').write_text(json.dumps(environment,indent=2))
 if head!=expected['head'] or tree!=expected['tree'] or git('status','--porcelain','--untracked-files=all'):raise RuntimeError('Source identity or cleanliness mismatch')
 fixture=harness/'backup-retention-fixture.go'
 fixture_bytes=fixture.read_bytes()
 fixture_blob=subprocess.check_output(['git','show','HEAD:qualification/backup-retention-fixture.go'],cwd=harness)
 fixture_receipt={'expected_sha256':manifest['fixture_sha256'],'actual_sha256':hashlib.sha256(fixture_bytes).hexdigest(),'git_blob_sha256':hashlib.sha256(fixture_blob).hexdigest(),'actual_bytes':len(fixture_bytes),'git_blob_bytes':len(fixture_blob),'actual_crlf_count':fixture_bytes.count(b'\r\n'),'git_blob_crlf_count':fixture_blob.count(b'\r\n')}
 (proof/'fixture-bytes.json').write_text(json.dumps(fixture_receipt,indent=2))
 if fixture_bytes!=fixture_blob or fixture_receipt['actual_sha256']!=manifest['fixture_sha256']:raise RuntimeError('Reviewed fixture hash mismatch')
 if a.phase=='fixed':
  for name,digest in manifest['source_files_sha256'].items():
   if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Fixed source bytes mismatch: '+name)
 verify_git_blobs('git-blobs-before')
 before=hashes()
 if {f.relative_to(source).as_posix() for f in source.rglob('*.go')}!={name for name in before if name.endswith('.go')}:raise RuntimeError('Untracked Go source starts present')
 (proof/'tracked-before.json').write_text(json.dumps(before,indent=2))
 (proof/'harness-hashes.json').write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in harness.iterdir() if f.is_file()},indent=2))
 # Pin caches before changing HOME, matching the repository's test contract.
 home=Path(tempfile.mkdtemp(prefix='magpie-qualification-home-'))
 env.update(HOME=str(home),USERPROFILE=str(home),XDG_CONFIG_HOME=str(home/'.config'),APPDATA=str(home/'AppData/Roaming'),LOCALAPPDATA=str(home/'AppData/Local'),GOPATH=go_env['GOPATH'],GOMODCACHE=go_env['GOMODCACHE'],GOCACHE=go_env['GOCACHE'],GOTOOLCHAIN='local',GOMAXPROCS='2',GOFLAGS='-p=2 -mod=readonly')
 (proof/'sandbox.json').write_text(json.dumps({k:env[k] for k in ['HOME','USERPROFILE','XDG_CONFIG_HOME','APPDATA','LOCALAPPDATA','GOPATH','GOMODCACHE','GOCACHE','GOTOOLCHAIN','GOMAXPROCS','GOFLAGS']},indent=2))
 changed=['internal/library/backup.go']+(['internal/library/backup_retention_test.go'] if a.phase=='fixed' else [])
 if run('gofmt',['gofmt','-l',*changed]).strip():raise RuntimeError('Changed files are not gofmt clean')
 native_tags=['-tags','gtk3'] if system=='Linux' else []
 run('vet-native',['go','vet',*native_tags,'./...'])
 run('vet-linux',['go','vet','-tags','nogui','./...'],extra={'GOOS':'linux'})
 run('vet-windows',['go','vet','./...'],extra={'GOOS':'windows'})
 run('build-native',['go','build',*native_tags])
 run('build-linux',['go','build','-tags','nogui'],extra={'GOOS':'linux'})
 run('build-windows',['go','build'],extra={'GOOS':'windows'})
 run('package-inventory',['go','list','-tags','nogui','./...'])
 run('test-inventory',['go','test','-tags','nogui','-json','-list','.','./...'])
 # Baseline is pristine here: no new tests or production adapter exists.
 run('full-tests',['go','test','-tags','nogui','-json','-count=1','-timeout','60m','./...'])
 if a.phase=='baseline':
  target=source/'internal/library/backup_retention_qualification_test.go'
  run('extract-baseline-fixture',['go','run',str(harness/'extract-fixture.go'),'--',str(fixture),str(target),str(proof/'fixture-ast.json')])
  ast=json.loads((proof/'fixture-ast.json').read_text())
  if ast!=manifest['baseline_fixture_ast']:raise RuntimeError('Baseline fixture relation not proven')
  focused='^(TestBackupRetentionUsesAbsoluteTime|TestBackupRetentionEqualTimesKeepNumericNewest|TestBackupRetentionKeepsSkillsHiddenAndFiles)$'
  run('focused-tests',['go','test','-tags','nogui','-json','-count=1','./internal/library','-run',focused],expected_exit=1)
 else:
  run('focused-tests',['go','test','-tags','nogui','-json','-count=1','./internal/library','-run','^TestBackupRetention'])
 result={'status':'PASS','phase':a.phase,'head':head,'system':system}
except Exception as error:
 result={'status':'FAIL','phase':a.phase,'error':str(error)}
finally:
 if before is not None:
  go_files={f.relative_to(source).as_posix() for f in source.rglob('*.go')}
  expected_go={name for name in before if name.endswith('.go')}
  if a.phase=='baseline' and (source/'internal/library/backup_retention_qualification_test.go').exists():expected_go.add('internal/library/backup_retention_qualification_test.go')
  (proof/'go-source-inventory.json').write_text(json.dumps({'expected':sorted(expected_go),'actual':sorted(go_files)},indent=2))
  if go_files!=expected_go:result={'status':'FAIL','phase':a.phase,'error':'Unknown or lost Go source file'}
  if git('rev-parse','HEAD')!=expected['head'] or git('rev-parse','HEAD^{tree}')!=expected['tree']:result={'status':'FAIL','phase':a.phase,'error':'Git identity changed during qualification'}
  try:verify_git_blobs('git-blobs-after')
  except Exception as error:result={'status':'FAIL','phase':a.phase,'error':str(error)}
  after=hashes();(proof/'tracked-after.json').write_text(json.dumps(after,indent=2));dirty=git('status','--porcelain','--untracked-files=no');(proof/'tracked-status.txt').write_text(dirty)
  if before!=after or dirty:result={'status':'FAIL','phase':a.phase,'error':'Tracked source mutated'}
 if home is not None:
  try:
   for f in home.rglob('*'):
    if not f.is_symlink():os.chmod(f,stat.S_IREAD|stat.S_IWRITE|(stat.S_IEXEC if f.is_dir() else 0))
   shutil.rmtree(home)
  except Exception as error:result={'status':'FAIL','phase':a.phase,'error':'Sandbox cleanup failed: '+str(error)}
 (proof/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
if result['status']!='PASS':raise SystemExit(1)

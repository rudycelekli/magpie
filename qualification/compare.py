"""Compare pristine originals and literal compatible regressions on all three OS."""
import argparse,json,re,hashlib,datetime
from pathlib import Path

MANIFEST=json.loads(Path(__file__).with_name('manifest.json').read_text())

def parse(path):
 runs=set();cases={};packages={};output={}
 for line in path.read_text().splitlines():
  row=json.loads(line);action=row['Action'];package=row.get('Package','');test=row.get('Test')
  if action=='build-fail':raise RuntimeError('Native build failure in test receipt')
  if test:
   key=package+'::'+test
   if action=='run':
    if key in runs:raise RuntimeError('Duplicate invocation')
    runs.add(key)
   if action=='output':
    text=row['Output']
    if not text.lstrip().startswith(('=== RUN','=== PAUSE','=== CONT','--- SKIP:','--- PASS:','--- FAIL:')):output.setdefault(key,[]).append(text)
   if action in ['pass','fail','skip']:
    if key in cases:raise RuntimeError('Duplicate outcome')
    cases[key]=action
  elif package and action in ['pass','fail','skip']:
   if package in packages:raise RuntimeError('Duplicate package outcome')
   packages[package]=action
 if not runs or runs!=set(cases) or not packages:raise RuntimeError('Empty or incomplete test receipt')
 if any(k not in output for k,v in cases.items() if v=='skip'):raise RuntimeError('Skip lacks reason evidence')
 skips={k:output[k] for k,v in cases.items() if v=='skip'}
 return cases,packages,skips

def command_contract(phase,system,env):
 from pathlib import PureWindowsPath,PurePosixPath
 path=PureWindowsPath if system=='Windows' else PurePosixPath
 native=['-tags','gtk3'] if system=='Linux' else []
 commands={
  'gofmt':(['gofmt','-l','internal/library/backup.go']+(['internal/library/backup_retention_test.go'] if phase=='fixed' else []),{}),
  'vet-native':(['go','vet',*native,'./...'],{}),
  'vet-linux':(['go','vet','-tags','nogui','./...'],{'GOOS':'linux'}),
  'vet-windows':(['go','vet','./...'],{'GOOS':'windows'}),
  'build-native':(['go','build',*native],{}),
  'build-linux':(['go','build','-tags','nogui'],{'GOOS':'linux'}),
  'build-windows':(['go','build'],{'GOOS':'windows'}),
  'package-inventory':(['go','list','-tags','nogui','./...'],{}),
  'test-inventory':(['go','test','-tags','nogui','-json','-list','.','./...'],{}),
  'full-tests':(['go','test','-tags','nogui','-json','-count=1','-timeout','60m','./...'],{}),
  'focused-tests':(['go','test','-tags','nogui','-json','-count=1','./internal/library','-run',
      '^(TestBackupRetentionUsesAbsoluteTime|TestBackupRetentionEqualTimesKeepNumericNewest|TestBackupRetentionKeepsSkillsHiddenAndFiles)$' if phase=='baseline' else '^TestBackupRetention'],{})}
 if phase=='baseline':
  commands['extract-baseline-fixture']=(['go','run',str(path(env['harness'])/'extract-fixture.go'),'--',str(path(env['harness'])/'backup-retention-fixture.go'),str(path(env['source'])/'internal/library/backup_retention_qualification_test.go'),str(path(env['proof'])/'fixture-ast.json')],{})
 return commands

def inspect_native(directory,phase,osname):
 result=json.loads((directory/'result.json').read_text());env=json.loads((directory/'environment.json').read_text());expected=MANIFEST['phases'][phase]
 system={'Linux':'Linux','Windows':'Windows','macOS':'Darwin'}[osname]
 if result.get('status')!='PASS' or result.get('head')!=expected['head'] or result.get('system')!=system:raise RuntimeError('Native status/source mismatch')
 if env['head']!=expected['head'] or env['tree']!=expected['tree'] or env['runner_os']!=osname or env['system']!=system:raise RuntimeError('Native environment identity mismatch')
 if env['go_version'].split()[2]!=MANIFEST['go_version'] or env['bun_version']!=MANIFEST['bun_version']:raise RuntimeError('Native runtime mismatch')
 before=json.loads((directory/'tracked-before.json').read_text())
 go_files=json.loads((directory/'go-source-inventory.json').read_text())
 expected_go={name for name in before if name.endswith('.go')}
 if phase=='baseline':expected_go.add('internal/library/backup_retention_qualification_test.go')
 if go_files['expected']!=sorted(expected_go) or go_files['actual']!=go_files['expected']:raise RuntimeError('Unknown or lost Go source file')
 for label in ['git-blobs-before','git-blobs-after']:
  blobs=json.loads((directory/(label+'.json')).read_text())
  if set(blobs)!=set(before):raise RuntimeError('Git blob coverage incomplete')
  for name,blob in blobs.items():
   if blob['expected_sha256']!=blob['actual_sha256'] or blob['actual_sha256']!=before[name] or not re.fullmatch('[0-9a-f]{40}',blob['git_blob']) or blob['mode'] not in ['100644','100755','120000']:
    raise RuntimeError('Working file differs from committed Git blob')
   if blob['storage'] not in ['file','symlink'] or (blob['mode']!='120000' and blob['storage']!='file'):raise RuntimeError('Working file storage differs from Git type')
 if phase=='fixed' and any(before.get(name)!=digest for name,digest in MANIFEST['source_files_sha256'].items()):raise RuntimeError('Reviewed fixed file bytes differ')
 if before!=json.loads((directory/'tracked-after.json').read_text()) or (directory/'tracked-status.txt').read_text().strip():raise RuntimeError('Source bytes mutated')
 labels={'gofmt','vet-native','vet-linux','vet-windows','build-native','build-linux','build-windows','package-inventory','test-inventory','full-tests','focused-tests'}
 if phase=='baseline':labels.add('extract-baseline-fixture')
 commands=json.loads((directory/'commands.json').read_text())
 contract=command_contract(phase,system,env)
 sandbox=json.loads((directory/'sandbox.json').read_text())
 if any(sandbox[k]!=env['go_env'][k] for k in ['GOPATH','GOMODCACHE','GOCACHE']) or sandbox['GOTOOLCHAIN']!='local' or sandbox['GOFLAGS']!='-p=2 -mod=readonly' or sandbox['HOME']!=sandbox['USERPROFILE']:
  raise RuntimeError('Sandbox/cache contract mismatch')
 if len(commands)!=len(labels) or {c['label'] for c in commands}!=labels:raise RuntimeError('Mandatory command inventory lost')
 for c in commands:
  try:
   start=datetime.datetime.fromisoformat(c['start']);end=datetime.datetime.fromisoformat(c['end'])
   if start.utcoffset() is None or end.utcoffset() is None or start>end:raise ValueError('invalid command timestamps')
  except (KeyError,ValueError,TypeError) as error:raise RuntimeError('Malformed or reversed command timestamps') from error
  for channel in ['stdout','stderr']:
   actual_hash=hashlib.sha256((directory/(c['label']+'.'+channel)).read_bytes()).hexdigest()
   if actual_hash!=c.get(channel+'_sha256'):raise RuntimeError('Raw command log hash differs')
  if c.get('argv')!=contract[c['label']][0] or c.get('extra_env')!=contract[c['label']][1]:raise RuntimeError('Native command scope or environment changed')
  expected_exit=1 if phase=='baseline' and c['label']=='focused-tests' else 0
  if c['exit']!=expected_exit or c['expected_exit']!=expected_exit:raise RuntimeError('Native command exit mismatch')
 if (directory/'gofmt.stdout').read_text().strip():raise RuntimeError('Formatting failure')
 if phase=='baseline' and json.loads((directory/'fixture-ast.json').read_text())!=MANIFEST['baseline_fixture_ast']:raise RuntimeError('Baseline literal AST fixture differs')
 full=parse(directory/'full-tests.stdout');focused=parse(directory/'focused-tests.stdout')
 collected=set();inventory_packages={}
 for line in (directory/'test-inventory.stdout').read_text().splitlines():
  row=json.loads(line)
  if row['Action']=='output':
   name=row.get('Output','').strip()
   if re.fullmatch(r'(Test|Example|Fuzz)\w*',name):collected.add(row['Package']+'::'+name)
  elif row['Action'] in ['pass','skip','fail'] and row.get('Package'):
   if row['Package'] in inventory_packages:raise RuntimeError('Duplicate collected package')
   inventory_packages[row['Package']]=row['Action']
 if not collected or any(v=='fail' for v in inventory_packages.values()) or set(inventory_packages)!=set(full[1]):raise RuntimeError('Collected test/package inventory incomplete')
 if collected!={k for k in full[0] if '/' not in k.split('::',1)[1]}:raise RuntimeError('Collected original or new top-level test did not run')
 if set(full[1])!=set((directory/'package-inventory.stdout').read_text().splitlines()):raise RuntimeError('Full package inventory incomplete')
 for cases,packages,_ in [full]:
  if any(v=='fail' for v in cases.values()) or any(v=='fail' for v in packages.values()):raise RuntimeError('Full native suite failed')
 return full,focused

def compare_pair(base,fixed,osname):
 (a,ap,askips),(bf,bfp,bfskips)=inspect_native(base,'baseline',osname)
 (b,bp,bskips),(ff,ffp,ffskips)=inspect_native(fixed,'fixed',osname)
 package=MANIFEST['package'];new={package+'::'+n for n in MANIFEST['new_test_names']}
 if set(b)!=set(a)|new or new&set(a):raise RuntimeError('Original or exact new identity inventory changed')
 if any(a[k]!=b[k] for k in a) or ap!=bp or askips!=bskips:raise RuntimeError('Original outcomes/package states/skip reasons changed')
 if any(b[k]!='pass' for k in new):raise RuntimeError('New full-suite tests failed or skipped')
 expected_b={package+'::'+n:'fail' for n in MANIFEST['baseline_focused_fail']}
 expected_b.update({package+'::'+n:'pass' for n in MANIFEST['baseline_focused_pass']})
 if bf!=expected_b or bfp!={package:'fail'} or bfskips:raise RuntimeError('Baseline did not produce exact behavioral negative and positive controls')
 if ff!={k:'pass' for k in new} or ffp!={package:'pass'} or ffskips:raise RuntimeError('Fixed focused tests missing/failing/skipped')
 return {'status':'PASS','original_test_identities':len(a),'packages':len(ap),'preserved_skips':len(askips),'new_fixed_pass':len(new),'baseline_fail_reports':len(MANIFEST['baseline_focused_fail'])}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('artifacts',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
 try:
  result={'status':'PASS','platforms':{name:compare_pair(a.artifacts/('magpie-'+name+'-baseline'),a.artifacts/('magpie-'+name+'-fixed'),name) for name in ['Linux','macOS','Windows']}}
 except Exception as error:result={'status':'FAIL','error':str(error)}
 a.output.write_text(json.dumps(result,indent=2));print(json.dumps(result))
 if result['status']!='PASS':raise SystemExit(1)

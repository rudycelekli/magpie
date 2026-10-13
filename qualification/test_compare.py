import copy,json,tempfile,unittest,hashlib
from pathlib import Path
from compare import MANIFEST,command_contract,compare_pair

P=MANIFEST['package']
def go_rows(cases,package_outcome='pass',reason='original_test.go:1: existing unavailable environment'):
 rows=[]
 for name,outcome in cases.items():
  rows.append({'Action':'run','Package':P,'Test':name})
  if outcome=='skip':
   rows.extend([{'Action':'output','Package':P,'Test':name,'Output':'=== RUN   '+name+'\n'},
                {'Action':'output','Package':P,'Test':name,'Output':'    '+reason+'\n'},
                {'Action':'output','Package':P,'Test':name,'Output':'--- SKIP: '+name+' (0.00s)\n'}])
  rows.append({'Action':outcome,'Package':P,'Test':name})
 rows.append({'Action':package_outcome,'Package':P});return rows

class QualificationControls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  for phase in ['baseline','fixed']:
   d=self.root/phase;d.mkdir();expected=MANIFEST['phases'][phase]
   env={'head':expected['head'],'tree':expected['tree'],'runner_os':'Linux','system':'Linux','go_version':'go version go1.26.8 linux/amd64','bun_version':'1.4.2','source':'/native/source','proof':'/native/proof','harness':'/native/harness','go_env':{'GOPATH':'/cache/go','GOMODCACHE':'/cache/mod','GOCACHE':'/cache/build'}}
   self.write(d,'environment.json',env);self.write(d,'result.json',{'status':'PASS','head':expected['head'],'system':'Linux'})
   before={'source.go':hashlib.sha256(b'same raw bytes').hexdigest()}
   if phase=='fixed':before.update(MANIFEST['source_files_sha256'])
   for f in ['tracked-before.json','tracked-after.json']:self.write(d,f,before)
   blobs={name:{'mode':'100644','git_blob':'1'*40,'expected_sha256':digest,'actual_sha256':digest,'storage':'file'} for name,digest in before.items()}
   for f in ['git-blobs-before.json','git-blobs-after.json']:self.write(d,f,blobs)
   go_names={name for name in before if name.endswith('.go')}
   if phase=='baseline':go_names.add('internal/library/backup_retention_qualification_test.go')
   self.write(d,'go-source-inventory.json',{'expected':sorted(go_names),'actual':sorted(go_names)})
   (d/'tracked-status.txt').write_text('');(d/'gofmt.stdout').write_text('')
   sandbox={k:env['go_env'][k] for k in ['GOPATH','GOMODCACHE','GOCACHE']};sandbox.update(HOME='/sandbox',USERPROFILE='/sandbox',GOTOOLCHAIN='local',GOFLAGS='-p=2 -mod=readonly')
   self.write(d,'sandbox.json',sandbox)
   commands=[]
   for label,(argv,extra) in command_contract(phase,'Linux',env).items():
    code=1 if phase=='baseline' and label=='focused-tests' else 0
    commands.append({'label':label,'argv':argv,'extra_env':extra,'exit':code,'expected_exit':code,'start':'2026-10-13T00:00:00+00:00','end':'2026-10-13T00:00:01+00:00'})
   self.write(d,'commands.json',commands)
   full={'TestOriginalPass':'pass','TestOriginalSkip':'skip'}
   if phase=='fixed':full.update({name:'pass' for name in MANIFEST['new_test_names']})
   self.rows(d,'full-tests.stdout',go_rows(full))
   self.rows(d,'test-inventory.stdout',[{'Action':'output','Package':P,'Output':name+'\n'} for name in full if '/' not in name]+[{'Action':'pass','Package':P}])
   (d/'package-inventory.stdout').write_text(P+'\n')
   focused={name:'fail' for name in MANIFEST['baseline_focused_fail']};focused.update({name:'pass' for name in MANIFEST['baseline_focused_pass']})
   if phase=='fixed':focused={name:'pass' for name in MANIFEST['new_test_names']}
   self.rows(d,'focused-tests.stdout',go_rows(focused,'fail' if phase=='baseline' else 'pass'))
   if phase=='baseline':self.write(d,'fixture-ast.json',MANIFEST['baseline_fixture_ast'])
   for c in commands:
    for channel in ['stdout','stderr']:
     log=d/(c['label']+'.'+channel)
     if not log.exists():log.write_text('')
     c[channel+'_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
   self.write(d,'commands.json',commands)
 def write(self,d,name,value):(d/name).write_text(json.dumps(value))
 def rows(self,d,name,value):(d/name).write_text(''.join(json.dumps(r)+'\n' for r in value))
 def edit(self,phase,name,fn):
  d=self.root/phase;j=json.loads((d/name).read_text());fn(j);self.write(d,name,j)
 def refresh_log_hash(self,phase,name):
  d=self.root/phase;label,channel=name.rsplit('.',1);commands=json.loads((d/'commands.json').read_text())
  for c in commands:
   if c['label']==label:c[channel+'_sha256']=hashlib.sha256((d/name).read_bytes()).hexdigest()
  self.write(d,'commands.json',commands)
 def edit_rows(self,phase,name,fn):
  d=self.root/phase;rows=[json.loads(s) for s in (d/name).read_text().splitlines()];fn(rows);self.rows(d,name,rows);self.refresh_log_hash(phase,name)
 def run_compare(self):return compare_pair(self.root/'baseline',self.root/'fixed','Linux')
 def reject(self):
  with self.assertRaises(RuntimeError):self.run_compare()
 def test_valid_pristine_and_compatible_fixture(self):self.assertEqual(self.run_compare()['new_fixed_pass'],9)
 def test_reversed_timestamp(self):self.edit('fixed','commands.json',lambda j:j[0].update(end='2026-10-12T23:59:59+00:00'));self.reject()
 def test_invalid_timestamp(self):self.edit('fixed','commands.json',lambda j:j[0].update(start='not time'));self.reject()
 def test_raw_log_tampering(self):(self.root/'fixed/vet-native.stdout').write_text('tampered');self.reject()
 def test_wrong_git_blob_hash(self):self.edit('fixed','git-blobs-before.json',lambda j:j['source.go'].update(actual_sha256='wrong'));self.reject()
 def test_git_blob_coverage_loss(self):self.edit('fixed','git-blobs-before.json',lambda j:j.pop('source.go'));self.reject()
 def test_symlink_blob_record(self):
  for phase in ['baseline','fixed']:
   for name in ['git-blobs-before.json','git-blobs-after.json']:self.edit(phase,name,lambda j:j['source.go'].update(mode='120000',storage='symlink'))
  self.assertEqual(self.run_compare()['status'],'PASS')
 def test_wrong_head(self):self.edit('fixed','environment.json',lambda j:j.update(head='0'*40));self.reject()
 def test_wrong_os(self):self.edit('fixed','environment.json',lambda j:j.update(system='Darwin'));self.reject()
 def test_wrong_go(self):self.edit('fixed','environment.json',lambda j:j.update(go_version='go version go1.27.1 linux/amd64'));self.reject()
 def test_wrong_bun(self):self.edit('fixed','environment.json',lambda j:j.update(bun_version='1.4.1'));self.reject()
 def test_unknown_go_shim(self):self.edit('fixed','go-source-inventory.json',lambda j:j['actual'].append('fake.go'));self.reject()
 def test_both_package_fail(self):
  for phase in ['baseline','fixed']:self.edit_rows(phase,'full-tests.stdout',lambda rows:rows[-1].update(Action='fail'))
  self.reject()
 def test_baseline_positive_control_fail(self):
  self.edit_rows('baseline','focused-tests.stdout',lambda rows:next(r for r in rows if r.get('Test')=='TestBackupRetentionKeepsSkillsHiddenAndFiles' and r['Action']=='pass').update(Action='fail'));self.reject()
 def test_mutated_source(self):self.edit('fixed','tracked-after.json',lambda j:j.update(extra='mutated'));self.reject()
 def test_missing_gate(self):self.edit('fixed','commands.json',lambda j:j.pop());self.reject()
 def test_wrong_native_exit(self):self.edit('fixed','commands.json',lambda j:j[0].update(exit=1));self.reject()
 def test_narrow_full_suite(self):
  self.edit('fixed','commands.json',lambda j:next(c for c in j if c['label']=='full-tests')['argv'].extend(['-run','TestOriginalPass']));self.reject()
 def test_wrong_cross_environment(self):self.edit('fixed','commands.json',lambda j:next(c for c in j if c['label']=='vet-linux')['extra_env'].update(GOOS='windows'));self.reject()
 def test_unpinned_cache(self):self.edit('fixed','sandbox.json',lambda j:j.update(GOCACHE='/wrong/cache'));self.reject()
 def test_baseline_production_shim(self):self.edit('baseline','fixture-ast.json',lambda j:j.update(production_changes=True));self.reject()
 def test_changed_baseline_method(self):self.edit('baseline','fixture-ast.json',lambda j:j['literal_function_sha256'].update(retentionCopies='wrong'));self.reject()
 def test_missing_original(self):
  self.edit_rows('fixed','full-tests.stdout',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r.get('Test')!='TestOriginalPass']));self.reject()
 def test_both_omit_collected_original(self):
  for phase in ['baseline','fixed']:self.edit_rows(phase,'full-tests.stdout',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r.get('Test')!='TestOriginalPass']))
  self.reject()
 def test_missing_new_test(self):
  name=MANIFEST['new_test_names'][0];self.edit_rows('fixed','full-tests.stdout',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r.get('Test')!=name]));self.reject()
 def test_unexpected_new_identity(self):self.edit_rows('fixed','full-tests.stdout',lambda rows:rows.__setitem__(slice(-1,-1),go_rows({'TestExtra':'pass'})[:-1]));self.reject()
 def test_full_package_fail(self):self.edit_rows('fixed','full-tests.stdout',lambda rows:rows[-1].update(Action='fail'));self.reject()
 def test_baseline_negative_absent(self):self.edit_rows('baseline','focused-tests.stdout',lambda rows:next(r for r in rows if r['Action']=='fail' and r.get('Test')) .update(Action='pass'));self.reject()
 def test_fixed_only_guard_missing(self):
  name='TestBackupRetentionUnknownTimeLeavesCopiesAlone';self.edit_rows('fixed','focused-tests.stdout',lambda rows:rows.__setitem__(slice(None),[r for r in rows if r.get('Test')!=name]));self.reject()
 def test_skip_reason_changed(self):self.edit_rows('fixed','full-tests.stdout',lambda rows:next(r for r in rows if r.get('Action')=='output' and 'existing unavailable' in r.get('Output','')).update(Output='different actual reason\n'));self.reject()
 def test_framework_duration_is_not_skip_reason(self):
  self.edit_rows('fixed','full-tests.stdout',lambda rows:next(r for r in rows if '--- SKIP:' in r.get('Output','')).update(Output='--- SKIP: TestOriginalSkip (1.23s)\n'));self.assertEqual(self.run_compare()['preserved_skips'],1)
 def test_package_inventory_loss(self):(self.root/'fixed/package-inventory.stdout').write_text(P+'\nmissing/package\n');self.refresh_log_hash('fixed','package-inventory.stdout');self.reject()

if __name__=='__main__':unittest.main()

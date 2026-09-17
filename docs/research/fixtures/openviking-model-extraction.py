"""Real-model synthetic save/commit/search probe against an isolated service.

Usage: python openviking-model-extraction.py /path/to/isolated/run-directory
Requires ov.conf and real configured models. Makes billed model requests.
Stores secret-bearing probe-state.json mode 0600 only in that run directory.
Leaves synthetic content for follow-up correction/deletion/recovery experiments.
A completed task alone is insufficient; the fixed synthetic facts must be recalled.
"""
import json,secrets,time,sys
from pathlib import Path
import httpx
r=Path(sys.argv[1])
c=json.loads((r/'ov.conf').read_text())
assert c['server']['host'] in ('127.0.0.1','localhost'), 'Use an isolated loopback service'
client=httpx.Client(base_url=f"http://{c['server']['host']}:{c['server']['port']}/api/v1",timeout=120,trust_env=False)
def request(method,path,key,body=None):
 q=client.request(method,path,headers={'X-API-Key':key},json=body)
 if q.status_code!=200: raise RuntimeError(f'{method} request failed: {q.status_code}')
 return q.json()['result']
root=c['server']['root_api_key']; account='model-'+secrets.token_hex(6)
request('POST','/admin/accounts',root,{'account_id':account,'admin_user_id':'admin'})
key=request('POST',f'/admin/accounts/{account}/users',root,{'user_id':'alice','role':'user'})['user_key']
s=request('POST','/sessions',key,{'auto_commit_policy':None})
sid=s['session_id']
request('POST',f'/sessions/{sid}/messages',key,{'role':'user','content':'请记住我的稳定偏好：所有技术方案都要写清目标和非目标，默认用简体中文。','source_message_ids':['synthetic-preference-1']})
t=time.monotonic()
commit=request('POST',f'/sessions/{sid}/commit',key,{'keep_recent_count':0})
state={'account':account,'key':key,'sessionId':sid,'commit':commit}
p=r/'probe-state.json';p.write_text(json.dumps(state));p.chmod(0o600)
print(json.dumps({'commitStatus':commit.get('status'),'taskIdPresent':bool(commit.get('task_id')),'archived':commit.get('archived')}),flush=True)
task_id=commit.get('task_id')
for _ in range(36):
 task=request('GET',f'/tasks/{task_id}',key)
 status=task.get('status')
 print(json.dumps({'taskStatus':status,'elapsedSeconds':round(time.monotonic()-t,1)}),flush=True)
 if status in ('completed','failed','cancelled'):
  (r/'task-result.json').write_text(json.dumps(task,ensure_ascii=False))
  if status=='completed':
   found=request('POST','/search/find',key,{'query':'技术方案的语言和格式偏好','target_uri':'viking://user/alice/memories','limit':5})
   (r/'search-result.json').write_text(json.dumps(found,ensure_ascii=False))
   recalled=json.dumps(found.get('memories',[]),ensure_ascii=False)
   assert '目标和非目标' in recalled and '简体中文' in recalled, 'Expected synthetic facts were not recalled'
   print(json.dumps({'syntheticFactsRecalled':True}),flush=True)
  else:
   raise RuntimeError(f'Extraction ended in {status}; inspect isolated task-result.json')
  break
 time.sleep(5)
else:
 raise TimeoutError('Extraction did not reach a terminal state within the probe window')

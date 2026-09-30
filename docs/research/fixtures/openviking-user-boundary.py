"""Run in a disposable OpenViking container with synthetic accounts only."""
import json,urllib.request,urllib.parse,urllib.error,secrets,sys
c=json.load(open(sys.argv[1] if len(sys.argv)>1 else '/etc/openviking/ov.conf'))
base='http://127.0.0.1:1933/api/v1'; root=c['server']['root_api_key']
def call(method,path,key,body=None):
 r=urllib.request.Request(base+path,method=method,headers={'X-API-Key':key,'Content-Type':'application/json'},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(r,timeout=120) as v:return v.status,json.load(v)
 except urllib.error.HTTPError as e:return e.code,None
account='dano495_boundary_'+secrets.token_hex(4)
assert call('POST','/admin/accounts',root,{'account_id':account,'admin_user_id':'admin'})[0]==200
keys={u:call('POST','/admin/accounts/'+account+'/users',root,{'user_id':u,'role':'user'})[1]['result']['user_key'] for u in ['alice','bob']}
uri='viking://user/alice/memories/preference.md'; content='Synthetic boundary preference: blue summaries.'
assert call('POST','/content/write',keys['alice'],{'uri':uri,'content':content,'mode':'create','wait':True,'timeout':90})[0]==200
query=urllib.parse.urlencode({'uri':uri})
statuses={'read':call('GET','/content/read?'+query,keys['bob'])[0], 'write':call('POST','/content/write',keys['bob'],{'uri':uri,'content':'changed','mode':'replace','wait':True})[0], 'delete':call('DELETE','/fs?'+query,keys['bob'])[0]}
assert all(s==403 for s in statuses.values()),statuses
status,after=call('GET','/content/read?'+query,keys['alice']);assert status==200 and content in json.dumps(after)
print(json.dumps({'crossUserMemory':statuses,'ownerContentUnchanged':True}))

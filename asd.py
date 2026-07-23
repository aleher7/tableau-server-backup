python -c "import json,time,jwt,requests; c=json.load(open('config.json')); k=open(c['github_private_key_path'],'rb').read(); a=int(time.time()); p={'iat':a-60,'exp':a+600,'iss':c['github_client_id']}; j=jwt.encode(p,k,algorithm='RS256'); j=j.decode() if isinstance(j,bytes) else j; u=f\"https://api.cantabrialabs.ghe.com/app/installations/{c['github_installation_id']}/access_tokens\"; h={'Authorization':f'Bearer {j}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'}; t=requests.post(u,headers=h,timeout=15).json()['token']; open('token_temporal.txt','w').write(t); print('Token guardado en token_temporal.txt')"

$token = Get-Content token_temporal.txt
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("x-access-token:$token"))
git -c "http.https://cantabrialabs.ghe.com.extraHeader=Authorization: Basic $b64" push https://cantabrialabs.ghe.com/cantabrialabs-it/CLabs-Tableau.git main

Remove-Item token_temporal.txt

git status

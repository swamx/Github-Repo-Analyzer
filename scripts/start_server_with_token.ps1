$t = [Environment]::GetEnvironmentVariable('GITHUB_TOKEN','User')
$env:GITHUB_TOKEN = $t
python -m uvicorn main:app --host 127.0.0.1 --port 8000

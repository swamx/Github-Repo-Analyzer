$t = [Environment]::GetEnvironmentVariable('GITHUB_TOKEN','User')
Write-Host "len=$($t.Length)"
try {
    $r = Invoke-RestMethod -Headers @{Authorization = "token $t"; 'User-Agent'='cli'} -Uri 'https://api.github.com/user'
    Write-Host $r.login
} catch {
    Write-Host 'ERROR'
    if ($_.Exception -and $_.Exception.Response) { Write-Host $_.Exception.Response.StatusCode.Value__ } else { Write-Host $_ }
}

<#
Creates the Cloud SQL database used by the workbench and stores only its
connection URL in Secret Manager. It deliberately never prints credentials.
#>
[CmdletBinding()]
param(
  [string]$ProjectId = "bond-agent-509210",
  [string]$Region = "asia-east1",
  [string]$Instance = "bond-agent-db",
  [string]$Database = "bond_agent"
)

$ErrorActionPreference = "Stop"
$gcloud = "C:\Users\18284\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

function New-SecretPassword {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes)
}

function Assert-GcloudSuccess([string]$Action) {
  if ($LASTEXITCODE -ne 0) {
    throw "Google Cloud action failed: $Action"
  }
}

function Add-SecretVersionWithoutNewline([string]$SecretName, [string]$Value) {
  $temporaryFile = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($temporaryFile, $Value, [System.Text.UTF8Encoding]::new($false))
    & $gcloud secrets versions add $SecretName --project $ProjectId --data-file $temporaryFile | Out-Null
    Assert-GcloudSuccess "write $SecretName secret"
  } finally {
    Remove-Item -LiteralPath $temporaryFile -Force -ErrorAction SilentlyContinue
  }
}

& $gcloud config set project $ProjectId | Out-Null
& $gcloud sql instances describe $Instance --project $ProjectId 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  $password = New-SecretPassword
  & $gcloud sql instances create $Instance `
    --project $ProjectId `
    --database-version POSTGRES_16 `
    --edition ENTERPRISE `
    --region $Region `
    --availability-type ZONAL `
    --cpu 2 `
    --memory 8GiB `
    --storage-type SSD `
    --storage-size 20GB `
    --assign-ip `
    --root-password $password
  Assert-GcloudSuccess "create Cloud SQL instance"
} else {
  # The Cloud SQL password is intentionally rotated into Secret Manager when
  # rerunning this bootstrap, so the deployed service remains the sole client.
  $password = New-SecretPassword
}

& $gcloud sql users set-password postgres --instance $Instance --project $ProjectId --password $password
Assert-GcloudSuccess "set PostgreSQL password"
& $gcloud sql databases describe $Database --instance $Instance --project $ProjectId 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  & $gcloud sql databases create $Database --instance $Instance --project $ProjectId
  Assert-GcloudSuccess "create application database"
}

$encodedPassword = [System.Uri]::EscapeDataString($password)
$connectionName = "$ProjectId`:$Region`:$Instance"
# Braces are required here: without them PowerShell treats `$Database?host` as
# one variable and silently drops the `?host=` query parameter needed by psycopg.
$databaseUrl = "postgresql+psycopg://postgres:$encodedPassword@/$($Database)?host=/cloudsql/$connectionName"

& $gcloud secrets describe database-url --project $ProjectId 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  & $gcloud secrets create database-url --project $ProjectId --replication-policy automatic | Out-Null
  Assert-GcloudSuccess "create database URL secret"
}
Add-SecretVersionWithoutNewline "database-url" $databaseUrl

Write-Output "Cloud SQL is ready: $connectionName. The database URL is stored only in Secret Manager."

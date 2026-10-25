<#
Copies model keys from the local .env file into Secret Manager without adding
newline characters. Values are never written to stdout or source control.
#>
[CmdletBinding()]
param([string]$ProjectId = "bond-agent-509210")

$ErrorActionPreference = "Stop"
$gcloud = "C:\Users\18284\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$envLines = Get-Content (Join-Path $PSScriptRoot "..\\.env")

function Get-EnvValue([string]$Name) {
  $line = $envLines | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
  if (-not $line) { throw "Missing $Name in .env" }
  $value = $line.Substring($Name.Length + 1).Trim()
  if (-not $value) { throw "Empty $Name in .env" }
  return $value
}

function Write-SecretWithoutNewline([string]$SecretName, [string]$Value) {
  $temporaryFile = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($temporaryFile, $Value, [System.Text.UTF8Encoding]::new($false))
    & $gcloud secrets versions add $SecretName --project $ProjectId --data-file $temporaryFile | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to write $SecretName" }
  } finally {
    Remove-Item -LiteralPath $temporaryFile -Force -ErrorAction SilentlyContinue
  }
}

Write-SecretWithoutNewline "deepseek-api-key" (Get-EnvValue "DEEPSEEK_API_KEY")
Write-Output "DeepSeek key was safely synchronized to Secret Manager. Vertex AI uses service-account authentication."

<#!
Create or update the daily private Cloud Scheduler task that enforces the
30-day project retention policy. Run this after deploy.ps1.
#>
[CmdletBinding()]
param(
  [string]$ProjectId = "bond-agent-509210",
  [string]$Region = "asia-east1",
  [string]$Service = "bond-agent",
  [string]$JobName = "bond-agent-retention",
  [string]$TaskInvokerServiceAccount = ""
)

$ErrorActionPreference = "Stop"
if (-not $TaskInvokerServiceAccount) { $TaskInvokerServiceAccount = "bond-agent-task@$ProjectId.iam.gserviceaccount.com" }
$url = gcloud run services describe $Service --project $ProjectId --region $Region --format "value(status.url)"
if (-not $url) { throw "未找到 Cloud Run 服务 $Service。请先运行 deploy.ps1。" }

gcloud scheduler jobs describe $JobName --project $ProjectId --location $Region 2>$null
if ($LASTEXITCODE -eq 0) {
  gcloud scheduler jobs update http $JobName --project $ProjectId --location $Region --schedule "30 2 * * *" --time-zone "Asia/Singapore" --uri "$url/internal/maintenance/purge" --http-method POST --oidc-service-account-email $TaskInvokerServiceAccount --oidc-token-audience $url
} else {
  gcloud scheduler jobs create http $JobName --project $ProjectId --location $Region --schedule "30 2 * * *" --time-zone "Asia/Singapore" --uri "$url/internal/maintenance/purge" --http-method POST --oidc-service-account-email $TaskInvokerServiceAccount --oidc-token-audience $url
}
Write-Output "已创建每日 02:30（新加坡时间）的 30 天数据清理任务。"

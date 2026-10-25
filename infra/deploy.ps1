<#!
Deploy the application image to Cloud Run. Run after setup-gcp.ps1 and after
setting the real database URL and service account values in Secret Manager.
#>
[CmdletBinding()]
param(
  [string]$ProjectId = "bond-agent-509210",
  [string]$Region = "asia-east1",
  [string]$Service = "bond-agent",
  [Parameter(Mandatory = $true)][string]$CloudSqlInstance,
  [Parameter(Mandatory = $true)][string]$Bucket,
  [Parameter(Mandatory = $true)][string]$TaskInvokerServiceAccount,
  [Parameter(Mandatory = $true)][string]$RuntimeServiceAccount,
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$image = "$Region-docker.pkg.dev/$ProjectId/bond-agent/bond-agent:latest"

# Build exactly this application directory. Sending the parent workspace would
# risk picking up the legacy application and unrelated local files.
if (-not $SkipBuild) {
  gcloud builds submit . --project $ProjectId --tag $image
  if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed; Cloud Run was not updated." }
}
gcloud run deploy $Service `
  --project $ProjectId `
  --region $Region `
  --image $image `
  --no-allow-unauthenticated `
  --port 8080 `
  --cpu 4 `
  --memory 8Gi `
  --timeout 900 `
  --min-instances 0 `
  --max-instances 2 `
  --service-account $RuntimeServiceAccount `
  --add-cloudsql-instances $CloudSqlInstance `
  --set-env-vars "APP_ENV=cloudrun,GOOGLE_CLOUD_PROJECT=$ProjectId,GOOGLE_CLOUD_REGION=$Region,GCS_BUCKET=$Bucket,TASK_QUEUE=bond-agent-jobs,TASKS_INVOKER_SERVICE_ACCOUNT=$TaskInvokerServiceAccount,VERTEX_AI_LOCATION=global,VERTEX_AI_EMBEDDING_MODEL=gemini-embedding-001,EMBEDDING_DIMENSION=1024,DEEPSEEK_BASE_URL=https://api.deepseek.com,DEEPSEEK_MODEL=deepseek-v4-pro" `
  --set-secrets "DATABASE_URL=database-url:latest,DEEPSEEK_API_KEY=deepseek-api-key:latest"

$url = gcloud run services describe $Service --project $ProjectId --region $Region --format "value(status.url)"
# `--set-env-vars` replaces the full environment map and would erase the
# Cloud SQL, storage and Vertex AI settings passed above.  Append only the URL.
gcloud run services update $Service --project $ProjectId --region $Region --update-env-vars "TASKS_SERVICE_URL=$url"
Write-Output "Deployed to $url"

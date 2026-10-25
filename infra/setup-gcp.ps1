<#!
One-time project bootstrap. It creates no model key and never prints a secret.
Use Cloud Console to create the DEEPSEEK_API_KEY secret first. Vertex AI uses
the Cloud Run runtime service account and does not need an embedding API key.
#>
[CmdletBinding()]
param(
  [string]$ProjectId = "bond-agent-509210",
  [string]$Region = "asia-east1",
  [string]$Bucket = "bond-agent-509210-materials",
  [string]$Queue = "bond-agent-jobs",
  [string]$Repository = "bond-agent"
)

$ErrorActionPreference = "Stop"
gcloud config set project $ProjectId
gcloud services enable run.googleapis.com sqladmin.googleapis.com cloudtasks.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com

gcloud artifacts repositories describe $Repository --location $Region 2>$null
if ($LASTEXITCODE -ne 0) { gcloud artifacts repositories create $Repository --repository-format docker --location $Region }

gcloud storage buckets describe "gs://$Bucket" 2>$null
if ($LASTEXITCODE -ne 0) { gcloud storage buckets create "gs://$Bucket" --location $Region --uniform-bucket-level-access }

gcloud tasks queues describe $Queue --location $Region 2>$null
if ($LASTEXITCODE -ne 0) { gcloud tasks queues create $Queue --location $Region }

gcloud iam service-accounts describe "bond-agent-task@$ProjectId.iam.gserviceaccount.com" 2>$null
if ($LASTEXITCODE -ne 0) { gcloud iam service-accounts create bond-agent-task --display-name "Bond Agent Cloud Tasks invoker" }
gcloud iam service-accounts describe "bond-agent-runtime@$ProjectId.iam.gserviceaccount.com" 2>$null
if ($LASTEXITCODE -ne 0) { gcloud iam service-accounts create bond-agent-runtime --display-name "Bond Agent Cloud Run runtime" }

gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:bond-agent-task@$ProjectId.iam.gserviceaccount.com" --role roles/run.invoker
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:bond-agent-runtime@$ProjectId.iam.gserviceaccount.com" --role roles/cloudsql.client
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:bond-agent-runtime@$ProjectId.iam.gserviceaccount.com" --role roles/storage.objectAdmin
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:bond-agent-runtime@$ProjectId.iam.gserviceaccount.com" --role roles/cloudtasks.enqueuer
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:bond-agent-runtime@$ProjectId.iam.gserviceaccount.com" --role roles/secretmanager.secretAccessor
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:bond-agent-runtime@$ProjectId.iam.gserviceaccount.com" --role roles/aiplatform.user
gcloud iam service-accounts add-iam-policy-binding "bond-agent-task@$ProjectId.iam.gserviceaccount.com" --member "serviceAccount:bond-agent-runtime@$ProjectId.iam.gserviceaccount.com" --role roles/iam.serviceAccountUser

Write-Output "基础资源完成。接下来创建 Cloud SQL PostgreSQL 16 实例、启用 vector 扩展，并在 Secret Manager 创建 database-url、deepseek-api-key 后运行 deploy.ps1。Vertex AI 通过运行时服务账号调用，不需要向量 API Key。运行时服务账号：bond-agent-runtime@$ProjectId.iam.gserviceaccount.com"

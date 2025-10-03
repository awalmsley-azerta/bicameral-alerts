# Deployment Guide

Complete guide to deploy the bicameral-alerts service to AWS ECS using GitHub Actions.

## Prerequisites

- AWS account with ECS cluster set up
- GitHub repository: `bicameral-alerts`
- Terraform infrastructure deployed (creates SQS queue, ECR repo, ECS service)

## 1. GitHub OIDC + IAM Role Setup

### Step 1: Create GitHub OIDC Provider in AWS (One-time setup)

If you haven't already set this up for your AWS account:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

### Step 2: Create IAM Role for GitHub Actions

```bash
# Save trust policy
cat > github-actions-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:awalmsley-azerta/bicameral-alerts:*"
        }
      }
    }
  ]
}
EOF

# Replace YOUR_ACCOUNT_ID with your AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed -i '' "s/YOUR_ACCOUNT_ID/$AWS_ACCOUNT_ID/g" github-actions-trust-policy.json

# Create the role
aws iam create-role \
  --role-name github-actions-bicameral-alerts \
  --assume-role-policy-document file://github-actions-trust-policy.json \
  --description "Role for GitHub Actions to deploy bicameral-alerts"

# Attach required policies
aws iam attach-role-policy \
  --role-name github-actions-bicameral-alerts \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

aws iam attach-role-policy \
  --role-name github-actions-bicameral-alerts \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess

# Get the role ARN (you'll need this for GitHub Secrets)
aws iam get-role \
  --role-name github-actions-bicameral-alerts \
  --query Role.Arn --output text
```

### Step 3: Add GitHub Secret

Add this secret to your GitHub repository (Settings → Secrets and variables → Actions):

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `AWS_ROLE_TO_ASSUME` | IAM Role ARN for OIDC | `arn:aws:iam::123456789012:role/github-actions-bicameral-alerts` |

**That's it!** No access keys needed. GitHub Actions will use OIDC to assume the role.

## 2. Verify Workflow Configuration

Check `.github/workflows/deploy.yml` and update if needed:

```yaml
env:
  AWS_REGION: us-east-1              # Your AWS region
  ECR_REPOSITORY: bicameral/alerts   # ECR repo name from Terraform output
  ECS_SERVICE: bicameral-alerts      # ECS service name from Terraform
  ECS_CLUSTER: bicameral-cluster     # Your ECS cluster name
  CONTAINER_NAME: alerts             # Container name in task definition
```

## 3. Infrastructure Setup (Terraform)

Ensure your Terraform infrastructure includes:

**In your infrastructure repo (`bicameral-infra`):**

```hcl
# variables.tf or local.auto.tfvars
alert_keywords = "codelco,enap,banco central,reforma tributaria"

# Optional: Use SSM Parameter for keywords
alert_keywords_ssm_param = "/bicameral/alerts/keywords"
```

**Apply Terraform:**

```bash
cd bicameral-infra
terraform plan
terraform apply
```

**Get outputs:**

```bash
terraform output sqs_alerts_queue_url
terraform output ecr_alerts_repository_url
```

## 4. Initial Manual Deployment (First Time Only)

Since GitHub Actions needs an existing task definition, do the first deployment manually:

```bash
# Get ECR repository URL
ECR_URL=$(cd ../bicameral-infra && terraform output -raw ecr_alerts_repository_url)

# Login to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_URL

# Build and push
docker build -t bicameral-alerts .
docker tag bicameral-alerts:latest $ECR_URL:latest
docker push $ECR_URL:latest

# ECS will automatically pull and run the new image
```

## 5. Automated Deployments

Once the initial setup is complete, every push to `main` will:

1. ✅ Build Docker image
2. ✅ Push to ECR with both `:latest` and `:$GITHUB_SHA` tags
3. ✅ Update ECS task definition
4. ✅ Deploy to ECS service
5. ✅ Wait for service stability

### Triggering a Deployment

```bash
# Commit and push to main
git add .
git commit -m "Update alert keywords"
git push origin main

# Or trigger manually from GitHub UI
# Actions → Deploy to AWS ECS → Run workflow
```

## 6. Monitoring

### View Logs

```bash
# Via AWS CLI
aws logs tail /ecs/bicameral/alerts --follow

# Via AWS Console
# CloudWatch → Log groups → /ecs/bicameral/alerts
```

### Check Service Status

```bash
aws ecs describe-services \
  --cluster bicameral-cluster \
  --services bicameral-alerts \
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount}'
```

### View Recent Alerts

```bash
aws logs filter-pattern /ecs/bicameral/alerts --filter-pattern "KEYWORD ALERT" --start-time 1h
```

## 7. Updating Keywords

### Option 1: Environment Variable (Terraform)

```hcl
# In bicameral-infra/local.auto.tfvars
alert_keywords = "codelco,enap,banco central,nueva keyword"
```

```bash
cd bicameral-infra
terraform apply
```

### Option 2: SSM Parameter

```bash
# Create/update SSM parameter
aws ssm put-parameter \
  --name /bicameral/alerts/keywords \
  --value '{"keywords": ["codelco", "enap", "banco central"]}' \
  --type String \
  --overwrite

# Update Terraform to use SSM
# In local.auto.tfvars:
alert_keywords_ssm_param = "/bicameral/alerts/keywords"
```

### Option 3: S3 File

```bash
# Upload keywords JSON to S3
echo '{"keywords": ["codelco", "enap", "banco central"]}' > keywords.json
aws s3 cp keywords.json s3://your-bucket/config/keywords.json

# Update ECS task definition environment variable:
ALERT_KEYWORDS_FILE=s3://your-bucket/config/keywords.json
```

## 8. Rollback

If a deployment fails:

```bash
# Via GitHub Actions: Re-run previous successful workflow

# Or manually:
aws ecs update-service \
  --cluster bicameral-cluster \
  --service bicameral-alerts \
  --task-definition bicameral-alerts:PREVIOUS_REVISION
```

## 9. Troubleshooting

### Service won't start

```bash
# Check task definition
aws ecs describe-task-definition --task-definition bicameral-alerts

# Check stopped tasks
aws ecs list-tasks --cluster bicameral-cluster --service-name bicameral-alerts --desired-status STOPPED

# Get failure reason
aws ecs describe-tasks --cluster bicameral-cluster --tasks TASK_ID
```

### No alerts being generated

1. Check keywords are configured:
   ```bash
   aws ecs describe-task-definition --task-definition bicameral-alerts | grep ALERT_KEYWORDS
   ```

2. Check SQS queue is receiving messages:
   ```bash
   aws sqs get-queue-attributes \
     --queue-url $(cd ../bicameral-infra && terraform output -raw sqs_alerts_queue_url) \
     --attribute-names ApproximateNumberOfMessages
   ```

3. Check CloudWatch logs for errors

### GitHub Actions failing

- Verify AWS credentials in GitHub Secrets
- Check IAM permissions for GitHub Actions user
- Ensure ECR repository exists
- Verify ECS service and cluster names match

## 10. Cost Optimization

```bash
# Scale down when not needed
aws ecs update-service \
  --cluster bicameral-cluster \
  --service bicameral-alerts \
  --desired-count 0

# Scale back up
aws ecs update-service \
  --cluster bicameral-cluster \
  --service bicameral-alerts \
  --desired-count 1
```

## Architecture Summary

```
GitHub Push
    ↓
GitHub Actions
    ├─ Build Docker Image
    ├─ Push to ECR
    └─ Update ECS Service
        ↓
ECS Fargate Task
    ├─ Pull image from ECR
    ├─ Load keywords from env/SSM/S3
    └─ Poll SQS alerts queue
        ↓
    Fetch transcript + analysis from S3
        ↓
    Check keywords
        ↓
    Generate alert (stdout → CloudWatch)
```

## Support

For issues or questions:
- Check CloudWatch logs: `/ecs/bicameral/alerts`
- Review GitHub Actions workflow runs
- Consult README.md for service configuration details


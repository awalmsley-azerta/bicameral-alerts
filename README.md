# Bicameral Alerts Service

Real-time keyword monitoring for Chilean legislative transcripts and analyses.

## Overview

The alerts service consumes completion events from the analyzer and checks both transcripts and analysis reports for specified keywords. When matches are found, it generates alerts (currently printed to stdout, future: email, webhooks, etc.).

## Features

- **Dual-source checking**: Scans both raw transcripts and AI-generated analyses
- **Case-insensitive matching**: Keywords are normalized for consistent detection
- **Flexible configuration**: Keywords via environment variables or JSON file
- **Idempotent processing**: Uses SQS message deletion for reliability
- **Future-ready**: Designed for multi-user keyword lists and email notifications

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SQS_ALERTS_QUEUE_URL` | Yes | SQS queue URL to consume from (analyzer outputs) |
| `ALERT_KEYWORDS` | No | Comma-separated list of keywords (case-insensitive) |
| `ALERT_KEYWORDS_FILE` | No | Path to JSON file with keywords (local or s3://) |
| `AWS_REGION` | No | AWS region (default: us-east-1) |
| `LOG_LEVEL` | No | Logging level (default: INFO) |
| `MINIMAL_LOGS` | No | Reduce log verbosity (default: true) |
| `SQS_VISIBILITY_TIMEOUT_SECONDS` | No | Message visibility timeout (default: 300) |

### Keyword Configuration

#### Option 1: Environment Variable
```bash
export ALERT_KEYWORDS="codelco,enap,banco central,reforma tributaria"
```

#### Option 2: JSON File (local)
```json
{
  "keywords": [
    "codelco",
    "enap",
    "banco central",
    "reforma tributaria"
  ]
}
```

```bash
export ALERT_KEYWORDS_FILE="./keywords.json"
```

#### Option 3: JSON File (S3)
```bash
export ALERT_KEYWORDS_FILE="s3://my-bucket/config/keywords.json"
```

## Usage

### Local Development

```bash
# Set up keywords
export ALERT_KEYWORDS="test,keyword,alert"

# Set queue URL
export SQS_ALERTS_QUEUE_URL="https://sqs.us-east-1.amazonaws.com/123456789/alerts-queue.fifo"

# Run
python main.py
```

### Test Mode

Test keyword matching without SQS:

```bash
export ALERT_KEYWORDS="hacienda,presupuesto"
export TEST_FILE="s3://bucket/transcripts/abc123.json"
python main.py
```

### Docker

```bash
# Build
docker build -t bicameral-alerts .

# Run
docker run -e AWS_REGION=us-east-1 \
  -e SQS_ALERTS_QUEUE_URL=https://... \
  -e ALERT_KEYWORDS="keyword1,keyword2" \
  bicameral-alerts
```

## Alert Format

When keywords are matched, alerts are printed to stdout:

```
================================================================================
🚨 KEYWORD ALERT
================================================================================
Run ID: abc123def456
Source: SENATE
Committee: Comisión de Hacienda
Date: 2025-01-15
Matched Keywords: codelco, presupuesto
Found in: transcript, analysis
Transcript: s3://bucket/transcripts/abc123def456.json
Analysis: s3://bucket/analyses/senate/html/hacienda-abc123de.html
PDF: s3://bucket/analyses/senate/pdf/hacienda-abc123de.pdf
================================================================================
```

## Architecture

```
Analyzer Service
    ↓ (publishes completion event)
SQS Queue (alerts-queue.fifo)
    ↓ (consumes)
Alerts Service
    ├─ Fetch transcript from S3
    ├─ Fetch analysis from S3
    ├─ Check keywords
    └─ Generate alert (print/email/webhook)
```

## Future Enhancements

- [ ] Email notifications (SES integration)
- [ ] Per-user keyword configuration
- [ ] Webhook integrations (Slack, Discord, etc.)
- [ ] Alert history/storage (DynamoDB)
- [ ] Advanced matching (fuzzy, regex, synonyms)
- [ ] Alert aggregation (daily digests)
- [ ] Web UI for keyword management

## IAM Permissions Required

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility"
      ],
      "Resource": "arn:aws:sqs:*:*:alerts-queue*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": [
        "arn:aws:s3:::transcripts-bucket/*",
        "arn:aws:s3:::analyses-bucket/*"
      ]
    }
  ]
}
```

## Development

### Adding Email Notifications

```python
# TODO: Add to main.py
import boto3
ses = boto3.client('ses', region_name='us-east-1')

def send_alert_email(alert_data):
    ses.send_email(
        Source='alerts@example.com',
        Destination={'ToAddresses': ['user@example.com']},
        Message={
            'Subject': {'Data': f'Alert: {alert_data["keywords"]}'},
            'Body': {'Text': {'Data': format_alert_text(alert_data)}}
        }
    )
```

### Adding Per-User Keywords

```python
# TODO: Load from DynamoDB or S3
def load_user_keywords():
    # user_id -> [keywords]
    return {
        "user1": ["codelco", "enap"],
        "user2": ["reforma tributaria", "pension"]
    }
```


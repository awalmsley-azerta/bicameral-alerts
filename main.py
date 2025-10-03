#!/usr/bin/env python3
"""
Bicameral Alerts Service

Monitors completed analyses and transcripts for specified keywords.
Consumes messages from the analyzer output queue and checks both:
- The raw transcript (may contain misspellings or variations)
- The AI-generated analysis (may have corrected names, brands, etc.)

Future enhancements:
- Email notifications
- Per-user keyword configuration
- Webhook integrations
"""
import os
import sys
import json
import time
import logging
from typing import Optional, List, Set
from urllib.parse import urlparse

try:
    import boto3  # type: ignore
except ImportError:
    boto3 = None  # type: ignore

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("alerts")

MINIMAL_LOGS = str(os.getenv("MINIMAL_LOGS", "true")).lower() in ("1", "true", "yes", "on")


def s3_client():
    """Create S3 client."""
    return boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


def sqs_client():
    """Create SQS client."""
    return boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _slugify_for_s3(value: str, max_length: int = 80) -> str:
    """Slugify a string for use in S3 keys or dictionary lookups."""
    import re
    v = (value or "").strip().lower()
    v = re.sub(r"\s+", "-", v)
    v = re.sub(r"[^a-z0-9\-_.]", "", v)
    v = re.sub(r"-+", "-", v).strip("-._")
    if len(v) > max_length:
        v = v[:max_length].rstrip("-._")
    return v or "session"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse s3://bucket/key into (bucket, key)."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an S3 URI: {uri}")
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def fetch_s3_text(s3_uri: str) -> Optional[str]:
    """Fetch text content from S3."""
    try:
        bucket, key = parse_s3_uri(s3_uri)
        s3 = s3_client()
        response = s3.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read()
        # Try to decode as JSON first (for transcripts)
        try:
            data = json.loads(content)
            # AssemblyAI transcript format
            if isinstance(data, dict) and "text" in data:
                return data["text"]
            # If it's a dict but no "text" field, return JSON string
            return json.dumps(data, ensure_ascii=False)
        except json.JSONDecodeError:
            # Plain text or HTML
            return content.decode("utf-8")
    except Exception as e:
        logger.warning(f"Failed to fetch {s3_uri}: {e}")
        return None


def load_keywords() -> dict:
    """Load keywords from environment variable or file.
    
    Keywords can be provided as:
    1. ALERT_KEYWORDS env var (comma-separated, case-insensitive)
    2. ALERT_KEYWORDS_FILE env var pointing to a JSON file with structured keywords
    
    Returns dict with structure:
    {
        "global": ["keyword1", "keyword2"],
        "commissions": {
            "commission-name": ["keyword3", "keyword4"]
        }
    }
    """
    result = {"global": set(), "commissions": {}}
    
    # From environment variable (treated as global keywords)
    keywords_env = os.getenv("ALERT_KEYWORDS", "").strip()
    if keywords_env:
        for kw in keywords_env.split(","):
            kw = kw.strip().lower()
            if kw:
                result["global"].add(kw)
    
    # From file (local or S3 - supports structured format)
    keywords_file = os.getenv("ALERT_KEYWORDS_FILE", "").strip()
    if keywords_file:
        try:
            if keywords_file.startswith("s3://"):
                content = fetch_s3_text(keywords_file)
                if content:
                    data = json.loads(content)
            else:
                with open(keywords_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            
            # Support new structured format: {"global": [...], "commissions": {...}}
            if isinstance(data, dict):
                # Load global keywords
                if "global" in data:
                    for kw in data["global"]:
                        kw = str(kw).strip().lower()
                        if kw:
                            result["global"].add(kw)
                
                # Load commission-specific keywords
                if "commissions" in data and isinstance(data["commissions"], dict):
                    for commission, kw_list in data["commissions"].items():
                        commission_key = str(commission).strip().lower()
                        result["commissions"][commission_key] = set()
                        for kw in kw_list:
                            kw = str(kw).strip().lower()
                            if kw:
                                result["commissions"][commission_key].add(kw)
                
                # Backward compatibility: support old format {"keywords": [...]}
                if "keywords" in data and "global" not in data:
                    for kw in data["keywords"]:
                        kw = str(kw).strip().lower()
                        if kw:
                            result["global"].add(kw)
            
            # Backward compatibility: support simple list format
            elif isinstance(data, list):
                for kw in data:
                    kw = str(kw).strip().lower()
                    if kw:
                        result["global"].add(kw)
        except Exception as e:
            logger.warning(f"Failed to load keywords from file {keywords_file}: {e}")
    
    return result


def check_keywords(text: str, keywords: Set[str]) -> List[str]:
    """Check if any keywords appear in text (case-insensitive, whole-word matching).
    
    Returns list of matched keywords.
    """
    if not text or not keywords:
        return []
    
    text_lower = text.lower()
    matches = []
    
    for keyword in keywords:
        # Simple substring search for now
        # TODO: Could enhance with word boundaries, regex, fuzzy matching, etc.
        if keyword in text_lower:
            matches.append(keyword)
    
    return matches


def process_analysis_event(event: dict, keywords_config: dict) -> None:
    """Process a single analysis completion event.
    
    Checks both transcript and analysis for keywords and prints alerts.
    Uses both global keywords and commission-specific keywords if available.
    """
    run_id = event.get("run_id", "unknown")
    source = event.get("source_type", "unknown")
    metadata = event.get("event_metadata", {})
    committee = metadata.get("committee") or metadata.get("title") or ""
    
    if not MINIMAL_LOGS:
        logger.info(f"Processing run_id={run_id} source={source} committee={committee}")
    
    # Build combined keyword set: global + commission-specific
    keywords_to_check: Set[str] = set(keywords_config.get("global", set()))
    
    # Add commission-specific keywords if available
    if committee:
        committee_slug = _slugify_for_s3(committee)
        if committee_slug in keywords_config.get("commissions", {}):
            commission_keywords = keywords_config["commissions"][committee_slug]
            keywords_to_check.update(commission_keywords)
            if not MINIMAL_LOGS:
                logger.debug(f"Added {len(commission_keywords)} commission-specific keywords for '{committee_slug}'")
    
    if not keywords_to_check:
        if not MINIMAL_LOGS:
            logger.debug(f"No keywords to check for run_id={run_id}")
        return
    
    # Extract S3 URIs
    s3_info = event.get("s3", {})
    transcript_uri = s3_info.get("transcript")
    analysis_html_uri = event.get("analysis_html_s3")
    analysis_pdf_uri = event.get("analysis_pdf_s3")
    
    # Prefer friendly URLs if available
    analysis_html_uri = event.get("analysis_html_s3_friendly") or analysis_html_uri
    
    all_matches: Set[str] = set()
    match_locations: List[str] = []
    
    # Check transcript
    if transcript_uri:
        if not MINIMAL_LOGS:
            logger.debug(f"Fetching transcript: {transcript_uri}")
        transcript_text = fetch_s3_text(transcript_uri)
        if transcript_text:
            matches = check_keywords(transcript_text, keywords_to_check)
            if matches:
                all_matches.update(matches)
                match_locations.append("transcript")
                if not MINIMAL_LOGS:
                    logger.info(f"Found {len(matches)} keyword(s) in transcript: {matches}")
    
    # Check analysis HTML
    if analysis_html_uri:
        if not MINIMAL_LOGS:
            logger.debug(f"Fetching analysis: {analysis_html_uri}")
        analysis_text = fetch_s3_text(analysis_html_uri)
        if analysis_text:
            matches = check_keywords(analysis_text, keywords_to_check)
            if matches:
                all_matches.update(matches)
                match_locations.append("analysis")
                if not MINIMAL_LOGS:
                    logger.info(f"Found {len(matches)} keyword(s) in analysis: {matches}")
    
    # Generate alert if any matches found
    if all_matches:
        metadata = event.get("event_metadata", {})
        committee = metadata.get("committee") or metadata.get("title") or "Unknown"
        date = metadata.get("date") or "Unknown date"
        
        alert_msg = (
            f"\n{'='*80}\n"
            f"🚨 KEYWORD ALERT\n"
            f"{'='*80}\n"
            f"Run ID: {run_id}\n"
            f"Source: {source.upper()}\n"
            f"Committee: {committee}\n"
            f"Date: {date}\n"
            f"Matched Keywords: {', '.join(sorted(all_matches))}\n"
            f"Found in: {', '.join(match_locations)}\n"
            f"Transcript: {transcript_uri or 'N/A'}\n"
            f"Analysis: {analysis_html_uri or 'N/A'}\n"
            f"PDF: {analysis_pdf_uri or 'N/A'}\n"
            f"{'='*80}\n"
        )
        
        print(alert_msg, flush=True)
        logger.info(f"ALERT: {len(all_matches)} keyword(s) matched for run_id={run_id}")
        
        # TODO: Send email notification
        # TODO: Post to webhook
        # TODO: Store alert in database
    else:
        if not MINIMAL_LOGS:
            logger.info(f"No keyword matches for run_id={run_id}")


def consume_loop():
    """Main loop: consume analyzer output queue and check for keyword alerts."""
    queue_url = os.getenv("SQS_ALERTS_QUEUE_URL")
    if not queue_url:
        logger.error("SQS_ALERTS_QUEUE_URL not set")
        sys.exit(1)
    
    if not boto3:
        logger.error("boto3 not available")
        sys.exit(1)
    
    # Load keywords configuration
    keywords_config = load_keywords()
    global_count = len(keywords_config.get("global", set()))
    commission_count = len(keywords_config.get("commissions", {}))
    
    if not global_count and not commission_count:
        logger.warning("No keywords configured. Set ALERT_KEYWORDS or ALERT_KEYWORDS_FILE.")
        logger.warning("Service will run but no alerts will be generated.")
    else:
        logger.info(f"Loaded {global_count} global keyword(s) and {commission_count} commission-specific keyword set(s)")
        if not MINIMAL_LOGS:
            logger.debug(f"Global keywords: {sorted(keywords_config.get('global', set()))}")
            for comm, kws in keywords_config.get("commissions", {}).items():
                logger.debug(f"Commission '{comm}': {sorted(kws)}")
    
    vis_timeout = int(os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "300") or "300")
    sqs = sqs_client()
    
    logger.info(f"Starting alerts service, polling {queue_url}")
    
    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10,
                MessageAttributeNames=["All"]
            )
            
            msgs = resp.get("Messages", [])
            if not msgs:
                continue
            
            for msg in msgs:
                receipt = msg["ReceiptHandle"]
                body = msg.get("Body", "{}")
                
                try:
                    # Extend visibility immediately
                    sqs.change_message_visibility(
                        QueueUrl=queue_url,
                        ReceiptHandle=receipt,
                        VisibilityTimeout=vis_timeout
                    )
                except Exception as e:
                    logger.warning(f"Failed to extend visibility: {e}")
                
                try:
                    event = json.loads(body)
                    process_analysis_event(event, keywords_config)
                    
                    # Delete message after successful processing
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON in message: {e}")
                    # Delete malformed messages
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                except Exception as e:
                    logger.exception(f"Error processing message: {e}")
                    # Leave message for retry (visibility timeout will expire)
        
        except KeyboardInterrupt:
            logger.info("Shutting down (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.exception(f"Error in main loop: {e}")
            time.sleep(5)


def main():
    """Entry point."""
    # Check for test mode
    test_file = os.getenv("TEST_FILE")
    if test_file:
        logger.info(f"Running in TEST mode with file: {test_file}")
        keywords_config = load_keywords()
        logger.info(f"Global keywords: {sorted(keywords_config.get('global', set()))}")
        logger.info(f"Commission keywords: {list(keywords_config.get('commissions', {}).keys())}")
        
        # Create mock event
        event = {
            "run_id": "test",
            "source_type": "test",
            "event_metadata": {"committee": "Test Committee", "date": "2025-01-01"},
            "s3": {"transcript": test_file},
            "analysis_html_s3": test_file,
        }
        process_analysis_event(event, keywords_config)
        return
    
    # Normal operation
    consume_loop()


if __name__ == "__main__":
    main()


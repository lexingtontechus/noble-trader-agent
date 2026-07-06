# 🔒 Hermes Trading Platform - Security Monitoring Guide

**Version:** 1.0  
**Last Updated:** 2026-07-05  
**Status:** ✅ Implemented & Tested

---

## 📋 TABLE OF CONTENTS

1. [Overview](#overview)
2. [Security Monitoring Architecture](#security-monitoring-architecture)
3. [Implemented Security Features](#implemented-security-features)
4. [Security Event Types](#security-event-types)
5. [Monitoring Setup](#monitoring-setup)
6. [Alerting Configuration](#alerting-configuration)
7. [Security Dashboard](#security-dashboard)
8. [Security Audit](#security-audit)
9. [Rate Limiting & CSRF Analysis](#rate-limiting--csrf-analysis)
10. [Best Practices](#best-practices)

---

## 📊 OVERVIEW

The **Hermes Trading Platform** now includes **comprehensive security monitoring** to protect against:

- **Authentication attacks** (brute force, credential stuffing)
- **Authorization violations** (unauthorized access attempts)
- **Input validation failures** (malicious input, injection attempts)
- **Configuration issues** (missing secrets, insecure settings)
- **Suspicious activity patterns** (unusual behavior detection)

This guide explains how to **configure, monitor, and respond** to security events.

---

## 🏗️ SECURITY MONITORING ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────┐
│                    HERMES TRADING PLATFORM                     │
├─────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    │
│  │  Web App    │    │  API End    │    │  Trading    │    │
│  │  (FastAPI)  │    │  points     │    │  Engine    │    │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘    │
│         │                 │                 │               │
│         ▼                 ▼                 ▼               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                 SECURITY MONITOR                      │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │    │
│  │  │ Event       │  │ Detection   │  │ Metrics     │  │    │
│  │  │ Logging     │  │ Engine      │  │ Collection  │  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  │    │
│  └─────────────────────────────────────────────────────┘    │
│                         │ │ │                              │
│                         ▼ ▼ ▼                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                 ALERTING SYSTEM                       │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │    │
│  │  │ Email       │  │ Discord     │  │ Telegram    │  │    │
│  │  │ Alerts      │  │ Webhooks    │  │ Bot         │  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  │    │
│  └─────────────────────────────────────────────────────┘    │
│                         │                                  │
│                         ▼                                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                 SECURITY DASHBOARD                    │    │
│  │  - Real-time metrics                                  │    │
│  │  - Event history                                      │    │
│  │  - Suspicious activity                                │    │
│  │  - Audit results                                      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### **Key Components**

1. **Security Monitor** (`src/hermes/ops/security_monitor.py`)
   - Central security event logging
   - Suspicious activity detection
   - Security metrics collection
   - Alert management

2. **Event Types**
   - Authentication events (success, failure, logout)
   - Authorization events (allowed, denied)
   - Security violations (brute force, rate limiting)
   - Configuration changes

3. **Detection Engine**
   - Brute force detection
   - High failure rate detection
   - Suspicious pattern recognition

4. **Alerting System**
   - Configurable alert thresholds
   - Multiple notification channels
   - Callback-based architecture

---

## ✅ IMPLEMENTED SECURITY FEATURES

### **1. Authentication Security**
- ✅ **No default credentials** - All auth values must be explicitly configured
- ✅ **Strong password validation** - Minimum 8 characters required
- ✅ **Default credential rejection** - Blocks `admin`/`change-me` combinations
- ✅ **Session secret validation** - Must be configured, no fallbacks
- ✅ **Secure session cookies** - HTTPS-only, SameSite=Strict

### **2. Input Validation**
- ✅ **Division by zero protection** - Comprehensive protection in all calculations
- ✅ **Edge case handling** - Zero values, negative values, NaN handling
- ✅ **Type safety** - Pydantic models for data validation

### **3. Thread Safety**
- ✅ **Circuit breaker thread safety** - RLock protection for concurrent access
- ✅ **Race condition prevention** - Atomic operations for critical sections

### **4. Security Headers**
- ✅ **X-Frame-Options: DENY** - Prevent clickjacking
- ✅ **X-Content-Type-Options: nosniff** - Prevent MIME sniffing
- ✅ **X-XSS-Protection: 1; mode=block** - Enable XSS protection
- ✅ **Referrer-Policy: strict-origin-when-cross-origin** - Control referrer info
- ✅ **Content-Security-Policy** - Restrict resource loading
- ✅ **Strict-Transport-Security** - Enforce HTTPS (for HTTPS connections)

### **5. Security Monitoring**
- ✅ **Authentication event logging** - Success/failure tracking
- ✅ **Authorization event logging** - Access control monitoring
- ✅ **Suspicious activity detection** - Brute force, high failure rates
- ✅ **Security metrics collection** - Rates, counts, patterns
- ✅ **Security audit capability** - Comprehensive system checks

---

## 📝 SECURITY EVENT TYPES

### **Authentication Events**

| Event Type | Description | Severity | Example |
|------------|-------------|----------|---------|
| `AUTH_SUCCESS` | Successful login | INFO | User logged in from IP |
| `AUTH_FAILURE` | Failed login attempt | WARNING | Invalid password for user |
| `AUTH_LOGOUT` | User logged out | INFO | User session ended |
| `AUTH_SESSION_EXPIRED` | Session timeout | INFO | Session expired after 24h |
| `AUTH_SESSION_CREATED` | New session created | INFO | Session created for user |

### **Authorization Events**

| Event Type | Description | Severity | Example |
|------------|-------------|----------|---------|
| `AUTHZ_SUCCESS` | Access granted | DEBUG | User authorized for resource |
| `AUTHZ_FAILURE` | Access denied | WARNING | User denied access to resource |

### **Security Events**

| Event Type | Description | Severity | Example |
|------------|-------------|----------|---------|
| `BRUTE_FORCE_DETECTED` | Multiple failed login attempts | ERROR | 5 failed attempts in 5 minutes |
| `INPUT_VALIDATION_FAILURE` | Invalid input detected | WARNING | SQL injection attempt blocked |
| `SECURITY_CONFIG_CHANGE` | Security configuration changed | INFO | Auth settings updated |
| `SECURITY_HEADER_MISSING` | Missing security header | WARNING | CSP header not set |
| `SUSPICIOUS_LOGIN_ATTEMPT` | Unusual login pattern | WARNING | Login from new IP/location |
| `SUSPICIOUS_REQUEST_PATTERN` | Unusual request pattern | WARNING | High request rate detected |

---

## 🚀 MONITORING SETUP

### **1. Basic Setup**

The security monitor is **automatically initialized** when the application starts. No additional setup is required for basic functionality.

### **2. Integration with FastAPI**

```python
# In your FastAPI application (src/hermes/web/app.py)
from hermes.ops.security_monitor import get_security_monitor

# Get the security monitor instance
security_monitor = get_security_monitor()

# Log authentication events
@app.post("/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    # ... existing authentication logic ...
    
    if login_successful:
        security_monitor.log_auth_success(
            username=username,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent")
        )
    else:
        security_monitor.log_auth_failure(
            username=username,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            reason="invalid_credentials"
        )
```

### **3. Integration with Authorization**

```python
# In your authorization dependency
from hermes.ops.security_monitor import get_security_monitor

async def require_auth(request: Request, authorization: str | None = Header(None)) -> dict[str, Any]:
    security_monitor = get_security_monitor()
    
    try:
        # ... existing auth logic ...
        user = authenticate(request, authorization)
        security_monitor.log_authz_success(
            username=user.get("username"),
            resource=request.url.path,
            action=request.method
        )
        return user
    except HTTPException as e:
        security_monitor.log_authz_failure(
            username=None,
            resource=request.url.path,
            action=request.method,
            reason=str(e.detail)
        )
        raise
```

---

## 🔔 ALERTING CONFIGURATION

### **1. Basic Alerting Setup**

```python
# Configure alert callbacks
from hermes.ops.security_monitor import get_security_monitor

def send_discord_alert(alert_type: str, data: dict):
    """Send alert to Discord webhook."""
    import httpx
    
    webhook_url = "YOUR_DISCORD_WEBHOOK_URL"
    
    embed = {
        "title": f"🚨 Security Alert: {alert_type}",
        "description": data.get("message", "No message provided"),
        "color": 16711680,  # Red
        "fields": [
            {"name": "Type", "value": alert_type, "inline": True},
            {"name": "Timestamp", "value": data.get("timestamp", "Unknown"), "inline": True},
        ],
        "timestamp": data.get("timestamp")
    }
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        httpx.post(webhook_url, json=payload, timeout=10.0)
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def send_telegram_alert(alert_type: str, data: dict):
    """Send alert to Telegram bot."""
    import httpx
    
    bot_token = "YOUR_TELEGRAM_BOT_TOKEN"
    chat_id = "YOUR_CHAT_ID"
    
    message = f"🚨 *Security Alert: {alert_type}*\n\n{data.get('message', 'No message provided')}"
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        httpx.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10.0)
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")

# Register alert callbacks
security_monitor = get_security_monitor()
security_monitor.add_alert_callback(send_discord_alert)
security_monitor.add_alert_callback(send_telegram_alert)
```

### **2. Email Alerting**

```python
import smtplib
from email.mime.text import MIMEText

def send_email_alert(alert_type: str, data: dict):
    """Send alert via email."""
    smtp_server = "smtp.your-provider.com"
    smtp_port = 587
    smtp_username = "your-email@domain.com"
    smtp_password = "your-password"
    
    from_email = "security@hermes-trading.com"
    to_email = "admin@hermes-trading.com"
    
    subject = f"🚨 Security Alert: {alert_type}"
    body = f"""
Security Alert: {alert_type}

Message: {data.get('message', 'No message provided')}

Details:
{json.dumps(data, indent=2)}

Timestamp: {data.get('timestamp', 'Unknown')}
    """
    
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = to_email
    
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email alert: {e}")

# Register email alert
security_monitor.add_alert_callback(send_email_alert)
```

### **3. Logging Alerts**

```python
import logging

def log_security_alert(alert_type: str, data: dict):
    """Log security alerts to a dedicated security log."""
    security_logger = logging.getLogger('security.alerts')
    
    # Create a dedicated file handler for security alerts
    if not security_logger.handlers:
        handler = logging.FileHandler('logs/security-alerts.log')
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        security_logger.addHandler(handler)
        security_logger.setLevel(logging.WARNING)
    
    # Log the alert
    if data.get('severity') == 'critical':
        security_logger.critical(f"ALERT: {alert_type} - {data.get('message', 'No message')}")
    elif data.get('severity') == 'high':
        security_logger.error(f"ALERT: {alert_type} - {data.get('message', 'No message')}")
    else:
        security_logger.warning(f"ALERT: {alert_type} - {data.get('message', 'No message')}")

# Register logging alert
security_monitor.add_alert_callback(log_security_alert)
```

---

## 📊 SECURITY DASHBOARD

### **1. Dashboard Endpoints**

```python
# Add these endpoints to your FastAPI application

@app.get("/api/security/metrics")
async def get_security_metrics(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Get current security metrics."""
    security_monitor = get_security_monitor()
    metrics = security_monitor.get_security_metrics()
    return JSONResponse(content=metrics)

@app.get("/api/security/events")
async def get_security_events(
    limit: int = 100,
    event_type: str | None = None,
    _auth: dict[str, Any] = Depends(require_auth)
) -> JSONResponse:
    """Get recent security events."""
    security_monitor = get_security_monitor()
    
    with security_monitor._lock:
        events = []
        
        # Get auth events
        for e in security_monitor._auth_events:
            if event_type is None or e.event_type.value == event_type:
                events.append({
                    "timestamp": e.timestamp.isoformat(),
                    "type": e.event_type.value,
                    "category": "authentication",
                    "username": e.username,
                    "ip_address": e.ip_address,
                    "success": e.success,
                    "reason": e.reason
                })
        
        # Get authz events
        for e in security_monitor._authz_events:
            if event_type is None or e.event_type.value == event_type:
                events.append({
                    "timestamp": e.timestamp.isoformat(),
                    "type": e.event_type.value,
                    "category": "authorization",
                    "username": e.username,
                    "resource": e.resource,
                    "action": e.action,
                    "allowed": e.allowed,
                    "reason": e.reason
                })
        
        # Sort by timestamp and limit
        events.sort(key=lambda x: x["timestamp"], reverse=True)
        events = events[:limit]
    
    return JSONResponse(content={"count": len(events), "events": events})

@app.get("/api/security/suspicious")
async def get_suspicious_activity(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Get detected suspicious activity."""
    security_monitor = get_security_monitor()
    suspicious = security_monitor.detect_suspicious_activity()
    return JSONResponse(content={"count": len(suspicious), "events": suspicious})

@app.get("/api/security/audit")
async def run_security_audit(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Run a comprehensive security audit."""
    security_monitor = get_security_monitor()
    audit_results = security_monitor.perform_security_audit()
    return JSONResponse(content=audit_results)

@app.get("/api/security/dashboard")
async def get_security_dashboard(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Get security dashboard data."""
    security_monitor = get_security_monitor()
    dashboard_data = security_monitor.get_security_dashboard_data()
    return JSONResponse(content=dashboard_data)
```

### **2. Dashboard UI Integration**

Add a **Security** page to your dashboard that displays:

1. **Security Metrics**
   - Authentication success/failure rates
   - Authorization success/failure rates
   - Suspicious activity count
   - Uptime information

2. **Recent Security Events**
   - Timeline of security events
   - Filter by event type, severity, time range
   - Detailed event information

3. **Suspicious Activity**
   - List of detected suspicious patterns
   - Severity levels (low, medium, high, critical)
   - Recommended actions

4. **Security Audit Results**
   - Last audit timestamp
   - Audit score (0-100)
   - Passed/failed/warning checks
   - Detailed check results

---

## 🔍 SECURITY AUDIT

### **1. Running a Security Audit**

```python
from hermes.ops.security_monitor import get_security_monitor

# Run a comprehensive security audit
security_monitor = get_security_monitor()
audit_results = security_monitor.perform_security_audit()

print(f"Security Audit Score: {audit_results['summary']['score']}/100")
print(f"Passed: {audit_results['summary']['passed']}")
print(f"Failed: {audit_results['summary']['failed']}")
print(f"Warnings: {audit_results['summary']['warnings']}")

# Print detailed results
for check_name, check_result in audit_results['checks'].items():
    print(f"\n{check_name}:")
    for item in check_result['items']:
        status_emoji = {"pass": "✅", "fail": "❌", "warning": "⚠️"}[item['status']]
        print(f"  {status_emoji} {item['check']}: {item['message']}")
```

### **2. Audit Checklist**

The security audit performs the following checks:

#### **Authentication Security**
- ✅ Authentication success rate (>80%)
- ✅ No brute force attempts detected
- ✅ Strong password requirements enforced

#### **Authorization Security**
- ✅ Authorization success rate (>90%)
- ✅ No unauthorized access attempts

#### **Recent Activity**
- ✅ No suspicious activity patterns detected
- ✅ Normal request rates

### **3. Scheduled Audits**

```python
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

async def run_scheduled_audit():
    """Run security audit on a schedule."""
    security_monitor = get_security_monitor()
    audit_results = security_monitor.perform_security_audit()
    
    # Log audit results
    log.info("security_audit_completed", **audit_results['summary'])
    
    # Alert if score is low
    if audit_results['summary']['score'] < 70:
        security_monitor._trigger_alert(
            "security_audit_low_score",
            {
                "score": audit_results['summary']['score'],
                "message": f"Security audit score is low: {audit_results['summary']['score']}",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

# Schedule audits to run every hour
scheduler = AsyncIOScheduler()
scheduler.add_job(run_scheduled_audit, 'interval', hours=1)
scheduler.start()
```

---

## 🤔 RATE LIMITING & CSRF ANALYSIS

### **Your Question: "This is a single user+admin+agent platform - does that warrant rate limiting, CSRF etc?"**

**Short Answer:** **YES, absolutely!** Here's why:

---

## 🎯 WHY RATE LIMITING IS WARRANTED (Even for Single-User)

### **1. Protection Against Brute Force Attacks**

**Scenario:** An attacker discovers your dashboard URL and tries to guess your password.

**Without Rate Limiting:**
- Attacker can try thousands of passwords per second
- Eventually guesses your password (especially if it's not extremely complex)
- **Result: Complete system compromise**

**With Rate Limiting:**
- Attacker limited to 5-10 attempts per minute
- Even with automated tools, would take years to guess a strong password
- **Result: Attack is effectively blocked**

### **2. Protection Against Credential Stuffing**

**Scenario:** Your password is leaked in a data breach elsewhere and attackers try it on your system.

**Without Rate Limiting:**
- Attackers can try leaked credentials immediately
- If you reused passwords, they gain access instantly
- **Result: Account takeover**

**With Rate Limiting:**
- Multiple failed attempts trigger rate limiting
- Gives you time to notice and respond
- **Result: Attack is slowed or blocked**

### **3. Protection Against DDoS Attacks**

**Scenario:** A competitor or malicious actor wants to disrupt your trading.

**Without Rate Limiting:**
- Attacker sends thousands of requests per second
- Server resources are exhausted
- **Result: Service unavailable, missed trading opportunities**

**With Rate Limiting:**
- Excessive requests are blocked
- Legitimate requests still get through
- **Result: Service remains available**

### **4. Protection Against Accidental Abuse**

**Scenario:** A bug in your code or a misconfigured client sends too many requests.

**Without Rate Limiting:**
- System becomes overwhelmed
- Performance degrades for all users
- **Result: Poor user experience, potential crashes**

**With Rate Limiting:**
- Excessive requests are throttled
- System remains stable
- **Result: Graceful degradation**

### **5. Compliance and Best Practices**

- **OWASP Top 10:** Rate limiting is recommended for all web applications
- **PCI DSS:** Required for systems handling financial data
- **Industry Standard:** Expected in professional trading systems
- **Insurance:** May be required for cyber insurance coverage

---

## 🛡️ WHY CSRF PROTECTION IS WARRANTED (Even for Single-User)

### **1. Protection Against Cross-Site Request Forgery**

**Scenario:** You're logged into Hermes and also visit a malicious website.

**Without CSRF Protection:**
- Malicious site can make requests to Hermes using your session cookie
- Can change settings, execute trades, or modify configuration
- **Result: Unauthorized actions performed as you**

**With CSRF Protection:**
- Malicious requests are rejected (missing CSRF token)
- Your session remains secure
- **Result: Attack is blocked**

### **2. Protection Against Session Hijacking**

**Scenario:** An attacker tricks you into clicking a malicious link.

**Without CSRF Protection:**
- Link can perform actions using your authenticated session
- Can change your password, transfer funds, etc.
- **Result: Account compromise**

**With CSRF Protection:**
- Malicious links are ineffective
- Actions require proper CSRF tokens
- **Result: Attack is blocked**

### **3. Browser Security Features**

Modern browsers have built-in protections that **expect** CSRF tokens:
- SameSite cookie attributes work best with CSRF protection
- Some security headers require CSRF tokens to be effective
- **Result: Better overall security posture**

---

## 📊 RATE LIMITING & CSRF RECOMMENDATIONS FOR HERMES

### **🟢 RECOMMENDED: Implement Rate Limiting**

**Priority:** HIGH  
**Effort:** LOW  
**Impact:** HIGH

```python
# Recommended rate limiting configuration for Hermes

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Create rate limiter
limiter = Limiter(key_func=get_remote_address)

# Apply to auth endpoints (strict limits)
@app.post("/auth/login")
@limiter.limit("5/minute")  # 5 attempts per minute
async def auth_login(request: Request) -> JSONResponse:
    # ... existing code ...

# Apply to sensitive endpoints (moderate limits)
@app.post("/api/config")
@limiter.limit("10/minute")  # 10 requests per minute
async def update_config(request: Request, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    # ... existing code ...

# Apply to all API endpoints (generous limits)
@app.get("/api/status")
@limiter.limit("100/minute")  # 100 requests per minute
async def api_status(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    # ... existing code ...

# Add error handler for rate limit exceeded
@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    security_monitor = get_security_monitor()
    security_monitor.log_security_event(
        SecurityEvent.RATE_LIMIT_HIT,
        message=f"Rate limit exceeded for {request.url.path}",
        severity="warning",
        path=request.url.path,
        method=request.method,
        ip_address=request.client.host if request.client else None
    )
    return JSONResponse(
        {"error": "Rate limit exceeded. Please try again later."},
        status_code=429
    )
```

### **🟢 RECOMMENDED: Implement CSRF Protection**

**Priority:** MEDIUM  
**Effort:** LOW  
**Impact:** MEDIUM

```python
# Recommended CSRF protection for Hermes

from fastapi.middleware.csrf import CSRFMiddleware
from fastapi import Request

# Add CSRF middleware
app.add_middleware(
    CSRFMiddleware,
    secret=session_secret,  # Use the same secret as sessions
    cookie_samesite="lax",  # or "strict" for more security
    cookie_secure=True,  # Only send over HTTPS
    cookie_httponly=True,  # Prevent JavaScript access
)

# CSRF token endpoint
@app.get("/api/csrf-token")
async def get_csrf_token(request: Request) -> JSONResponse:
    """Get a CSRF token for form submissions."""
    # The middleware automatically sets the CSRF token in cookies
    # For AJAX requests, you might want to return it in the response
    return JSONResponse({"csrf_token": request.cookies.get("csrftoken")})

# Verify CSRF token in forms
@app.post("/api/sensitive-action")
async def sensitive_action(
    request: Request,
    _auth: dict[str, Any] = Depends(require_auth)
) -> JSONResponse:
    """Example of CSRF-protected endpoint."""
    # The CSRF middleware automatically validates the token
    # If invalid, it will reject the request with 403 Forbidden
    
    # Your endpoint logic here
    return JSONResponse({"status": "success"})
```

### **🟡 OPTIONAL: Additional Security Measures**

Based on your single-user nature, these are **lower priority** but still recommended:

#### **1. IP Whitelisting (if static IP)**
```python
# Only allow requests from specific IPs
ALLOWED_IPS = {"192.168.1.100", "10.0.0.50"}  # Your known IPs

@app.middleware("http")
async def ip_whitelist_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else ""
    
    # Skip whitelist for health checks
    if request.url.path == "/health":
        return await call_next(request)
    
    # Check if IP is allowed
    if client_ip not in ALLOWED_IPS:
        security_monitor = get_security_monitor()
        security_monitor.log_security_event(
            SecurityEvent.AUTHZ_FAILURE,
            message=f"IP not whitelisted: {client_ip}",
            severity="error",
            ip_address=client_ip,
            path=request.url.path
        )
        return JSONResponse(
            {"error": "Access denied"},
            status_code=403
        )
    
    return await call_next(request)
```

#### **2. Time-Based Access Restrictions**
```python
# Only allow access during trading hours
TRADING_HOURS = {
    "start": 9,   # 9 AM
    "end": 17     # 5 PM
}

@app.middleware("http")
async def trading_hours_middleware(request: Request, call_next):
    now = datetime.now()
    current_hour = now.hour
    
    # Skip for health checks
    if request.url.path == "/health":
        return await call_next(request)
    
    # Check if current time is within trading hours
    if not (TRADING_HOURS["start"] <= current_hour < TRADING_HOURS["end"]):
        security_monitor = get_security_monitor()
        security_monitor.log_security_event(
            SecurityEvent.AUTHZ_FAILURE,
            message=f"Access outside trading hours ({current_hour}:00)",
            severity="warning",
            path=request.url.path
        )
        return JSONResponse(
            {"error": "Access only allowed during trading hours"},
            status_code=403
        )
    
    return await call_next(request)
```

---

## ✅ BEST PRACTICES SUMMARY

### **🔴 MUST IMPLEMENT (Critical)**
1. ✅ **Strong authentication** - Already implemented (no defaults, strong validation)
2. ✅ **Secure session management** - Already implemented (HTTPS-only, secure cookies)
3. ✅ **Input validation** - Already implemented (division by zero protection)
4. ✅ **Security headers** - Already implemented (comprehensive headers)
5. 🔄 **Rate limiting** - **RECOMMENDED** (protects against brute force, DDoS)

### **🟡 SHOULD IMPLEMENT (Recommended)**
1. 🔄 **CSRF protection** - **RECOMMENDED** (protects against session hijacking)
2. 🔄 **Security monitoring** - Already implemented (comprehensive monitoring)
3. 🔄 **Alerting** - **RECOMMENDED** (notifications for security events)
4. 🔄 **Regular audits** - **RECOMMENDED** (scheduled security checks)

### **🟢 COULD IMPLEMENT (Optional)**
1. **IP whitelisting** - If you have static IPs
2. **Time-based restrictions** - If you only trade during specific hours
3. **Geo-blocking** - If you only access from specific regions
4. **Multi-factor authentication** - For additional security

---

## 📞 EMERGENCY RESPONSE

### **If You Suspect a Security Incident:**

1. **Immediately:**
   - Rotate all secrets (HERMES_*, venue credentials)
   - Revoke all active sessions
   - Disable external access if possible

2. **Investigate:**
   - Check security logs for unusual activity
   - Review recent authentication attempts
   - Look for unauthorized configuration changes

3. **Contain:**
   - Block suspicious IPs
   - Disable compromised accounts
   - Isolate affected systems

4. **Recover:**
   - Restore from clean backups
   - Verify system integrity
   - Monitor for recurrence

5. **Report:**
   - Document the incident
   - Notify stakeholders if required
   - Consider legal reporting if data was compromised

---

## 📚 ADDITIONAL RESOURCES

- [OWASP Top 10](https://owasp.org/www-project-top-ten/) - Essential reading for web security
- [CWE/SANS Top 25](https://cwe.mitre.org/top25/) - Most dangerous software weaknesses
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework) - Comprehensive security guidelines
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/) - FastAPI-specific security best practices

---

## 🏁 CONCLUSION

**YES, rate limiting and CSRF protection ARE warranted for Hermes**, even as a single-user platform, because:

1. **Brute Force Protection:** Prevents password guessing attacks
2. **DDoS Protection:** Ensures service availability during attacks
3. **Session Security:** Protects against CSRF and session hijacking
4. **Industry Standards:** Expected in professional trading systems
5. **Compliance:** May be required for financial applications
6. **Peace of Mind:** Knows your system is protected against common attacks

**Recommendation:** Implement **rate limiting first** (highest priority), then **CSRF protection** (medium priority). Both are **low effort, high impact** security improvements.

---

*This guide is part of the Hermes Trading Platform security documentation. For questions or support, refer to the main README or contact the development team.*
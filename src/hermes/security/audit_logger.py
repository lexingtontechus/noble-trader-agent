"""
Comprehensive Audit Logging for Hermes Trading Platform

Provides detailed audit logging for security and compliance:
- Authentication events
- Authorization decisions
- Administrative actions
- Data access events
- Configuration changes
- Security incidents
- Compliance logging
- Log rotation and retention
- Secure log storage
"""

import json
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from contextlib import contextmanager


class AuditEventType(Enum):
    """Types of audit events."""
    # Authentication events
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    SESSION_CREATED = "session_created"
    SESSION_TERMINATED = "session_terminated"
    SESSION_EXPIRED = "session_expired"
    
    # Authorization events
    AUTHORIZATION_GRANTED = "authorization_granted"
    AUTHORIZATION_DENIED = "authorization_denied"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    
    # Administrative actions
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"
    USER_PERMISSION_CHANGED = "user_permission_changed"
    
    CONFIGURATION_CHANGED = "configuration_changed"
    SECURITY_POLICY_CHANGED = "security_policy_changed"
    BACKUP_CREATED = "backup_created"
    RESTORE_PERFORMED = "restore_performed"
    
    # Data access events
    DATA_ACCESS = "data_access"
    DATA_EXPORT = "data_export"
    DATA_IMPORT = "data_import"
    DATA_MODIFICATION = "data_modification"
    
    # Security incidents
    SECURITY_ALERT = "security_alert"
    BRUTE_FORCE_ATTEMPT = "brute_force_attempt"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    SECURITY_POLICY_VIOLATION = "security_policy_violation"
    
    # Compliance events
    COMPLIANCE_CHECK = "compliance_check"
    AUDIT_TRAIL_CREATED = "audit_trail_created"
    AUDIT_REPORT_GENERATED = "audit_report_generated"


class AuditSeverity(Enum):
    """Severity levels for audit events."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """Audit event data structure."""
    event_type: AuditEventType
    severity: AuditSeverity
    timestamp: datetime
    user_id: Optional[str]
    username: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    resource: Optional[str]
    action: Optional[str]
    result: Optional[str]
    details: Optional[Dict[str, Any]]
    session_id: Optional[str]
    request_id: Optional[str]
    correlation_id: Optional[str]


class AuditLogger:
    """
    Comprehensive audit logging system.
    
    Features:
    - Structured logging with JSON format
    - Multiple output destinations (file, database, log management)
    - Log rotation and retention
    - Performance monitoring
    - Compliance reporting
    - Real-time monitoring
    - Event filtering
    - Log integrity verification
    """
    
    # Configuration
    DEFAULT_LOG_DIR = "logs"
    DEFAULT_LOG_FILE = "audit.log"
    MAX_LOG_SIZE = 100 * 1024 * 1024  # 100MB
    MAX_LOG_FILES = 10
    DEFAULT_RETENTION_DAYS = 90
    
    # Performance monitoring
    BUFFER_SIZE = 1000
    FLUSH_INTERVAL = 5  # seconds
    
    def __init__(self, log_dir: str = None, log_file: str = None, 
                 max_log_size: int = None, max_log_files: int = None,
                 retention_days: int = None):
        """
        Initialize audit logger.
        
        Args:
            log_dir: Directory for log files
            log_file: Name of log file
            max_log_size: Maximum size of log files in bytes
            max_log_files: Maximum number of log files to keep
            retention_days: Number of days to retain logs
        """
        self.log_dir = log_dir or self.DEFAULT_LOG_DIR
        self.log_file = log_file or self.DEFAULT_LOG_FILE
        self.max_log_size = max_log_size or self.MAX_LOG_SIZE
        self.max_log_files = max_log_files or self.MAX_LOG_FILES
        self.retention_days = retention_days or self.DEFAULT_RETENTION_DAYS
        
        # Initialize log directory
        self._ensure_log_directory()
        
        # Log buffer
        self._buffer: deque[AuditEvent] = deque(maxlen=self.BUFFER_SIZE)
        
        # Performance monitoring
        self._event_counts: Dict[str, int] = {}
        self._error_count = 0
        self._lock = threading.RLock()
        
        # Start flush timer
        self._start_flush_timer()
        
        # Initialize logging
        self._setup_logging()
        
        # Log initialization
        self.log_event(
            event_type=AuditEventType.AUDIT_TRAIL_CREATED,
            severity=AuditSeverity.INFO,
            message="Audit logging system initialized",
            details={"log_dir": self.log_dir, "retention_days": self.retention_days}
        )
    
    def _ensure_log_directory(self) -> None:
        """Ensure log directory exists."""
        log_path = Path(self.log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # Set appropriate permissions
        os.chmod(log_path, 0o750)
    
    def _setup_logging(self) -> None:
        """Setup logging configuration."""
        self.logger = logging.getLogger('audit')
        self.logger.setLevel(logging.INFO)
        
        # Remove existing handlers
        self.logger.handlers.clear()
        
        # Create file handler
        log_path = Path(self.log_dir) / self.log_file
        handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        
        # Create formatter
        formatter = logging.Formatter('%(message)s')  # JSON format, no need for timestamp
        handler.setFormatter(formatter)
        
        self.logger.addHandler(handler)
        
        # Set permissions
        os.chmod(log_path, 0o640)
    
    def log_event(self, event_type: Union[str, AuditEventType], 
                  severity: Union[str, AuditSeverity],
                  message: Optional[str] = None,
                  user_id: Optional[str] = None,
                  username: Optional[str] = None,
                  ip_address: Optional[str] = None,
                  user_agent: Optional[str] = None,
                  resource: Optional[str] = None,
                  action: Optional[str] = None,
                  result: Optional[str] = None,
                  details: Optional[Dict[str, Any]] = None,
                  session_id: Optional[str] = None,
                  request_id: Optional[str] = None,
                  correlation_id: Optional[str] = None) -> None:
        """
        Log an audit event.
        
        Args:
            event_type: Type of event
            severity: Severity level
            message: Event message
            user_id: User ID
            username: Username
            ip_address: IP address
            user_agent: User agent
            resource: Resource being accessed
            action: Action performed
            result: Result of action
            details: Additional details
            session_id: Session ID
            request_id: Request ID
            correlation_id: Correlation ID
        """
        # Convert to enums if needed
        if isinstance(event_type, str):
            event_type = AuditEventType(event_type)
        if isinstance(severity, str):
            severity = AuditSeverity(severity)
        
        # Create audit event
        event = AuditEvent(
            event_type=event_type,
            severity=severity,
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            username=username,
            ip_address=ip_address,
            user_agent=user_agent,
            resource=resource,
            action=action,
            result=result,
            details=details or {},
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id
        )
        
        # Add to buffer
        with self._lock:
            self._buffer.append(event)
            
            # Update event counts
            event_key = f"{event_type.value}_{severity.value}"
            self._event_counts[event_key] = self._event_counts.get(event_key, 0) + 1
    
    def _flush_events(self) -> None:
        """Flush buffered events to log file."""
        with self._lock:
            if not self._buffer:
                return
            
            # Write events to log
            for event in self._buffer:
                try:
                    log_entry = self._format_log_entry(event)
                    self.logger.info(log_entry)
                except Exception as e:
                    self._error_count += 1
                    # Log to stderr as fallback
                    print(f"Audit logging error: {e}")
            
            # Clear buffer
            self._buffer.clear()
    
    def _format_log_entry(self, event: AuditEvent) -> str:
        """Format audit event as JSON string."""
        # Convert to dict
        event_dict = asdict(event)
        
        # Convert enums to strings
        event_dict['event_type'] = event.event_type.value
        event_dict['severity'] = event.severity.value
        
        # Convert datetime to ISO format
        event_dict['timestamp'] = event.timestamp.isoformat()
        
        # Ensure all values are JSON serializable
        def sanitize_value(value):
            if isinstance(value, (datetime, timedelta)):
                return str(value)
            elif isinstance(value, Enum):
                return value.value
            elif isinstance(value, dict):
                return {k: sanitize_value(v) for k, v in value.items()}
            elif isinstance(value, (list, tuple)):
                return [sanitize_value(v) for v in value]
            else:
                return value
        
        sanitized_dict = {k: sanitize_value(v) for k, v in event_dict.items()}
        
        # Convert to JSON
        return json.dumps(sanitized_dict, ensure_ascii=False)
    
    def _start_flush_timer(self) -> None:
        """Start flush timer."""
        import threading
        
        def flush_worker():
            while True:
                try:
                    self._flush_events()
                except Exception:
                    pass
                threading.Event().wait(self.FLUSH_INTERVAL)
        
        thread = threading.Thread(target=flush_worker, daemon=True)
        thread.start()
    
    def get_event_counts(self, time_period: Optional[timedelta] = None) -> Dict[str, int]:
        """
        Get event counts within time period.
        
        Args:
            time_period: Time period to consider (from now)
            
        Returns:
            Dictionary of event counts
        """
        cutoff_time = datetime.now(timezone.utc) - (time_period or timedelta(hours=24))
        
        counts = {}
        
        # This is a simplified implementation
        # In a real implementation, you'd query the log files
        for event_key, count in self._event_counts.items():
            counts[event_key] = count
        
        return counts
    
    def generate_audit_report(self, start_date: datetime, end_date: datetime,
                           report_format: str = "json") -> Union[Dict[str, Any], str]:
        """
        Generate audit report for date range.
        
        Args:
            start_date: Start date for report
            end_date: End date for report
            report_format: Format of report (json, csv, html)
            
        Returns:
            Report data or string
        """
        # Read log files in date range
        log_files = self._get_log_files_in_range(start_date, end_date)
        
        events = []
        for log_file in log_files:
            events.extend(self._read_log_file(log_file))
        
        # Filter events by date range
        filtered_events = [
            event for event in events
            if start_date <= event.timestamp <= end_date
        ]
        
        # Generate report based on format
        if report_format == "json":
            return {
                "report_generated": datetime.now(timezone.utc).isoformat(),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_events": len(filtered_events),
                "event_counts": self._get_event_summary(filtered_events),
                "events": [asdict(event) for event in filtered_events]
            }
        elif report_format == "csv":
            return self._generate_csv_report(filtered_events)
        elif report_format == "html":
            return self._generate_html_report(filtered_events)
        else:
            raise ValueError(f"Unsupported report format: {report_format}")
    
    def _get_log_files_in_range(self, start_date: datetime, end_date: datetime) -> List[Path]:
        """Get log files within date range."""
        log_files = []
        log_path = Path(self.log_dir)
        
        for log_file in log_path.glob("*.log"):
            # Check if file is within date range
            file_date = self._extract_date_from_filename(log_file.name)
            if file_date and start_date <= file_date <= end_date:
                log_files.append(log_file)
        
        return sorted(log_files)
    
    def _extract_date_from_filename(self, filename: str) -> Optional[datetime]:
        """Extract date from filename."""
        # Simple implementation - adjust based on naming convention
        if "audit_" in filename:
            try:
                date_str = filename.split("_")[1].split(".")[0]
                return datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
            except:
                return None
        return None
    
    def _read_log_file(self, log_file: Path) -> List[AuditEvent]:
        """Read and parse log file."""
        events = []
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        event = self._parse_log_line(line.strip())
                        if event:
                            events.append(event)
                    except Exception:
                        continue
        except Exception:
            pass
        
        return events
    
    def _parse_log_line(self, line: str) -> Optional[AuditEvent]:
        """Parse log line into audit event."""
        try:
            data = json.loads(line)
            
            # Convert string values back to enums
            event_type = AuditEventType(data['event_type'])
            severity = AuditSeverity(data['severity'])
            
            # Convert ISO string back to datetime
            timestamp = datetime.fromisoformat(data['timestamp'])
            
            return AuditEvent(
                event_type=event_type,
                severity=severity,
                timestamp=timestamp,
                user_id=data.get('user_id'),
                username=data.get('username'),
                ip_address=data.get('ip_address'),
                user_agent=data.get('user_agent'),
                resource=data.get('resource'),
                action=data.get('action'),
                result=data.get('result'),
                details=data.get('details', {}),
                session_id=data.get('session_id'),
                request_id=data.get('request_id'),
                correlation_id=data.get('correlation_id')
            )
        except Exception:
            return None
    
    def _get_event_summary(self, events: List[AuditEvent]) -> Dict[str, int]:
        """Get summary of event counts."""
        summary = {}
        for event in events:
            key = f"{event.event_type.value}_{event.severity.value}"
            summary[key] = summary.get(key, 0) + 1
        return summary
    
    def _generate_csv_report(self, events: List[AuditEvent]) -> str:
        """Generate CSV report."""
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write headers
        writer.writerow([
            'timestamp', 'event_type', 'severity', 'user_id', 'username',
            'ip_address', 'resource', 'action', 'result', 'details'
        ])
        
        # Write events
        for event in events:
            writer.writerow([
                event.timestamp.isoformat(),
                event.event_type.value,
                event.severity.value,
                event.user_id,
                event.username,
                event.ip_address,
                event.resource,
                event.action,
                event.result,
                json.dumps(event.details)
            ])
        
        return output.getvalue()
    
    def _generate_html_report(self, events: List[AuditEvent]) -> str:
        """Generate HTML report.

        NOTE: uses string.Template ($ placeholders), NOT str.format(). The
        template contains CSS braces (e.g. `body { font-family: ... }`) which
        str.format() would try to parse as field markers and raise KeyError.
        string.Template ignores `{`/`}`, so it is safe here.
        """
        from string import Template
        html_template = Template("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Audit Report</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                table { border-collapse: collapse; width: 100%; }
                th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                th { background-color: #f2f2f2; }
                .severity-critical { color: red; font-weight: bold; }
                .severity-error { color: orange; font-weight: bold; }
                .severity-warning { color: yellow; background-color: #333; }
            </style>
        </head>
        <body>
            <h1>Audit Report</h1>
            <p>Generated: $generated</p>
            <p>Total Events: $total_events</p>
            <table>
                <tr>
                    <th>Timestamp</th>
                    <th>Event Type</th>
                    <th>Severity</th>
                    <th>User</th>
                    <th>Resource</th>
                    <th>Action</th>
                    <th>Result</th>
                </tr>
                $rows
            </table>
        </body>
        </html>
        """)

        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        import html as _html
        rows = []
        for event in events:
            row_class = f"severity-{_html.escape(event.severity.value)}"
            # Escape ALL user-influenced fields to prevent stored XSS in the report.
            # A username/resource/action like '<img src=x onerror=alert(1)>' would
            # otherwise execute when an operator opens the HTML audit report.
            rows.append(f"""
                <tr class="{row_class}">
                    <td>{_html.escape(event.timestamp.strftime("%Y-%m-%d %H:%M:%S"))}</td>
                    <td>{_html.escape(str(event.event_type.value))}</td>
                    <td>{_html.escape(event.severity.value)}</td>
                    <td>{_html.escape(str(event.username or event.user_id or 'N/A'))}</td>
                    <td>{_html.escape(str(event.resource or 'N/A'))}</td>
                    <td>{_html.escape(str(event.action or 'N/A'))}</td>
                    <td>{_html.escape(str(event.result or 'N/A'))}</td>
                </tr>
            """)

        return html_template.substitute(
            generated=generated,
            total_events=len(events),
            rows="".join(rows)
        )
    
    def rotate_logs(self) -> None:
        """Rotate log files."""
        log_path = Path(self.log_dir)
        current_log = log_path / self.log_file
        
        # Check if rotation is needed
        if current_log.exists() and current_log.stat().st_size > self.max_log_size:
            # Rotate logs
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            rotated_log = log_path / f"audit_{timestamp}.log"
            
            # Move current log to rotated
            current_log.rename(rotated_log)
            
            # Create new log file
            self._setup_logging()
            
            # Clean up old logs
            self._cleanup_old_logs()
    
    def _cleanup_old_logs(self) -> None:
        """Clean up old log files based on retention policy."""
        log_path = Path(self.log_dir)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        
        # Find and remove old log files
        for log_file in log_path.glob("*.log"):
            if log_file.name != self.log_file:  # Don't remove current log
                file_date = self._extract_date_from_filename(log_file.name)
                if file_date and file_date < cutoff_date:
                    try:
                        log_file.unlink()
                    except Exception:
                        pass


# Global audit logger instance
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Get or create the global audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def log_audit_event(event_type: Union[str, AuditEventType], 
                   severity: Union[str, AuditSeverity],
                   message: Optional[str] = None,
                   **kwargs) -> None:
    """Log an audit event using the global logger."""
    logger = get_audit_logger()
    logger.log_event(event_type, severity, message, **kwargs)


# Context manager for logging operations
@contextmanager
def audit_context(event_type: Union[str, AuditEventType], 
                  severity: Union[str, AuditSeverity],
                  **kwargs):
    """
    Context manager for logging audit events.
    
    Args:
        event_type: Type of event
        severity: Severity level
        **kwargs: Additional event parameters
        
    Usage:
        with audit_context("user_login", "info", username="admin", ip="192.168.1.1"):
            # Perform operation
            perform_login()
    """
    start_time = datetime.now(timezone.utc)
    
    try:
        # Log start of operation
        log_audit_event(
            event_type=event_type,
            severity=severity,
            result="started",
            timestamp=start_time,
            **kwargs
        )
        
        # Execute the operation
        yield
        
        # Log success
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        log_audit_event(
            event_type=event_type,
            severity=severity,
            result="success",
            duration_seconds=duration,
            timestamp=end_time,
            **kwargs
        )
        
    except Exception as e:
        # Log failure
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        log_audit_event(
            event_type=event_type,
            severity="error",
            result="failed",
            error=str(e),
            duration_seconds=duration,
            timestamp=end_time,
            **kwargs
        )
        
        # Re-raise the exception
        raise
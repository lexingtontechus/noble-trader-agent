"""
Secure Session Management for Hermes Trading Platform

Provides secure session handling with features:
- Secure cookie generation and validation
- Session timeout and expiration
- IP binding and user agent validation
- Session hijacking protection
- Secure session storage
- Session token rotation
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import threading


@dataclass
class SessionData:
    """Session data container."""
    session_id: str
    user_id: str
    username: str
    created_at: float
    last_accessed: float
    expires_at: float
    ip_address: str
    user_agent: str
    is_secure: bool
    metadata: Dict[str, Any] = None


class SecureSessionManager:
    """
    Secure session management with protection against hijacking.
    
    Features:
    - Secure session token generation
    - IP and user agent binding
    - Session timeout and expiration
    - Token rotation
    - Session monitoring
    - Automatic cleanup
    """
    
    # Session configuration
    DEFAULT_SESSION_TIMEOUT = 28800  # 8 hours in seconds
    IDLE_TIMEOUT = 1800  # 30 minutes in seconds
    SECURE_COOKIE_NAME = "hermes_session"
    HTTP_ONLY = True
    SECURE_FLAG = True
    SAME_SITE = "Strict"
    
    # Security settings
    TOKEN_LENGTH = 32
    MAX_SESSIONS_PER_USER = 3
    TOKEN_ROTATION_INTERVAL = 3600  # 1 hour
    CLEANUP_INTERVAL = 3600  # 1 hour
    
    # Storage
    _sessions: Dict[str, SessionData] = {}
    _user_sessions: Dict[str, List[str]] = {}
    _lock = threading.RLock()
    
    def __init__(self, session_timeout: int = None, idle_timeout: int = None):
        """
        Initialize session manager.
        
        Args:
            session_timeout: Maximum session lifetime in seconds
            idle_timeout: Maximum idle time in seconds
        """
        self.session_timeout = session_timeout or self.DEFAULT_SESSION_TIMEOUT
        self.idle_timeout = idle_timeout or self.IDLE_TIMEOUT
        
        # Start cleanup thread
        self._start_cleanup_thread()
    
    def create_session(self, user_id: str, username: str, ip_address: str, 
                      user_agent: str, metadata: Dict[str, Any] = None) -> str:
        """
        Create a new secure session.
        
        Args:
            user_id: User identifier
            username: Username
            ip_address: Client IP address
            user_agent: Client user agent
            metadata: Additional session metadata
            
        Returns:
            Session token
        """
        with self._lock:
            # Check maximum sessions per user
            if user_id in self._user_sessions:
                current_sessions = self._user_sessions[user_id]
                if len(current_sessions) >= self.MAX_SESSIONS_PER_USER:
                    # Remove oldest session
                    oldest_session = current_sessions[0]
                    self._remove_session(oldest_session)
                    current_sessions.pop(0)
            
            # Generate secure session token
            session_id = self._generate_session_token()
            
            # Create session data
            now = time.time()
            session_data = SessionData(
                session_id=session_id,
                user_id=user_id,
                username=username,
                created_at=now,
                last_accessed=now,
                expires_at=now + self.session_timeout,
                ip_address=ip_address,
                user_agent=user_agent,
                is_secure=self._is_secure_connection(),
                metadata=metadata or {}
            )
            
            # Store session
            self._sessions[session_id] = session_data
            
            # Update user sessions
            if user_id not in self._user_sessions:
                self._user_sessions[user_id] = []
            self._user_sessions[user_id].append(session_id)
            
            # Log session creation
            self._log_session_event("created", session_data)
            
            return session_id
    
    def validate_session(self, session_token: str, ip_address: str, 
                        user_agent: str) -> Optional[SessionData]:
        """
        Validate a session token.
        
        Args:
            session_token: Session token to validate
            ip_address: Current client IP address
            user_agent: Current client user agent
            
        Returns:
            SessionData if valid, None otherwise
        """
        with self._lock:
            if not session_token:
                return None
            
            # Find session
            session_data = self._sessions.get(session_token)
            if not session_data:
                return None
            
            # Check if session is expired
            if time.time() > session_data.expires_at:
                self._remove_session(session_token)
                return None
            
            # Check idle timeout
            if time.time() - session_data.last_accessed > self.idle_timeout:
                self._remove_session(session_token)
                return None
            
            # Validate IP address if enabled
            if self._is_ip_binding_enabled():
                if session_data.ip_address != ip_address:
                    # Check for IP change
                    self._handle_ip_change(session_data, ip_address)
                    return None
            
            # Validate user agent if enabled
            if self._is_user_agent_enabled():
                if session_data.user_agent != user_agent:
                    # Check for user agent change
                    self._handle_user_agent_change(session_data, user_agent)
                    return None
            
            # Update last accessed time
            session_data.last_accessed = time.time()
            
            # Check if we need to rotate token
            if self._should_rotate_token(session_data):
                new_session_id = self._rotate_session_token(session_data)
                return self._sessions.get(new_session_id)
            
            return session_data
    
    def refresh_session(self, session_token: str) -> bool:
        """
        Refresh a session (extend expiration).
        
        Args:
            session_token: Session token to refresh
            
        Returns:
            True if session was refreshed
        """
        with self._lock:
            session_data = self._sessions.get(session_token)
            if not session_data:
                return False
            
            # Extend expiration
            session_data.expires_at = time.time() + self.session_timeout
            session_data.last_accessed = time.time()
            
            self._log_session_event("refreshed", session_data)
            return True
    
    def terminate_session(self, session_token: str) -> bool:
        """
        Terminate a session.
        
        Args:
            session_token: Session token to terminate
            
        Returns:
            True if session was terminated
        """
        with self._lock:
            if session_token in self._sessions:
                session_data = self._sessions[session_token]
                self._remove_session(session_token)
                self._log_session_event("terminated", session_data)
                return True
            return False
    
    def terminate_all_user_sessions(self, user_id: str) -> int:
        """
        Terminate all sessions for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            Number of sessions terminated
        """
        with self._lock:
            if user_id not in self._user_sessions:
                return 0
            
            terminated_count = 0
            for session_id in self._user_sessions[user_id]:
                if session_id in self._sessions:
                    session_data = self._sessions[session_id]
                    self._remove_session(session_id)
                    self._log_session_event("terminated", session_data)
                    terminated_count += 1
            
            # Clear user sessions
            del self._user_sessions[user_id]
            
            return terminated_count
    
    def get_session_info(self, session_token: str) -> Optional[Dict[str, Any]]:
        """
        Get session information (without sensitive data).
        
        Args:
            session_token: Session token
            
        Returns:
            Session information dictionary
        """
        with self._lock:
            session_data = self._sessions.get(session_token)
            if not session_data:
                return None
            
            # Return session info without sensitive data
            return {
                "session_id": session_data.session_id,
                "user_id": session_data.user_id,
                "username": session_data.username,
                "created_at": session_data.created_at,
                "last_accessed": session_data.last_accessed,
                "expires_at": session_data.expires_at,
                "ip_address": session_data.ip_address,  # Consider redacting this
                "user_agent": session_data.user_agent,  # Consider redacting this
                "is_secure": session_data.is_secure,
                "metadata": session_data.metadata
            }
    
    def get_active_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all active sessions for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            List of session information dictionaries
        """
        with self._lock:
            if user_id not in self._user_sessions:
                return []
            
            active_sessions = []
            for session_id in self._user_sessions[user_id]:
                if session_id in self._sessions:
                    session_info = self.get_session_info(session_id)
                    if session_info:
                        active_sessions.append(session_info)
            
            return active_sessions
    
    def cleanup_expired_sessions(self) -> int:
        """
        Remove all expired sessions.
        
        Returns:
            Number of sessions removed
        """
        with self._lock:
            now = time.time()
            expired_sessions = []
            
            for session_id, session_data in self._sessions.items():
                if now > session_data.expires_at:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                session_data = self._sessions[session_id]
                self._remove_session(session_id)
                self._log_session_event("expired", session_data)
            
            return len(expired_sessions)
    
    def _generate_session_token(self) -> str:
        """Generate a secure session token."""
        return secrets.token_urlsafe(self.TOKEN_LENGTH)
    
    def _rotate_session_token(self, session_data: SessionData) -> str:
        """
        Rotate session token for security.
        
        Args:
            session_data: Current session data
            
        Returns:
            New session token
        """
        # Create new session token
        new_session_id = self._generate_session_token()
        
        # Copy session data
        new_session_data = SessionData(
            session_id=new_session_id,
            user_id=session_data.user_id,
            username=session_data.username,
            created_at=session_data.created_at,
            last_accessed=session_data.last_accessed,
            expires_at=session_data.expires_at,
            ip_address=session_data.ip_address,
            user_agent=session_data.user_agent,
            is_secure=session_data.is_secure,
            metadata=session_data.metadata.copy()
        )
        
        # Remove old session
        self._remove_session(session_data.session_id)
        
        # Store new session
        self._sessions[new_session_id] = new_session_data
        
        # Update user sessions
        if session_data.user_id in self._user_sessions:
            # Replace old session ID with new one
            sessions = self._user_sessions[session_data.user_id]
            try:
                index = sessions.index(session_data.session_id)
                sessions[index] = new_session_id
            except ValueError:
                pass
        
        self._log_session_event("rotated", new_session_data)
        
        return new_session_id
    
    def _remove_session(self, session_id: str) -> None:
        """Remove a session."""
        session_data = self._sessions.get(session_id)
        if session_data:
            # Remove from storage
            del self._sessions[session_id]
            
            # Remove from user sessions
            if session_data.user_id in self._user_sessions:
                sessions = self._user_sessions[session_data.user_id]
                if session_id in sessions:
                    sessions.remove(session_id)
                # Clean up empty user sessions
                if not sessions:
                    del self._user_sessions[session_data.user_id]
    
    def _is_secure_connection(self) -> bool:
        """Check if connection is secure (HTTPS)."""
        # In a real implementation, check request.is_secure
        return True  # Assume secure for demo
    
    def _is_ip_binding_enabled(self) -> bool:
        """Check if IP binding is enabled."""
        return True  # Enabled by default for security
    
    def _is_user_agent_enabled(self) -> bool:
        """Check if user agent binding is enabled."""
        return True  # Enabled by default for security
    
    def _should_rotate_token(self, session_data: SessionData) -> bool:
        """Check if session token should be rotated."""
        return (time.time() - session_data.last_accessed) > self.TOKEN_ROTATION_INTERVAL
    
    def _handle_ip_change(self, session_data: SessionData, new_ip: str) -> None:
        """Handle IP address change in session."""
        # Log the change
        self._log_security_event(
            "ip_change_detected",
            session_data.username,
            {"old_ip": session_data.ip_address, "new_ip": new_ip}
        )
        
        # For now, terminate session (could implement verification flow)
        self._remove_session(session_data.session_id)
    
    def _handle_user_agent_change(self, session_data: SessionData, new_ua: str) -> None:
        """Handle user agent change in session."""
        # Log the change
        self._log_security_event(
            "user_agent_change_detected",
            session_data.username,
            {"old_ua": session_data.user_agent, "new_ua": new_ua}
        )
        
        # For now, terminate session (could implement verification flow)
        self._remove_session(session_data.session_id)
    
    def _log_session_event(self, event: str, session_data: SessionData) -> None:
        """Log session events."""
        # Import here to avoid circular imports
        from hermes.ops.security_monitor import SecurityMonitor
        
        if hasattr(self, '_security_monitor'):
            self._security_monitor.log_security_event(
                event_type=f"session_{event}",
                message=f"Session {event} for {session_data.username}",
                username=session_data.username,
                severity="info",
                session_id=session_data.session_id,
                ip_address=session_data.ip_address
            )
    
    def _log_security_event(self, event: str, username: str, data: Dict[str, Any]) -> None:
        """Log security events."""
        # Import here to avoid circular imports
        from hermes.ops.security_monitor import SecurityMonitor
        
        if hasattr(self, '_security_monitor'):
            self._security_monitor.log_security_event(
                event_type=f"security_{event}",
                message=f"Security event: {event} for {username}",
                username=username,
                severity="warning",
                **data
            )
    
    def _start_cleanup_thread(self) -> None:
        """Start the cleanup thread."""
        def cleanup_worker():
            while True:
                try:
                    self.cleanup_expired_sessions()
                except Exception:
                    pass
                time.sleep(self.CLEANUP_INTERVAL)
        
        thread = threading.Thread(target=cleanup_worker, daemon=True)
        thread.start()


# Global session manager instance
_session_manager: SecureSessionManager | None = None


def get_session_manager() -> SecureSessionManager:
    """Get or create the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SecureSessionManager()
    return _session_manager


def create_session(user_id: str, username: str, ip_address: str, 
                  user_agent: str, metadata: Dict[str, Any] = None) -> str:
    """Create a new session using the global manager."""
    manager = get_session_manager()
    return manager.create_session(user_id, username, ip_address, user_agent, metadata)


def validate_session(session_token: str, ip_address: str, user_agent: str) -> Optional[SessionData]:
    """Validate a session using the global manager."""
    manager = get_session_manager()
    return manager.validate_session(session_token, ip_address, user_agent)


def terminate_session(session_token: str) -> bool:
    """Terminate a session using the global manager."""
    manager = get_session_manager()
    return manager.terminate_session(session_token)


def refresh_session(session_token: str) -> bool:
    """Refresh a session using the global manager."""
    manager = get_session_manager()
    return manager.refresh_session(session_token)
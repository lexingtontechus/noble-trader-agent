"""
Password Security Utilities for Hermes Trading Platform

Provides secure password hashing, verification, and validation functions
following security best practices including:
- Argon2 for password hashing (PHC winner)
- Secure password validation
- Password strength checking
- Password history management
"""

import hashlib
import secrets
import re
from typing import Optional, List, Tuple
from dataclasses import dataclass


@dataclass
class PasswordValidationResult:
    """Result of password validation."""
    is_valid: bool
    errors: List[str]
    strength_score: int
    meets_requirements: bool


class PasswordSecurity:
    """
    Secure password handling utilities.
    
    Features:
    - Argon2id password hashing (if available)
    - PBKDF2 fallback hashing
    - Password strength validation
    - Secure password comparison
    - Password history management
    """
    
    # Password requirements
    MIN_LENGTH = 12
    MAX_LENGTH = 128
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_DIGITS = True
    REQUIRE_SPECIAL = True
    FORBIDDEN_PATTERNS = [
        r'password',
        r'admin',
        r'root',
        r'test',
        r'user',
        r'123456',
        r'qwerty',
        r'letmein'
    ]
    
    # Hashing parameters
    HASH_ITERATIONS = 600000  # For PBKDF2 (minimum recommended)
    HASH_ALGORITHM = 'sha256'
    SALT_LENGTH = 32
    KEY_LENGTH = 32
    
    def __init__(self):
        """Initialize password security utilities."""
        # Try to use Argon2 if available, fallback to PBKDF2
        self.use_argon2 = self._check_argon2_available()
        
    def _check_argon2_available(self) -> bool:
        """Check if Argon2 is available."""
        try:
            import argon2
            return True
        except ImportError:
            return False
    
    def hash_password(self, password: str) -> str:
        """
        Hash a password securely.
        
        Args:
            password: Plain text password to hash
            
        Returns:
            Hashed password string in format: algorithm:salt:hash
        """
        if not password:
            raise ValueError("Password cannot be empty")
        
        # Generate random salt
        salt = secrets.token_bytes(self.SALT_LENGTH)
        
        if self.use_argon2:
            # Use Argon2 if available
            hash_value = self._hash_with_argon2(password, salt)
            return f"argon2:{salt.hex()}:{hash_value}"
        else:
            # Fallback to PBKDF2
            hash_value = self._hash_with_pbkdf2(password, salt)
            return f"pbkdf2:{salt.hex()}:{hash_value}:{self.HASH_ITERATIONS}"
    
    def _hash_with_argon2(self, password: str, salt: bytes) -> str:
        """Hash password using Argon2id."""
        import argon2
        
        # Create argon2 hasher
        hasher = argon2.PasswordHasher(
            time_cost=3,  # 3 iterations
            memory_cost=65536,  # 64MB
            parallelism=4,
            hash_len=self.KEY_LENGTH,
            salt_len=len(salt)
        )
        
        # Hash password
        return hasher.hash(password.encode(), salt=salt)
    
    def _hash_with_pbkdf2(self, password: str, salt: bytes) -> bytes:
        """Hash password using PBKDF2 with HMAC-SHA256."""
        import hashlib
        
        # Derive key using PBKDF2
        dk = hashlib.pbkdf2_hmac(
            self.HASH_ALGORITHM,
            password.encode('utf-8'),
            salt,
            self.HASH_ITERATIONS,
            dklen=self.KEY_LENGTH
        )
        return dk
    
    def verify_password(self, password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash.
        
        Args:
            password: Plain text password to verify
            hashed_password: Stored hashed password
            
        Returns:
            True if password matches, False otherwise
        """
        if not password or not hashed_password:
            return False
        
        # Parse hashed password
        try:
            parts = hashed_password.split(':')
            if len(parts) < 3:
                return False
            
            algorithm = parts[0]
            salt_hex = parts[1]
            hash_value = parts[2]
            
            # Decode salt
            salt = bytes.fromhex(salt_hex)
            
            # Hash the provided password with same salt
            if algorithm == "argon2":
                return self._verify_argon2(password, salt, hash_value)
            elif algorithm == "pbkdf2":
                iterations = int(parts[3]) if len(parts) > 3 else self.HASH_ITERATIONS
                return self._verify_pbkdf2(password, salt, hash_value, iterations)
            else:
                return False
                
        except Exception:
            return False
    
    def _verify_argon2(self, password: str, salt: bytes, hash_value: str) -> bool:
        """Verify Argon2 password hash."""
        import argon2
        
        try:
            hasher = argon2.PasswordHasher(
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
                hash_len=self.KEY_LENGTH,
                salt_len=len(salt)
            )
            
            # Verify hash
            hasher.verify(hash_value, password.encode(), salt=salt)
            return True
            
        except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.InvalidHash):
            return False
    
    def _verify_pbkdf2(self, password: str, salt: bytes, hash_value: str, iterations: int) -> bool:
        """Verify PBKDF2 password hash."""
        import hashlib
        
        # Recompute hash with same parameters
        computed_hash = hashlib.pbkdf2_hmac(
            self.HASH_ALGORITHM,
            password.encode('utf-8'),
            salt,
            iterations,
            dklen=self.KEY_LENGTH
        )
        
        # Use constant-time comparison
        return secrets.compare_digest(
            computed_hash.hex(),
            hash_value
        )
    
    def validate_password_strength(self, password: str, username: Optional[str] = None) -> PasswordValidationResult:
        """
        Validate password strength and requirements.
        
        Args:
            password: Password to validate
            username: Username for similarity checking
            
        Returns:
            PasswordValidationResult with validation details
        """
        errors = []
        strength_score = 0
        requirements_met = 0
        
        # Check length
        if len(password) < self.MIN_LENGTH:
            errors.append(f"Password must be at least {self.MIN_LENGTH} characters long")
        else:
            strength_score += min(len(password), 20) * 2
            requirements_met += 1
        
        if len(password) > self.MAX_LENGTH:
            errors.append(f"Password must be no more than {self.MAX_LENGTH} characters long")
        
        # Check character requirements
        if self.REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
            errors.append("Password must contain at least one uppercase letter")
        else:
            strength_score += 10
            requirements_met += 1
        
        if self.REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
            errors.append("Password must contain at least one lowercase letter")
        else:
            strength_score += 10
            requirements_met += 1
        
        if self.REQUIRE_DIGITS and not re.search(r'\d', password):
            errors.append("Password must contain at least one digit")
        else:
            strength_score += 10
            requirements_met += 1
        
        if self.REQUIRE_SPECIAL and not re.search(r'[^A-Za-z0-9]', password):
            errors.append("Password must contain at least one special character")
        else:
            strength_score += 10
            requirements_met += 1
        
        # Check for forbidden patterns
        password_lower = password.lower()
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, password_lower):
                errors.append("Password contains forbidden patterns")
                break
        else:
            strength_score += 20
            requirements_met += 1
        
        # Check password against username (if provided)
        if username:
            username_lower = username.lower()
            if password_lower in username_lower or username_lower in password_lower:
                errors.append("Password cannot contain username")
            else:
                strength_score += 10
                requirements_met += 1
        
        # Check for common sequences
        if self._has_common_sequences(password):
            errors.append("Password contains common sequences")
        else:
            strength_score += 10
        
        # Ensure minimum requirements are met
        self.meets_requirements = requirements_met >= 4
        
        return PasswordValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            strength_score=strength_score,
            meets_requirements=self.meets_requirements
        )
    
    def _has_common_sequences(self, password: str) -> bool:
        """Check for common keyboard sequences and repeated patterns."""
        # Check for keyboard sequences
        sequences = [
            '123', '234', '345', '456', '567', '678', '789', '890',
            'qwe', 'wer', 'ert', 'rty', 'tyu', 'yui', 'uio', 'iop',
            'asd', 'sdf', 'dfg', 'fgh', 'ghj', 'hjk', 'jkl',
            'zxc', 'xcv', 'cvb', 'vbn', 'bnm'
        ]
        
        password_lower = password.lower()
        for sequence in sequences:
            if sequence in password_lower:
                return True
        
        # Check for repeated characters
        if re.search(r'(.)\1{2,}', password):
            return True
        
        return False
    
    def check_password_breach(self, password: str) -> bool:
        """
        Check if password has been exposed in data breaches.
        
        Args:
            password: Password to check
            
        Returns:
            True if password appears in breach data (for demo purposes)
            Note: In production, use haveibeenpwned API
        """
        # For demo, we'll check common passwords
        # In production, use actual breach database or API
        
        common_passwords = [
            'password', '123456', '123456789', '12345678', '12345',
            'qwerty', 'abc123', '111111', '123123', 'admin',
            'letmein', 'welcome', 'monkey', 'password1', 'sunshine'
        ]
        
        return password.lower() in common_passwords
    
    def is_password_history_unique(self, password: str, password_history: List[str]) -> bool:
        """
        Check if password is unique compared to history.
        
        Args:
            password: New password to check
            password_history: List of previous password hashes
            
        Returns:
            True if password is unique
        """
        if not password_history:
            return True
        
        # Check against each historical password
        for old_hash in password_history:
            if self.verify_password(password, old_hash):
                return False
        
        return True


# Global password security instance
_password_security: PasswordSecurity | None = None


def get_password_security() -> PasswordSecurity:
    """Get or create the global password security instance."""
    global _password_security
    if _password_security is None:
        _password_security = PasswordSecurity()
    return _password_security


def hash_password(password: str) -> str:
    """Hash a password using the global security instance."""
    security = get_password_security()
    return security.hash_password(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a password using the global security instance."""
    security = get_password_security()
    return security.verify_password(password, hashed_password)


def validate_password_strength(password: str, username: Optional[str] = None) -> PasswordValidationResult:
    """Validate password strength using the global security instance."""
    security = get_password_security()
    return security.validate_password_strength(password, username)
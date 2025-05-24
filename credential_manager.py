import os
import time
import requests
import logging
from pathlib import Path
from typing import Optional, Tuple
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class CredentialManager:
    def __init__(self, env_file: str = ".env"):
        """
        Initialize the credential manager.
        
        Args:
            env_file: Path to the .env file
        """
        self.env_file = Path(env_file)
        self.last_refresh_time = 0
        self.token_expiry = 0  # Will be set when we get a token
        self._load_credentials()
        
    def _load_credentials(self):
        """Load credentials from .env file."""
        load_dotenv(self.env_file)
        self.client_id = os.getenv("TWITCH_APP_CLIENT_ID")
        self.client_secret = os.getenv("TWITCH_APP_CLIENT_SECRET")
        self.refresh_token = os.getenv("TWITCH_BOT_REFRESH_TOKEN")
        self.access_token = os.getenv("TWITCH_BOT_ACCESS_TOKEN")
        
        if not all([self.client_id, self.client_secret, self.refresh_token, self.access_token]):
            missing = []
            if not self.client_id: missing.append("TWITCH_APP_CLIENT_ID")
            if not self.client_secret: missing.append("TWITCH_APP_CLIENT_SECRET")
            if not self.refresh_token: missing.append("TWITCH_BOT_REFRESH_TOKEN")
            if not self.access_token: missing.append("TWITCH_BOT_ACCESS_TOKEN")
            raise ValueError(f"Missing required credentials: {', '.join(missing)}")

    def _update_env_file(self, access_token: str, refresh_token: str):
        """Update the .env file with new tokens."""
        with open(self.env_file, "r") as file:
            lines = file.readlines()

        with open(self.env_file, "w") as file:
            for line in lines:
                if line.startswith("TWITCH_BOT_ACCESS_TOKEN="):
                    file.write(f"TWITCH_BOT_ACCESS_TOKEN={access_token}\n")
                elif line.startswith("TWITCH_BOT_REFRESH_TOKEN="):
                    file.write(f"TWITCH_BOT_REFRESH_TOKEN={refresh_token}\n")
                else:
                    file.write(line)
        
        logger.info("✅ .env file updated with new tokens")

    def _refresh_token(self) -> Tuple[Optional[str], Optional[str]]:
        """Refresh the Twitch access token."""
        url = 'https://id.twitch.tv/oauth2/token'
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }

        try:
            response = requests.post(url, data=payload)
            if response.status_code == 200:
                data = response.json()
                access_token = data['access_token']
                new_refresh_token = data.get('refresh_token', self.refresh_token)
                self.token_expiry = time.time() + data.get('expires_in', 3600)  # Default to 1 hour if not provided
                logger.info("✅ Token refreshed successfully")
                return access_token, new_refresh_token
            else:
                logger.error(f"❌ Error refreshing token: {response.status_code} {response.text}")
                return None, None
        except Exception as e:
            logger.error(f"❌ Exception while refreshing token: {e}")
            return None, None

    def get_valid_token(self) -> Optional[str]:
        """
        Get a valid access token, refreshing if necessary.
        Returns None if unable to get a valid token.
        """
        current_time = time.time()
        
        # Refresh if token is expired or will expire in the next 5 minutes
        if current_time >= self.token_expiry - 300:  # 5 minute buffer
            logger.info("Token expired or expiring soon, refreshing...")
            new_access, new_refresh = self._refresh_token()
            
            if new_access and new_refresh:
                self.access_token = new_access
                self.refresh_token = new_refresh
                self._update_env_file(new_access, new_refresh)
                self.last_refresh_time = current_time
            else:
                logger.error("Failed to refresh token")
                return None
                
        return self.access_token

    def validate_token(self) -> bool:
        """
        Validate the current access token with Twitch.
        Returns True if token is valid, False otherwise.
        """
        if not self.access_token:
            return False
            
        try:
            response = requests.get(
                'https://id.twitch.tv/oauth2/validate',
                headers={'Authorization': f'OAuth {self.access_token}'}
            )
            if response.status_code == 200:
                data = response.json()
                self.token_expiry = time.time() + data.get('expires_in', 3600)
                return True
            return False
        except Exception as e:
            logger.error(f"Error validating token: {e}")
            return False 
import requests
import os
from dotenv import load_dotenv

ENV_FILE = ".env"

def load_env():
    load_dotenv(ENV_FILE)
    return {
        "client_id": os.getenv("TWITCH_APP_CLIENT_ID"),
        "client_secret": os.getenv("TWITCH_APP_CLIENT_SECRET"),
        "refresh_token": os.getenv("TWITCH_BOT_REFRESH_TOKEN"),
    }

def refresh_twitch_token(client_id, client_secret, refresh_token):
    url = 'https://id.twitch.tv/oauth2/token'
    payload = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret,
    }

    response = requests.post(url, data=payload)
    if response.status_code == 200:
        data = response.json()
        access_token = data['access_token']
        new_refresh_token = data.get('refresh_token', refresh_token)  # fallback to old if not returned
        print("✅ Token refreshed")
        return access_token, new_refresh_token
    else:
        print("❌ Error refreshing token:", response.status_code, response.text)
        return None, None

def update_env_file(access_token, refresh_token):
    with open(ENV_FILE, "r") as file:
        lines = file.readlines()

    with open(ENV_FILE, "w") as file:
        for line in lines:
            if line.startswith("TWITCH_BOT_ACCESS_TOKEN="):
                file.write(f"TWITCH_BOT_ACCESS_TOKEN={access_token}\n")
            elif line.startswith("TWITCH_BOT_REFRESH_TOKEN="):
                file.write(f"TWITCH_BOT_REFRESH_TOKEN={refresh_token}\n")
            else:
                file.write(line)

    print("✅ .env file updated")

if __name__ == "__main__":
    creds = load_env()
    new_access, new_refresh = refresh_twitch_token(creds["client_id"], creds["client_secret"], creds["refresh_token"])
    if new_access and new_refresh:
        update_env_file(new_access, new_refresh)

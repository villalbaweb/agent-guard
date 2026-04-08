import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("GOOGLE_API_KEY")
if not api_key:
    print("GOOGLE_API_KEY not found.")
    exit(1)

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"

print(f"Requesting models from: {url.replace(api_key, 'AIza...')}")
response = requests.get(url)

if response.status_code == 200:
    models = response.json().get('models', [])
    print("\n--- Available Models ---")
    for model in models:
        # Only print gemini models to keep it clean
        if 'gemini' in model['name']:
            print(f"Name: {model['name']}, Description: {model['description'][:50]}...")
else:
    print(f"Error {response.status_code}: {response.text}")

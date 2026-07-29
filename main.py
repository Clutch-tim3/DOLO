from firebase_admin import initialize_app
from firebase_functions import https_fn
from app import app as fastapi_app

# Initialize the Firebase Admin SDK
try:
    initialize_app()
except ValueError:
    pass # Already initialized

# Wrap the FastAPI application for Firebase Cloud Functions
api = https_fn.on_request(fastapi_app)

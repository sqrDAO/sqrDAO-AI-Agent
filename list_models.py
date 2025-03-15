import os
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Configure Gemini
api_key = os.getenv('GEMINI_API_KEY')
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

genai.configure(api_key=api_key)

# List all available models
print("Available models:")
for model in genai.list_models():
    print(f"- Name: {model.name}")
    print(f"  Supported generation methods: {model.supported_generation_methods}")
    print() 
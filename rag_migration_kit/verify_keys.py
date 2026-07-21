import os
import voyageai
from groq import Groq
from dotenv import load_dotenv

def test_keys():
    # Force reload .env
    load_dotenv(override=True)
    
    voyage_key = os.getenv("VOYAGE_API_KEY2")
    groq_key = os.getenv("GROQ_API_KEY")
    
    log_content = []
    log_content.append("--- API Key Verification ---")
    
    if not voyage_key:
        log_content.append("❌ VOYAGE_API_KEY2: Not found in .env")
    else:
        log_content.append(f"🔍 VOYAGE_API_KEY2: {voyage_key[:6]}...{voyage_key[-4:]}")
        try:
            client = voyageai.Client(api_key=voyage_key)
            client.embed(["test"], model="voyage-3")
            log_content.append("✅ VOYAGE_API_KEY2: Working")
        except Exception as e:
            log_content.append(f"❌ VOYAGE_API_KEY2: Failed - {e}")

    if not groq_key:
        log_content.append("❌ GROQ_API_KEY: Not found in .env")
    else:
        log_content.append(f"🔍 GROQ_API_KEY: {groq_key[:6]}...{groq_key[-4:]}")
        try:
            client = Groq(api_key=groq_key)
            client.chat.completions.create(
                messages=[{"role": "user", "content": "test"}],
                model="llama-3.3-70b-versatile",
            )
            log_content.append("✅ GROQ_API_KEY: Working")
        except Exception as e:
            log_content.append(f"❌ GROQ_API_KEY: Failed - {e}")

    output_text = "\n".join(log_content)
    print(output_text)
    with open("key_verification.log", "w", encoding="utf-8") as f:
        f.write(output_text)

if __name__ == "__main__":
    test_keys()

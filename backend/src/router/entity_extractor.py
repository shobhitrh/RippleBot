import re
import json
import logging
from typing import List
from backend.src import config

logger = logging.getLogger(__name__)

def extract_query_entities(query_text: str) -> List[str]:
    """
    USIE v4 Entity Extractor:
    Extracts target entities, IDs, codes, numbers, and proper names from user queries,
    completely decoupling conversational phrasing/synonyms from database cell lookup.
    """
    if not query_text or not query_text.strip():
        return []

    q = query_text.strip()
    candidates = []

    # 1. Regex Pass (0ms, 100% deterministic for codes, IDs, numbers, quoted strings)
    # A. Quoted strings (e.g. "Regional Ops Mgr-RAO" or '60593501')
    quoted = re.findall(r'["\']([^"\']+)["\']', q)
    candidates.extend(quoted)

    # B. Pure numeric IDs/codes >= 4 digits (e.g. 60593501, 70881900)
    numeric_ids = re.findall(r'\b\d{4,}\b', q)
    candidates.extend(numeric_ids)

    # C. Uppercase / Alphanumeric hyphenated codes (e.g. IRM-Head-CB, RCSC-CSM-UsedCar)
    alpha_codes = re.findall(r'\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b', q)
    for ac in alpha_codes:
        if any(char.isdigit() for char in ac) or any(char.isupper() for char in ac):
            candidates.append(ac)

    # Deduplicate candidates preserving order
    unique_candidates = []
    seen = set()
    for c in candidates:
        c_clean = c.strip()
        if c_clean and c_clean.lower() not in seen and len(c_clean) >= 2:
            seen.add(c_clean.lower())
            unique_candidates.append(c_clean)

    if unique_candidates:
        logger.info(f"[USIE Entity Extractor] Regex Pass extracted: {unique_candidates}")
        return unique_candidates

    # 2. LLM Fallback Pass (~30ms on Groq) for proper names, phrases, and natural language titles
    # Skip LLM extractor if query is short or pure conversational greeting
    clean_words = [w for w in re.split(r'[^\w]+', q.lower()) if w]
    if len(clean_words) <= 2 or clean_words[0] in {"hi", "hello", "hey", "help", "who", "what", "how", "why"}:
        pass

    prompt = f"""You are an entity extraction module for a database search engine. Given a user query, extract the target entity name, job title, code, designation, or specific item the user is asking about.

Return ONLY a JSON array of extracted string entities, e.g. ["IRM-Head Wholesale Policy"] or ["60593501"].
If the query is a general question, count request, or contains no specific target entity, return [].

Examples:
Query: "what can you tell me about job id 60593501?" -> ["60593501"]
Query: "give me details on Regional Ops Mgr-RAO" -> ["Regional Ops Mgr-RAO"]
Query: "run a sanity check for IRM-Head Wholesale Policy-CB" -> ["IRM-Head Wholesale Policy-CB"]
Query: "how many candidates are active?" -> []

User Query: "{q}"
JSON Output:"""

    for gkey in config.GROQ_API_KEYS:
        try:
            from groq import Groq
            client = Groq(api_key=gkey)
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=40
            )
            txt = (completion.choices[0].message.content or "").strip()
            if "[" in txt and "]" in txt:
                json_str = txt[txt.find("["):txt.rfind("]")+1]
                extracted = json.loads(json_str)
                if isinstance(extracted, list) and extracted:
                    logger.info(f"[USIE Entity Extractor] LLM Pass extracted: {extracted}")
                    return [str(e).strip() for e in extracted if str(e).strip()]
        except Exception:
            continue

    return []

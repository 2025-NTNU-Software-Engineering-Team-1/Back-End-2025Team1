"""
AI Service layer for calling AI providers.
"""

import json
import requests
import re
from typing import Tuple, List, Optional

from .exceptions import AIServiceError
from .prompts import get_vtuber_response_schema
from .logging import get_logger
from mongo.engine import DEFAULT_AI_MODEL

logger = get_logger('service')

__all__ = [
    'call_ai_service',
    'DEFAULT_MODEL',
]

DEFAULT_MODEL = DEFAULT_AI_MODEL
DEFAULT_TIMEOUT = 60


def call_ai_service(
    api_key_value: str,
    system_prompt: str,
    history_messages: List[dict],
    user_message: str,
    current_code: str = "",
    model_name: str = DEFAULT_MODEL,
    use_structured_output: bool = True,
) -> Tuple[dict, int, int]:
    """
    Send request to Google Gemini API with conversation history.
    
    Args:
        api_key_value: The API key for authentication.
        system_prompt: System instruction for the AI.
        history_messages: Previous conversation messages.
        user_message: Current user message.
        current_code: Optional code submitted by student.
        model_name: Model to use (default: DEFAULT_AI_MODEL).
        use_structured_output: Whether to use response_schema for JSON output.
        
    Returns:
        Tuple of (response_json, input_tokens, output_tokens)
        
    Raises:
        AIServiceError: If the API call fails.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": api_key_value}

    # Build user content
    user_content_text = f"Student Question: {user_message}"
    if current_code:
        user_content_text += f"\n\nStudent Code:\n{current_code}"

    current_msg_obj = {"role": "user", "parts": [{"text": user_content_text}]}

    # Assemble conversation (History + Current)
    contents = []
    if history_messages:
        contents.extend(history_messages)
    contents.append(current_msg_obj)

    # Build generation config
    generation_config = {"temperature": 0.7}

    if use_structured_output:
        generation_config["response_mime_type"] = "application/json"
        generation_config["response_schema"] = get_vtuber_response_schema()

    # Build payload with systemInstruction
    payload = {
        "system_instruction": {
            "parts": [{
                "text": system_prompt
            }]
        },
        "contents": contents,
        "generationConfig": generation_config
    }

    try:
        logger.debug(f"Calling AI service: model={model_name}")
        response = requests.post(url,
                                 params=params,
                                 headers=headers,
                                 json=payload,
                                 timeout=DEFAULT_TIMEOUT)

        if response.status_code != 200:
            logger.error(
                f"AI Service Error {response.status_code}: {response.text}")
            raise AIServiceError(f"AI Provider Error: {response.text}",
                                 status_code=response.status_code)

        result = response.json()

        # Extract token usage
        usage = result.get('usageMetadata', {})
        in_tokens = usage.get('promptTokenCount', 0)
        out_tokens = usage.get('candidatesTokenCount', 0)

        # Parse response
        response_json = _parse_ai_response(result, use_structured_output)

        logger.info(
            f"AI response received: in_tokens={in_tokens}, out_tokens={out_tokens}"
        )
        return response_json, in_tokens, out_tokens

    except requests.exceptions.Timeout:
        logger.error("AI service request timed out")
        raise AIServiceError("AI service request timed out")
    except requests.exceptions.RequestException as e:
        logger.error(f"AI service request failed: {str(e)}")
        raise AIServiceError(f"AI service request failed: {str(e)}")


def _parse_ai_response(result: dict, is_structured: bool) -> dict:
    """
    Parse AI response from Gemini API.
    
    Args:
        result: Raw API response.
        is_structured: Whether structured output was requested.
        
    Returns:
        Parsed response dictionary.
    """
    try:
        content_text = result['candidates'][0]['content']['parts'][0]['text']

        if is_structured:
            # Structured output should be clean JSON
            return json.loads(content_text)

        # Legacy: Clean markdown wrapper if present
        clean_text = content_text.strip()
        if clean_text.startswith("```"):
            clean_text = re.sub(r"^```(?:json)?\s*",
                                "",
                                clean_text,
                                flags=re.IGNORECASE)
            clean_text = re.sub(r"\s*```$", "", clean_text)

        return json.loads(clean_text)

    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse AI response: {e}")
        raw_text = result.get('candidates',
                              [{}])[0].get('content',
                                           {}).get('parts',
                                                   [{}])[0].get('text', '')
        return {
            "data": [{
                "text": raw_text or "Error parsing AI response.",
                "emotion": "smile"
            }]
        }

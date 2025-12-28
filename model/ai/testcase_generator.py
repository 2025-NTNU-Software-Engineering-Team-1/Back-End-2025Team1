"""
AI Test Case Generator Service.

Generates test cases for problems using AI based on problem description.
"""

from typing import Optional, Dict, Any

from .context import get_problem_context
from .service import call_ai_service
from .logging import get_logger
from .prompts import TESTCASE_GENERATOR_PROMPT, build_testcase_prompt

logger = get_logger('testcase_generator')

__all__ = [
    'generate_testcase',
]


def _get_testcase_response_schema() -> dict:
    """
    Get JSON schema for structured output (array of testcases).
    """
    return {
        "type": "object",
        "properties": {
            "testcases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "The test case input"
                        },
                        "expected_output": {
                            "type": "string",
                            "description": "The expected output"
                        },
                        "explanation": {
                            "type": "string",
                            "description": "Brief explanation"
                        }
                    },
                    "required": ["input", "expected_output"]
                }
            }
        },
        "required": ["testcases"]
    }


def generate_testcase(
        problem_id: str = None,
        user=None,
        user_hint: str = "",
        api_key: Optional[str] = None,
        model: str = "gemini-flash-lite-latest",
        language: str = "zh-tw",
        problem_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Generate a test case for a problem using AI.
    
    Args:
        problem_id: The problem identifier (optional if problem_context is provided).
        user: User object making the request.
        user_hint: Optional hint from user about what kind of test case to generate.
        api_key: Gemini API key to use.
        model: The model to use for generation.
        language: User's preferred language for explanations (e.g., 'en', 'zh-tw').
        problem_context: Optional pre-built context dict with title, description, 
                         input_format, output_format. If provided, problem_id lookup is skipped.
        
    Returns:
        Dictionary containing:
        - input: The generated test case input
        - expected_output: The expected output
        - explanation: Brief explanation
        - tokens_used: Token usage info
        
    Raises:
        ValueError: If problem not found or generation fails.
    """
    logger.info(f"Generating test case for problem {problem_id or 'new'}")
    logger.debug(f"Language = {language}")

    # Get problem context - use provided context or fetch from DB
    if problem_context:
        context = problem_context
    elif problem_id:
        try:
            context = get_problem_context(problem_id, user)
        except Exception as e:
            logger.error(f"Failed to get problem context: {e}")
            raise ValueError(f"Problem not found: {problem_id}")
    else:
        raise ValueError(
            "Either problem_id or problem_context must be provided")

    # Build the prompt using helper from prompts.py
    final_prompt = build_testcase_prompt(context, user_hint, language)
    lang = '繁體中文' if language == 'chinese' else language
    lang_instruction = f"in {lang}" if lang else "in English"

    # Call AI service
    try:
        response, in_tokens, out_tokens = call_ai_service(
            api_key_value=api_key,
            system_prompt=final_prompt,
            history_messages=[],
            user_message=
            f"Please generate test cases. Remember to write explanations {lang_instruction}.",
            current_code="",
            model_name=model,
            use_structured_output=False)

        if not response:
            logger.error("AI service returned empty response")
            raise ValueError("Failed to generate test case: No response")

        # Extract testcases array from response
        testcases = response.get('testcases', [])

        # If response is old single format, convert to array
        if not testcases and response.get('input'):
            testcases = [{
                "input": response.get('input', ''),
                "expected_output": response.get('expected_output', ''),
                "explanation": response.get('explanation', '')
            }]

        if not testcases:
            logger.error("AI response did not contain testcases")
            raise ValueError(
                "Failed to generate test case: Invalid response format")

        result = {
            "testcases": testcases,
            "tokens_used": {
                "input": in_tokens,
                "output": out_tokens
            }
        }

        logger.info(
            f"Successfully generated {len(testcases)} test cases for problem {problem_id}"
        )
        return result

    except Exception as e:
        logger.error(f"Failed to generate test case: {e}")
        raise ValueError(f"Failed to generate test case: {str(e)}")

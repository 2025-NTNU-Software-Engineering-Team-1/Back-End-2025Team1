"""
AI Test Case Generator Service.

Generates test cases for problems using AI based on problem description.
"""

from typing import Optional, Dict, Any

from .context import get_problem_context
from .service import call_ai_service
from .logging import get_logger

logger = get_logger('testcase_generator')

__all__ = [
    'generate_testcase',
    'TESTCASE_GENERATOR_PROMPT',
]

# Prompt template for test case generation
TESTCASE_GENERATOR_PROMPT = """You are a test case generator for programming problems.

[Problem Info]
Title: {title}
Description: {description}
Input Format: {input_format}
Output Format: {output_format}

[User's Request]
{user_hint}

[Task]
Generate 3 test cases that match the problem's input/output format.
Each test case should cover different scenarios (e.g., basic case, edge case, larger input).

[Critical Constraints]
1. The input MUST strictly follow the Input Format specified above.
2. The output MUST be what a correct solution would produce.
3. Generate diverse test cases covering different scenarios.
4. Output strict JSON only.

[JSON Schema]
{{
  "testcases": [
    {{
      "input": "test case input as a string",
      "expected_output": "expected output as a string",
      "explanation": "brief explanation"
    }}
  ]
}}
"""


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


def generate_testcase(problem_id: str,
                      user,
                      user_hint: str = "",
                      api_key: Optional[str] = None,
                      model: str = "gemini-flash-lite-latest",
                      language: str = "zh-tw") -> Dict[str, Any]:
    """
    Generate a test case for a problem using AI.
    
    Args:
        problem_id: The problem identifier.
        user: User object making the request.
        user_hint: Optional hint from user about what kind of test case to generate.
        api_key: Gemini API key to use.
        model: The model to use for generation.
        language: User's preferred language for explanations (e.g., 'en', 'zh-tw').
        
    Returns:
        Dictionary containing:
        - input: The generated test case input
        - expected_output: The expected output
        - explanation: Brief explanation
        - tokens_used: Token usage info
        
    Raises:
        ValueError: If problem not found or generation fails.
    """
    logger.info(f"Generating test case for problem {problem_id}")

    # Get problem context
    try:
        context = get_problem_context(problem_id, user)
    except Exception as e:
        logger.error(f"Failed to get problem context: {e}")
        raise ValueError(f"Problem not found: {problem_id}")

    # Build the prompt
    user_hint_text = user_hint.strip(
    ) if user_hint else "Generate a representative test case."

    # Language instruction
    lang_instruction = f"in {language}" if language else "in English"

    # Update prompt with explicit language instruction
    final_prompt = TESTCASE_GENERATOR_PROMPT.format(
        title=context.get('title', ''),
        description=context.get('description', ''),
        input_format=context.get('input_format', ''),
        output_format=context.get('output_format', ''),
        user_hint=user_hint_text)

    # Inject language requirement into the task and schema
    final_prompt = final_prompt.replace(
        "Generate 3 test cases",
        f"Generate 3 test cases. The explanation field MUST be {lang_instruction}."
    ).replace('"explanation": "brief explanation"',
              f'"explanation": "brief explanation {lang_instruction}"')

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

"""
Prompt templates for AI services.
"""

from .logging import get_logger

logger = get_logger('prompts')

__all__ = [
    'EMOTION_KEYWORDS',
    'VTUBER_SYSTEM_PROMPT_TEMPLATE',
    'build_vtuber_prompt',
    'get_vtuber_response_schema',
    'TESTCASE_GENERATOR_PROMPT',
    'build_testcase_prompt',
]

# Valid emotions for Vtuber responses
EMOTION_KEYWORDS = ["smile", "unhappy", "tired", "surprised"]

# System prompt template for Vtuber AI assistant
VTUBER_SYSTEM_PROMPT_TEMPLATE = """You are an AI teaching assistant with a Vtuber persona.

[Context Info]
Problem: {title}
Description: {description}
Hint: {hint}
Input Format: {input_format}
Output Format: {output_format}
Current Time: {current_time}
Student's Last Submission: {last_submission_summary}
Last Submission Error: {last_submission_error}
Last Trial Result: {last_trial_summary}

[Task]
1. Act as the Vtuber character. Analyze the student's code/problem.
2. Provide guidance (hints), NOT full solutions.
3. If the student has errors, help them understand what went wrong.
4. Your final output must be a RAW JSON object.

[Critical Constraints]
1. The "text" field MUST contain **only the spoken dialogue**.
2. Do NOT put code blocks or Markdown inside the "text" field.
3. The "emotion" field must be exactly one string from: [{emotion_list_str}].
4. Output strict JSON only.
5. Do NOT greet the student or introduce yourself in every response. Continue naturally from the conversation.

[JSON Schema]
{{
  "data": [
    {{
      "text": "String",
      "emotion": "String"
    }}
  ]
}}
"""

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
Generate up to 20 test cases (minimum 3) that match the problem's input/output format.
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


def build_testcase_prompt(context: dict,
                          user_hint: str = "",
                          language: str = "zh-tw") -> str:
    """
    Build prompt for testcase generation.
    
    Args:
        context: Problem context with title, description, input_format, output_format.
        user_hint: User's hint about what kind of testcase to generate.
        language: Language for explanation field.
        
    Returns:
        Formatted prompt string.
    """
    hint_text = user_hint.strip(
    ) if user_hint else "Generate a representative test case."
    lang_instruction = f"in {language}" if language else "in 繁體中文"

    prompt = TESTCASE_GENERATOR_PROMPT.format(
        title=context.get('title', ''),
        description=context.get('description', ''),
        input_format=context.get('input_format', ''),
        output_format=context.get('output_format', ''),
        user_hint=hint_text)

    # Inject language requirement
    prompt = prompt.replace(
        "Generate 3 test cases",
        f"Generate 3 test cases. The explanation field MUST be {lang_instruction}."
    ).replace('"explanation": "brief explanation"',
              f'"explanation": "brief explanation {lang_instruction}"')

    logger.debug(
        f"Built testcase prompt for problem: {context.get('title', 'Unknown')}"
    )
    return prompt


def build_vtuber_prompt(context: dict) -> str:
    """
    Build system prompt for Vtuber AI assistant.
    
    Args:
        context: Problem context dictionary containing title, description, etc.
        
    Returns:
        Formatted system prompt string.
    """
    emotion_list_str = ", ".join(EMOTION_KEYWORDS)

    prompt = VTUBER_SYSTEM_PROMPT_TEMPLATE.format(
        title=context.get('title', ''),
        description=context.get('description', ''),
        hint=context.get('hint', '') or 'No hints available',
        input_format=context.get('input_format', ''),
        output_format=context.get('output_format', ''),
        current_time=context.get('current_time', ''),
        last_submission_summary=context.get('last_submission_summary',
                                            'No record'),
        last_submission_error=context.get('last_submission_error', '')
        or 'None',
        last_trial_summary=context.get('last_trial_summary', 'No record'),
        emotion_list_str=emotion_list_str)

    logger.debug(
        f"Built Vtuber prompt for problem: {context.get('title', 'Unknown')}")
    return prompt


def get_vtuber_response_schema() -> dict:
    """
    Get JSON schema for structured output.
    
    Returns:
        Schema dictionary for Gemini response_schema parameter.
    """
    return {
        "type": "object",
        "properties": {
            "data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string"
                        },
                        "emotion": {
                            "type": "string",
                            "enum": EMOTION_KEYWORDS
                        }
                    },
                    "required": ["text", "emotion"]
                }
            }
        },
        "required": ["data"]
    }

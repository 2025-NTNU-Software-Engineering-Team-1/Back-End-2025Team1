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

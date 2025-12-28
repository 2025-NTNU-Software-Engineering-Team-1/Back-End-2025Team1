import json
import requests
import re
from mongo import AiApiKey, AiApiLog, AiTokenUsage, Course, Problem, Submission
from flask import current_app
from mongo.engine import DEFAULT_AI_MODEL

__all__ = [
    'get_available_key',
    'get_problem_context',
    'call_ai_service',
    'save_ai_transaction',
    'process_ai_request',
    'EMOTION_KEYWORDS',
    'DEFAULT_AI_MODEL',
]

# Emotion
EMOTION_KEYWORDS = ["smile", "unhappy", "tired", "surprised"]

AI_SYSTEM_PROMPT_TEMPLATE = """
You are an AI teaching assistant with a Vtuber persona.

[Context Info]
Problem: {title}
Description: {description}
Input Format: {input_format}
Output Format: {output_format}
Student's Last Status: {last_submission_summary}

[Task]
1. Act as the Vtuber character. Analyze the student's code/problem.
2. Provide guidance (hints), NOT full solutions.
3. Your final output must be a RAW JSON object.

[Critical Constraints]
1. The "text" field MUST contain **only the spoken dialogue**.
2. Do NOT put code blocks or Markdown inside the "text" field.
3. The "emotion" field must be exactly one string from: [{emotion_list_str}].
4. Output strict JSON only.

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


def get_available_key(course_name: str):
    """
    Check and select an available API Key by course_name.
    Returns: (key_wrapper, error_message)
    """
    # 1. Get course via wrapper
    try:
        course = Course(course_name)
        if not course:  # Check if course exists
            return None, "Course not found"
    except Exception:
        return None, "Course not found"

    # Check if Course AI is enabled
    if not getattr(course, 'is_ai_vt_enabled', False):
        return None, "AI assistant is disabled for this course."

    # 2. Check model configuration
    model = getattr(course, 'ai_model', None)
    if not model or not getattr(model, 'is_active', False):
        return None, "No active AI model configured."

    # 3. Find available Keys via wrapper (by course_name string)
    keys = AiApiKey.get_active_keys_by_course_name(course_name)

    if not keys:
        return None, "No API keys configured."

    # Filter keys that haven't reached the model's RPD limit
    rpd_limit = getattr(model, 'rpd_limit', 1000)
    valid_keys = [k for k in keys if getattr(k, 'rpd', 0) < rpd_limit]

    if not valid_keys:
        return None, f"Daily limit ({rpd_limit} RPD) reached for all keys."

    # Load Balancing: Select the key with the lowest RPD usage
    best_key = min(valid_keys, key=lambda k: getattr(k, 'rpd', 0))
    return best_key, None


def get_problem_context(problem_id: str, user):
    """
    Collect context information by problem_id.
    """
    try:
        p = Problem(problem_id)
        if not p:  # Check if problem exists
            return None
    except Exception:
        return None

    # Assemble basic problem information
    desc = getattr(p, 'description', None)
    context = {
        "title": getattr(p, 'problem_name', ""),
        "description": getattr(desc, 'description', "") if desc else "",
        "input_format": getattr(desc, 'input', "") if desc else "",
        "output_format": getattr(desc, 'output', "") if desc else "",
        "samples": [],
        "last_submission_summary": "No previous submission found."
    }

    # Process sample test cases
    if desc and hasattr(desc, 'sample_input') and desc.sample_input:
        limit = 2
        sample_output = getattr(desc, 'sample_output', []) or []
        context["samples"] = [{
            "input": s_in,
            "output": s_out
        } for i, (s_in,
                  s_out) in enumerate(zip(desc.sample_input, sample_output))
                              if i < limit]

    # Get student's last submission status
    try:
        last_sub = Submission.get_last_submission(problem_id, user.username)
        if last_sub:
            status_map = {
                0: "Accepted",
                -1: "Wrong Answer",
                -2: "Compile Error",
                1: "Time Limit Exceeded",
                3: "Runtime Error"
            }
            status_str = status_map.get(getattr(last_sub, 'status', -1),
                                        "Unknown")
            score = getattr(last_sub, 'score', 0)
            context[
                "last_submission_summary"] = f"Result: {status_str}, Score: {score}/100"
    except Exception:
        pass

    return context


def call_ai_service(api_key_value: str,
                    system_prompt: str,
                    history_messages: list,
                    user_message: str,
                    current_code: str,
                    model_name: str = "gemini-2.5-flash"):
    """
    Send request to Google Gemini API with conversation history.
    """
    # Use v1 API with gemini-2.5-flash
    url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": api_key_value}

    # Prepare the current user's input content with system prompt embedded
    current_content_text = f"{system_prompt}\n\n---\n\nStudent Question: {user_message}\n"
    if current_code:
        current_content_text += f"\nStudent Code:\n{current_code}"

    # Construct the current message object
    current_msg_obj = {
        "role": "user",
        "parts": [{
            "text": current_content_text
        }]
    }

    # Assemble the complete conversation (History + Current)
    contents = []
    if history_messages:
        contents.extend(history_messages)
    contents.append(current_msg_obj)

    payload = {"contents": contents, "generationConfig": {"temperature": 0.7}}

    try:
        response = requests.post(url,
                                 params=params,
                                 headers=headers,
                                 json=payload,
                                 timeout=60)

        if response.status_code != 200:
            current_app.logger.error(f"Current Key: {api_key_value}")
            current_app.logger.error(
                f"AI Service Error {response.status_code}: {response.text}")
            raise Exception(
                f"AI Provider Error {response.status_code}: {response.text}")

        result = response.json()

        usage = result.get('usageMetadata', {})
        in_tokens = usage.get('promptTokenCount', 0)
        out_tokens = usage.get('candidatesTokenCount', 0)

        try:
            content_text = result['candidates'][0]['content']['parts'][0][
                'text']

            # Clean Markdown
            clean_text = content_text.strip()
            if clean_text.startswith("```"):
                clean_text = re.sub(r"^```(?:json)?\s*",
                                    "",
                                    clean_text,
                                    flags=re.IGNORECASE)
                clean_text = re.sub(r"\s*```$", "", clean_text)
            # -----------------------------

            response_json = json.loads(clean_text)

        except (KeyError, IndexError, json.JSONDecodeError):
            raw_text = result.get('candidates',
                                  [{}])[0].get('content', {}).get(
                                      'parts', [{}])[0].get('text', '')
            response_json = {
                "data": [{
                    "text": raw_text or "Error parsing AI response.",
                    "emotion": "smile"  # 
                }]
            }

        return response_json, in_tokens, out_tokens

    except Exception as e:
        raise e


def save_ai_transaction(course_name: str,
                        username: str,
                        key_obj,
                        user_text: str,
                        ai_response_text: str,
                        input_tokens: int,
                        output_tokens: int,
                        problem_id=None,
                        emotion: str = None):
    """
    After AI Responese, save the transaction records:
    1. Write conversation logs (AiApiLog - Clean)
    2. Write Token usage (AiTokenUsage - Billing)
    3. Update Key's cumulative usage (AiApiKey - RPD check)
    """
    try:
        # 1. Write conversation logs (excluding Token info, keep clean)
        # Note: Save User message first, then AI response
        AiApiLog.add_message(course_name, username, "user", user_text)
        AiApiLog.add_message(course_name, username, "model", ai_response_text,
                             emotion)

        # 2. Write Token usage (Billing)
        # key_obj may be Wrapper or Document, ensure the correct object is passed to ReferenceField
        # Assuming AiApiKey is a logic class, it can usually be directly accepted by ReferenceField (mongoengine feature)
        # Unwrap the wrapper if it has .obj
        key_doc = key_obj.obj if hasattr(key_obj, 'obj') else key_obj

        AiTokenUsage.add_usage(api_key_obj=key_doc,
                               course_name=course_name,
                               input_tokens=input_tokens,
                               output_tokens=output_tokens,
                               problem_id=problem_id)

        # 3. Update Key's cumulative usage (RPD and total)
        # Call AiApiKey (Logic Class) increment_usage
        if hasattr(key_obj, 'increment_usage'):
            key_obj.increment_usage(input_tokens, output_tokens)
        else:
            # If the passed object is an engine document, it may not have logic methods, be cautious
            # Usually, the object obtained through check_rate_limit is a Logic Instance
            pass

        return True

    except Exception as e:
        current_app.logger.error(f"Failed to save AI transaction: {str(e)}")
        return False


def process_ai_request(user, course_name, problem_id, message, current_code):
    """
    Process an AI request from a student.
    Returns:
        response_json (dict): The AI response in JSON format.
    Raises:
        HTTPError: If any error occurs during processing.
    """
    # 1. Check permissions and limits
    key, error_msg = get_available_key(course_name)
    if not key:
        current_app.logger.warning(f"AI Request Denied: {error_msg}")
        raise PermissionError(error_msg)

    # 2. Collect Context
    context = get_problem_context(problem_id, user)
    if not context:
        current_app.logger.warning("Problem context not found.")
        raise ValueError("Problem context not found.")

    # 3. Get History
    history_for_ai = []

    raw_history = AiApiLog.get_history(course_name, user.username) or []
    limit = 10
    recent_history = raw_history[-limit:] if raw_history else []

    for log in recent_history:
        role = log.get('role')
        parts = log.get('parts', [])
        text_content = "".join(
            [p.get('text', "") for p in parts if isinstance(p, dict)])

        history_for_ai.append({
            "role": role,
            "parts": [{
                "text": text_content
            }]
        })

    # 4. Assemble System Prompt
    course = Course(course_name)
    model_name = course.ai_model.name if (
        course and course.ai_model) else DEFAULT_AI_MODEL

    emotion_list_str = ", ".join(EMOTION_KEYWORDS)

    system_prompt = AI_SYSTEM_PROMPT_TEMPLATE.format(
        title=context.get('title', ''),
        description=context.get('description', ''),
        input_format=context.get('input_format', ''),
        output_format=context.get('output_format', ''),
        last_submission_summary=context.get('last_submission_summary',
                                            'No record'),
        emotion_list_str=emotion_list_str)

    # 5. Call AI Service
    try:
        response_json, in_tokens, out_tokens = call_ai_service(
            key.key_value,
            system_prompt,
            history_for_ai,
            message,
            current_code,
            model_name=model_name)
    except Exception as e:
        current_app.logger.error(f"AI service error: {str(e)}")
        raise RuntimeError(f"AI service error: {str(e)}")

    # 6. Validate Emotions
    def _validate_emotion(val):
        s = str(val).strip().lower() if val is not None else ""
        return s if s in EMOTION_KEYWORDS else "smile"

    if isinstance(response_json, dict) and isinstance(
            response_json.get('data'), list):
        for item in response_json['data']:
            if isinstance(item, dict):
                item['emotion'] = _validate_emotion(item.get('emotion'))

    # 7. Save Transaction (Log + Usage + RPD)
    ai_response_text = json.dumps(response_json, ensure_ascii=False)

    save_success = save_ai_transaction(course_name=course_name,
                                       username=user.username,
                                       key_obj=key,
                                       user_text=message,
                                       ai_response_text=ai_response_text,
                                       input_tokens=in_tokens,
                                       output_tokens=out_tokens,
                                       problem_id=problem_id)

    if not save_success:
        current_app.logger.error("Failed to save AI transaction stats.")
        # Not raising error here
        # Continue to return response

    return response_json

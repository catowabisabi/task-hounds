# Manager Step Prompts

## Prompt 1

```
Translate the plan into executable todo items. Use previous Worker and Reviewer evidence to decide status. Worker claims are evidence, but Manager makes the final status decision. Create subtasks for concrete implementation parts when a todo is broad. Do not release the task yet.

Return JSON with keys: todo_list, suggestion_verification.
todo_list must be an array of objects: {content, status, priority, subtasks:[{content,status,priority}]}.
```

## Prompt 2

```
f"You are the Manager agent inside Task Hounds flow_01.\nCurrent graph node: {step_name}.\n{instruction}\n\nReturn exactly one JSON object inside one ```json fenced block.\n\n{existing_section}=== HUMAN_DIRECTIVE ===\n{flow_input.human_directive}\n\n=== HUMAN_NEW_THOUGHT_AND_SUGGESTION ===\n{flow_input.human_new_thought_and_suggestion or '(none)'}\n\n=== HUMAN_SUGGESTED_NEW_TASK_OR_ITEM ===\n{flow_input.human_suggested_new_task_or_item or '(none)'}\n\n=== MANAGER MESSAGE HISTORY / HUMAN MESSAGE ===\n{flow_input.manager_message or '(none)'}\n\n=== EXISTING TODO STATE ===\n"
```

## Prompt 3

```
f"\n\n=== CURRENT GRAPH STATE ===\ninput_digest={state.input_digest or '(none)'}\ndecision={json.dumps(state.decision, ensure_ascii=False)}\nplan={state.plan or '(none)'}\ntodo_list={json.dumps(state.todo_list, ensure_ascii=False)}\nsuggestion_content={state.suggestion_content or '(none)'}\nsuggestion_verification={state.suggestion_verification or '(none)'}\n\n=== PREVIOUS WORKER_REPORT ===\n{loop_input.worker_report or '(none)'}\n\n=== REVIEWER_FEEDBACK ===\n{loop_input.reviewer_feedback or '(none)'}\n"
```

## Prompt 4

```
f"\n\n=== PREVIOUS RESPONSE WAS INVALID ===\nParser error: {error}\nRequired keys: {', '.join(sorted(required_keys))}\nReturn only one fenced JSON object. No prose before or after.\nBad response preview:\n{bad_reply[:1200]}\n"
```

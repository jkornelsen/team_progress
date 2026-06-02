## Scoped Files for Bundling

Prompt 1 along with full bundled code:

```
You are helping modify an existing Flask + SQLAlchemy project.

IMPORTANT:
- do not rewrite architecture
- avoid unrelated refactors
- identify risks before coding

Your task:
1. Identify all places likely requiring modification
2. Identify possible regressions
3. Propose implementation order
4. Do NOT generate code yet

Goal:
[enter goal]
```

Prompt 2:

```
Return a FILE MANIFEST for implementing this change.

Rules:
- output ONLY file paths
- include NO explanations
- include NO code
- include ONLY files you are confident are relevant or directly adjacent
- prefer completeness over minimality, but avoid noise
```

Prompt 3:

```
You are helping modify an existing Flask + SQLAlchemy project.
- output diffs to implement changes
- prefer minimal diffs
- preserve existing behavior unless explicitly changed
- avoid unrelated refactors

Goal:
```

Copy and paste goal from prompt 1.
Drag and drop bundled file selection.

## Goals

- How to get results grounded in source of truth
- Provide a bit of stable guidance along with fresh, task-specific context
- Bundle new code, old code, json data files

## Ideas

- Tell me when there's some reason that you believe what I ask isn't a good idea or is incorrect.
- Use inline constants from model e.g. '{{ Participant.OUTCOME }}' in javascript rather than hardcoded literal 'outcome'
- Do math on server rather than calculating 'current_val op val_transform' in javascript

## Repeatable Mini-Template

```
Context:

- This project is a rewrite.
- Old code defines intended behavior.
- New code is the target architecture.
- Old JSON reflects legacy data format.

Task:
<what you want>

Focus:

- preserve behavior, not structure
```

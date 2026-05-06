## Goals

- How to get results grounded in source of truth

- Provide a bit of stable guidance along with fresh, task-specific context

- Bundle new code, old code, json data files

## Ideas

- Use inline constants from model e.g. '{{ Participant.OUTCOME }}' in javascript rather than hardcoded literal 'outcome'

- Do math on server rather than calculating 'current_val op val_transform' in javascript

## Repeatable Mini-Template

Context:

- This project is a rewrite.
- Old code defines intended behavior.
- New code is the target architecture.
- Old JSON reflects legacy data format.

Task:
<what you want>

Focus:

- preserve behavior, not structure

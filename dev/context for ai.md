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

## Generating JSON Using Instructions

generate a json file according to the attached instructions. topic is an undersea quest rpg.

probably should have at least two steps, including a second one to make corrections

attempt 1: **Target Entity Count**: Typically 15-30 Items, 10–15 Locations, 6–12 Characters.
issues:
    desinations had just 'id' instead of 'loc2_id' -- interesting idea though
    didn't show anyone or anything in overview -- should always to this
    no door positions so destinations on grid are unusable
    chars also not given a position
    no piles of items either at locations or in char inventories
attempt 2: Target Entity Count: ~10-20 Items, ~8-20 Locations, ~4-8 Characters.
issues:
    event factors didn't put event field data inside dicts
    didn't show anyone or anything in overview
    no piles of items either at locations or in char inventories

not sure how interesting this is for ideas or randomly generated entertainment,
    but does seem helpful for testing,
    and to show how the game might be expected to work


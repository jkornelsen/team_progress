import json
import re
import os
import sys

SECTIONS = ["items", "locations", "characters", "attribs", "events"]

IN_DIR  = "alph_in"
OUT_DIR = "alph_out"

os.makedirs(IN_DIR,  exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

def find_section_bounds(content, section):
    """Return (array_start, array_end) — the positions of the '[' and matching ']'
    for the given top-level section key."""
    m = re.search(rf'"{section}":\s*\[', content)
    if not m:
        return None
    open_bracket = m.end() - 1          # position of '['
    i = open_bracket + 1
    depth = 1
    while i < len(content) and depth > 0:
        if content[i] == '[':
            depth += 1
        elif content[i] == ']':
            depth -= 1
        i += 1
    close_bracket = i - 1               # position of matching ']'
    return open_bracket, close_bracket


def split_entities(inner):
    """Split the text inside a section array into:
      - a list of (prefix, entity_blob) tuples, where prefix is the
        whitespace/comma before each entity object and entity_blob is the
        full {...} object text (brace-depth tracked so nesting is handled)
      - a trailer string: any content after the last closing '}'

    Returns (entities, trailer).
    """
    entities = []
    i = 0
    n = len(inner)

    while i < n:
        # Collect the gap before the next '{' (whitespace, commas, newlines)
        gap_start = i
        while i < n and inner[i] != '{':
            i += 1
        if i >= n:
            # No more objects — everything remaining is trailing content
            return entities, inner[gap_start:]
        prefix = inner[gap_start:i]

        # Collect the full {...} object using brace-depth tracking
        brace_start = i
        depth = 0
        while i < n:
            if inner[i] == '{':
                depth += 1
            elif inner[i] == '}':
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        entity_blob = inner[brace_start:i]
        entities.append((prefix, entity_blob))

    return entities, ""  # no trailing content

def name_stripped(entity_blob):
    m = re.search(r'"name":\s*"((?:[^"\\]|\\.)*)"', entity_blob)
    if m is None:
        return ''
    decoded = json.loads(f'"{m.group(1)}"')
    return re.sub(r'^\W+', '', decoded)

def sort_section(content, section):
    """Sort the entities in one section by name and return updated content."""
    bounds = find_section_bounds(content, section)
    if bounds is None:
        return content

    open_bracket, close_bracket = bounds
    inner = content[open_bracket + 1 : close_bracket]  # everything inside [ ... ]

    entities, trailer = split_entities(inner)
    if len(entities) <= 1:
        return content                  # nothing to sort

    # Sort only the entity blobs; keep prefixes tied to their original positions
    # Strategy: preserve the first prefix exactly, sort the blobs, and reassign
    # the remaining prefixes in order so separators stay unchanged.
    prefixes = [p for p, _ in entities]
    blobs    = [b for _, b in entities]

    sorted_blobs = sorted(blobs, key=name_stripped)

    # Reassemble: pair each sorted blob with its original prefix, then append trailer
    new_inner = "".join(p + b for p, b in zip(prefixes, sorted_blobs)) + trailer

    # Splice back — only the inside of the array changes
    return content[:open_bracket + 1] + new_inner + content[close_bracket:]


def process_file(in_path, out_path):
    with open(in_path, "r", encoding="utf-8") as f:
        content = f.read()

    for section in SECTIONS:
        content = sort_section(content, section)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  {in_path}  →  {out_path}")


files = [fn for fn in os.listdir(IN_DIR) if os.path.isfile(os.path.join(IN_DIR, fn))]

if not files:
    print(f"No files found in '{IN_DIR}/'. Add files there and re-run.")
    sys.exit(0)

print(f"Processing {len(files)} file(s)...")
for fn in files:
    in_path  = os.path.join(IN_DIR,  fn)
    out_path = os.path.join(OUT_DIR, fn)
    process_file(in_path, out_path)

print("Done.")

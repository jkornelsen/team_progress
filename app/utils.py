import re
import locale
import logging
from flask import g, session, request, url_for, redirect
from markupsafe import Markup
import bleach
from bleach.css_sanitizer import CSSSanitizer
from sqlalchemy.dialects.postgresql import Range

logger = logging.getLogger(__name__)

# Constants for Abbreviated format
SUFFIXES = [
    '', 'k', 'm', 'b', 't', 'q', 'Q', 's', 'S', 'o', 'n', 'd',
    'u', 'U', 'v', 'V', 'w', 'W', 'x', 'X', 'y', 'Y', 'z', 'Z'
]

# ------------------------------------------------------------------------
# Number Formatting logic
# ------------------------------------------------------------------------

def format_num(value, nformat='en_US'):
    """
    Formats a number based on the session's 'number_format' setting.
    Supports: 'sci', 'abbr', and standard locales like 'en_US' or 'de_DE'.
    """
    if value is None or value == '':
        return ''
    
    try:
        value = float(value)
    except (ValueError, TypeError):
        return str(value)

    # Handle small integers cleanly
    if abs(value) < 1000 and int(value) == value:
        return str(int(value))

    if nformat == "sci":  # Scientific: 1.23e6
        formatted = "{:.2e}".format(value)
        # Clean up leading zeros in exponent for readability
        formatted = re.sub(r'e\+0?(\d)', r'e\1', formatted)
        formatted = re.sub(r'e-0?(\d)', r'e-\1', formatted)
        return formatted

    if nformat == "abbr":  # Abbreviated: 1.23m
        chunk = 0
        abs_val = abs(value)
        while abs_val >= 1000 and chunk < len(SUFFIXES) - 1:
            abs_val /= 1000.0
            chunk += 1
        # Use 2 decimal places for abbreviated chunks
        return f"{abs_val if value >=0 else -abs_val:.2f}{SUFFIXES[chunk]}"

    # Standard Locale Formatting
    try:
        # Fallback to C if locale is invalid
        current_locale = nformat if '.' in nformat else f"{nformat}.UTF-8"
        locale.setlocale(locale.LC_ALL, current_locale)
    except locale.Error:
        locale.setlocale(locale.LC_ALL, 'C')

    # Determine precision: show up to 7 significant digits
    try:
        val_str = locale.format_string("%.7f", value, grouping=True)
        # Strip trailing zeros and the decimal point if it becomes empty
        val_str = val_str.rstrip('0').rstrip(locale.localeconv()['decimal_point'])
        return val_str
    except Exception:
        return str(value)

def unformat_num(value_str):
    """
    Parses strings like '1.23m' or '1,000.50' back into floats.
    """
    if not value_str:
        return 0.0
    
    value_str = str(value_str).strip().lower()

    # Handle Scientific
    if 'e' in value_str:
        try: return float(value_str)
        except ValueError: pass

    # Handle Abbreviated suffixes
    for i, suffix in enumerate(SUFFIXES[1:], 1):
        if suffix and value_str.endswith(suffix):
            try:
                num_part = value_str[:-len(suffix)]
                return float(num_part) * (1000 ** i)
            except ValueError: pass

    # Handle standard locale (strip everything but numbers, dots, and minus)
    try:
        clean_str = re.sub(r'[^\d.-]', '', value_str)
        return float(clean_str)
    except ValueError:
        return 0.0

# ------------------------------------------------------------------------
# Coordinate & Grid Parsing
# ------------------------------------------------------------------------

def parse_coords(coord_str):
    """
    Converts '1,2' into [1, 2] OR '1,1,3,3' into [1, 1, 3, 3].
    Returns an empty list if no numbers are found.
    """
    if not coord_str:
        return []
    try:
        # Find all numbers (including negatives) in the string
        nums = [int(n) for n in re.findall(r'-?\d+', coord_str)]
        return nums
    except Exception:
        return []

def parse_dimensions(dim_str):
    """Converts '10x10' or '10,10' into [10, 10]."""
    res = parse_coords(dim_str)
    return res[:2] if len(res) >= 2 else [0, 0]

def parse_numrange(min_val, max_val):
    """
    Converts two inputs into a PostgreSQL-compatible Range object.
    Handles 'None' or empty strings as infinite bounds.
    """
    try:
        # If input is empty, treat as None (Postgres Infinity)
        l_bound = float(min_val) if (min_val is not None and str(min_val).strip() != "") else None
        u_bound = float(max_val) if (max_val is not None and str(max_val).strip() != "") else None
        
        # [lower, upper) - inclusive lower, exclusive upper is standard
        return Range(l_bound, u_bound, bounds='[]') 
    except (ValueError, TypeError):
        return Range(None, None) # Default to unbounded

# ------------------------------------------------------------------------
# Discovery
# ------------------------------------------------------------------------

def mask_string(s):
    """Replaces non-space characters with bullets."""
    return ''.join('•' if c != ' ' else ' ' for c in s)

# ------------------------------------------------------------------------
# HTML Sanitization Filter
# ------------------------------------------------------------------------

def htmlify_filter(html):
    """
    Converts newline to <br> and handles custom <c=color> tags safely.
    Uses Bleach for security.
    """
    if not html:
        return ""

    # Convert custom color tags: <c="red">text</c> -> <span style="color:red;">text</span>
    html = re.sub(
        r'<c\s*=\s*["\']?([^"\'>]+)["\']?\s*>',
        r'<span style="color:\1;">', html)
    html = re.sub(r'</c\s*>', r'</span>', html)

    # Sanitize
    css_sanitizer = CSSSanitizer(allowed_css_properties=['color', 'font-weight'])
    html = bleach.clean(
        html,
        tags={'a', 'b', 'i', 'span', 'pre', 'br', 'strong', 'em', 'u'},
        attributes={
            'a': ['href', 'title'],
            'span': ['style']
        },
        css_sanitizer=css_sanitizer
    )

    # Standardize links (ensure they are internal or safe)
    def sanitize_href(match):
        href = match.group(2).strip()
        if not re.match(r'^/[a-zA-Z0-9/=?&_]*$', href):
            return 'href="#"'
        return match.group(0)

    html = re.sub(r'href\s*=\s*(["\']?)([^>]+)', sanitize_href, html)
    
    # Final newlines to BR
    html = html.replace('\n', '<br>')
    
    return Markup(html)

# ------------------------------------------------------------------------
# Request Handling
# ------------------------------------------------------------------------

class RequestHelper:
    """
    Helper to safely extract typed values from request.form or request.args.
    """
    @staticmethod
    def get_int(key, default=0):
        val = request.values.get(key, '').strip()
        try:
            return int(unformat_num(val))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def get_float(key, default=0.0):
        val = request.values.get(key, '').strip()
        return unformat_num(val)

    @staticmethod
    def get_bool(key):
        val = request.values.get(key, '').lower()
        return val in ['true', 'on', '1', 'checked']

    @staticmethod
    def get_coords(key):
        val = request.values.get(key, '').strip()
        return parse_coords(val)

def parse_form_data(form_dict):
    """
    Converts flat form keys like 'stats[0][id]' into a nested structure.
    Returns a dictionary with lists where indices were present.
    """
    result = {}

    for key, value in form_dict.items():
        # Split 'stats[0][attrib_id]' into ['stats', '0', 'attrib_id']
        parts = re.findall(r'[^\[\]]+', key)
        
        curr = result
        for i, part in enumerate(parts):
            # Is this the last part? (The actual field name or value)
            if i == len(parts) - 1:
                curr[part] = value
            else:
                # Is the NEXT part a digit? If so, this part is a list/indexed dict
                next_part = parts[i+1]
                if next_part.isdigit():
                    if part not in curr:
                        curr[part] = {} # Use dict temporarily to handle indices
                    curr = curr[part]
                else:
                    if part not in curr:
                        curr[part] = {}
                    curr = curr[part]

    # Post-process: Convert dictionaries that have only numeric keys into sorted lists
    return _inflate_lists(result)

def _inflate_lists(node):
    if not isinstance(node, dict):
        return node
    
    # Recursively process children
    for key in node:
        node[key] = _inflate_lists(node[key])

    # If all keys are digits, convert this dict to a list
    if node and all(k.isdigit() for k in node.keys()):
        return [node[str(i)] for i in sorted(map(int, node.keys())) if str(i) in node]
    
    return node

def capture_origin(name=None):
    """
    Stores the current URL in the session as the 'origin' for the next page.
    - name: A friendly display name (e.g. 'Valerius' or 'Iron Ore')
    """
    session['origin_url'] = request.full_path
    session['origin_name'] = name or "Previous Page"

def redirect_back(default='play.overview'):
    """
    Redirects to the stored origin, or a default route.
    Clears the origin after use to prevent loops.
    """
    target = session.pop('origin_url', request.referrer)
    if not target or target == request.url:
        return redirect(url_for(default))
    return redirect(target)

# ------------------------------------------------------------------------
# Hotkey Generation
# ------------------------------------------------------------------------

class LinkLetters:
    """
    Generates unique letters for links to be used as hotkeys.

    Specify keys to exclude that are already used. You could also exclude
    keys that may be difficult to distinguish on certain pages (e.g. l vs 1).

    Caches links to ensure the same URL gets the same hotkey on one page.
    """
    def __init__(self, excluded='om'): # 'o' (overview) and 'm' (main setup)
        self.index = 0
        self.alphabet = [chr(i) for i in range(ord('a'), ord('z')+1) if chr(i) not in excluded]
        self.alphabet += [chr(i) for i in range(ord('A'), ord('Z')+1)] # Add capitals as fallback
        self.links = {} # URL -> Letter cache

    def next(self, link=None):
        """Returns the next available letter or the cached one for this link."""
        if link and link in self.links:
            return self.links[link]
            
        if self.index < len(self.alphabet):
            letter = self.alphabet[self.index]
            self.index += 1
            if link:
                self.links[link] = letter
            return letter
        return ""

# ------------------------------------------------------------------------
# JSON Condensing
# ------------------------------------------------------------------------

def condense_json(json_str):
    """
    Post-processes a JSON string to collapse coordinate-style arrays 
    onto a single line for better manual readability.
    """
    # 1. Collapse any 2-item arrays (e.g., [1, 2] or ["slot", "main"])
    # Patterns: [ value , value ]
    json_str = re.sub(
        r'\[\s*(-?[\d.]+|".*?")\s*,\s*(-?[\d.]+|".*?")\s*\]',
        lambda m: f'[{m.group(1)}, {m.group(2)}]',
        json_str
    )

    # 2. Specifically target spatial keys that might have more items (like 'excluded' with 4)
    tuple_keys = ["door1", "door2", "dimensions", "excluded", "position", "numeric_range"]
    key_pattern = "|".join(tuple_keys)
    pattern = rf'"({key_pattern})":\s*\[(.*?)\]'

    def collapse_spatial_data(match):
        key = match.group(1)
        # Remove all internal whitespace and newlines
        content = match.group(2).replace("\n", "").replace(" ", "")
        # Re-insert clean spacing: [1,2,3,4] -> [1, 2, 3, 4]
        formatted_content = ", ".join(content.split(","))
        return f'"{key}": [{formatted_content}]'

    return re.sub(pattern, collapse_spatial_data, json_str, flags=re.DOTALL)

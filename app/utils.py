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
    """Remove formatting from a string. Returns a float."""
    if not value_str or not isinstance(value_str, str):
        return 0.0
    value_str = value_str.strip()
    if value_str == '':
        return 0.0

    # Scientific notation "1.23e6"
    if re.match(r'^[-+]?\d+(\.\d+)?e[+-]?\d+$', value_str, re.IGNORECASE):
        try:
            return float(value_str)
        except ValueError:
            return 0.0

    # Abbreviated format like "1.23m"
    abbr_map = {
        suffix: 10 ** (3 * chunk)
        for chunk, suffix in enumerate(SUFFIXES[1:])}
    match = re.match(
        rf'^(\d+(\.\d+)?)([{ "".join(SUFFIXES[1:]) }])$', value_str)
    if match:
        number = float(match.group(1))
        suffix = match.group(3)
        return number * abbr_map[suffix]

    # Remove grouping characters like commas or periods
    try:
        normalized_value_str = re.sub(r'[^\d.-]', '', value_str)
        return float(normalized_value_str)
    except (ValueError, TypeError):
        return 0.0


# ------------------------------------------------------------------------
# Coordinate & Grid Parsing
# ------------------------------------------------------------------------

def parse_coords(coord_str, required_len=2):
    """
    For example '1x2' becomes (1, 2) and '1,1,3,3' becomes (1, 1, 3, 3).
    Returns None if required_len numbers are not found.
    """
    if not coord_str:
        return None
    try:
        # Find all numbers (including negatives) in the string
        nums = [int(n) for n in re.findall(r'-?\d+', coord_str)]
        if len(nums) == required_len:
            return tuple(nums)
        return None
    except (ValueError, TypeError):
        return None

def parse_numrange(min_val, max_val):
    """
    Converts two inputs into a PostgreSQL-compatible Range object.
    Handles 'None' or empty strings as infinite bounds.
    """
    try:
        # If input is empty, treat as None (Postgres Infinity)
        l_bound = float(min_val) if (
            min_val is not None and str(min_val).strip() != "") else None
        u_bound = float(max_val) if (
            max_val is not None and str(max_val).strip() != "") else None
        
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
    """Safely extracts typed values from request.form or request.args."""
    def __init__(self, source):
        if source not in ['form', 'args']:
            raise ValueError("Source must be 'form' or 'args'")
        self.source = source

    def _get_source(self):
        return getattr(request, self.source)

    def _get_from_request(self, key):
        return self._get_source().get(key, '')

    def get_str(self, key, default=''):
        """Retrieve a string from the request.
        No special checks are needed -- just a thin wrapper method.
        """
        return (self._get_from_request(key) or default).strip()

    def get_int(self, key, default=0):
        formatted_str = self._get_from_request(key)
        if formatted_str == '':
            return default
        float_value = unformat_num(formatted_str)
        try:
            return int(float_value)
        except (ValueError, TypeError):
            return default

    def get_float(self, key, default=0.0):
        """Retrieve a floating point value using unformat_num logic."""
        formatted_str = self._get_from_request(key)
        if formatted_str == '':
            return default
        return unformat_num(formatted_str)

    def get_bool(self, key, default=False):
        """Retrieve a boolean value from the request."""
        value_str = self._get_from_request(key)
        if value_str == '':
            return default

        normalized = value_str.lower().strip()
        if normalized in ('true', '1', 't', 'y', 'yes', 'on'):
            return True
        if normalized in ('false', '0', 'f', 'n', 'no', 'off'):
            return False
            
        return default

    def get_coords(self, key):
        val = self._get_from_request(key)
        return parse_coords(val)

    def get_list(self, key_text):
        """
        Extracts nested form data starting with 'key_text[' and returns a list.

        For example:
        {
            "stats[0][id]": "1",
            "stats[0][val]": "A",
            "stats[1][id]": "2"
        }
        ...is returned as:
        [
            {"id": "1", "val": "A"},
            {"id": "2"}
        ]
        Keys that do not fit this structure are discarded.

        If the form only sends a single value per row, then use the 
        standard Flask `request.form.getlist('item_refs[]')` instead.
        """
        temp_root = {}

        form_dict = self._get_source()
        for key, value in form_dict.items():
            if key.startswith(f"{key_text}["):
                # Split 'stats[0][id]' into ['stats', '0', 'id']
                parts = re.findall(r'[^\[\]]+', key)
                
                curr = temp_root
                for i, part in enumerate(parts):
                    if i == len(parts) - 1:
                        curr[part] = value
                    else:
                        if part not in curr:
                            curr[part] = {}
                        curr = curr[part]

        target_data = temp_root.get(key_text, {})
        result = _inflate_lists(target_data)
        if isinstance(result, list):
            return result
        return []

def _inflate_lists(node):
    """Recursively converts dictionaries with numeric keys into
    sorted Python lists.
    """
    # If it's not a dict, it's a leaf value (string)
    if not isinstance(node, dict):
        return node
    
    # Process children first
    for key in node:
        node[key] = _inflate_lists(node[key])

    # If any key is a digit, this level is intended to be a list
    if node and any(k.isdigit() for k in node.keys()):
        digit_keys = [k for k in node.keys() if k.isdigit()]
        sorted_indices = sorted(map(int, digit_keys))
        # Build the list. If indices are 0, 2, 4, this creates a list of 3 items.
        return [node[str(i)] for i in sorted_indices]
    
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

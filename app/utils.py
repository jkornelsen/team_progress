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
        val = self._get_from_request(key)
        try:
            return int(float(unformat_num(val)))
        except (ValueError, TypeError):
            return default

    def get_float(self, key, default=0.0):
        """Retrieve a floating point value using unformat_num logic."""
        formatted_str = self._get_from_request(key)
        value_str = unformat_num(formatted_str)
        if formatted_str == '' or value_str == '':
            return default
        try:
            return float(value_str)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key, default=False):
        """Retrieve a boolean value from the request."""
        value_str = self._get_from_request(key)
        if value_str == '':
            return default
        try:
            return bool(value_str)
        except ValueError:
            return default

    def get_coords(self, key):
        val = self._get_from_request(key)
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

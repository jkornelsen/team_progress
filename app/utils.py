import markdown
import locale
import logging
import re
from flask import g, session, request, url_for, redirect
from markupsafe import Markup
import bleach
from bleach.css_sanitizer import CSSSanitizer
from .models import GENERAL_ID

logger = logging.getLogger(__name__)

BIGNUM_SUFFIXES = [
    '', 'k', 'm', 'b', 't', 'q', 'Q', 's', 'S', 'o', 'n', 'd',
    'u', 'U', 'v', 'V', 'w', 'W', 'x', 'X', 'y', 'Y', 'z', 'Z'
]

# ------------------------------------------------------------------------
# Request Handling
# ------------------------------------------------------------------------

class BaseFieldMap:
    """Safely extracts typed values from a dict."""
    def __init__(self, data):
        self.data = data or {}

    def __contains__(self, key):
        """Support 'key' in basefieldmap."""
        return key in self.data

    def __iter__(self):
        """Support 'for key in basefieldmap'."""
        return iter(self.data)

    def keys(self):
        return self.data.keys()

    def _get_raw(self, key):
        return self.data.get(key, '')

    def get_str(self, key, default=''):
        """Retrieve a string from the request.
        No special checks are needed -- just a thin wrapper method.
        """
        return (self._get_raw(key) or default).strip()

    def get_int(self, key, default=0):
        formatted_str = self._get_raw(key)
        if formatted_str == '':
            return default
        float_value = unformat_num(formatted_str)
        try:
            return int(float_value)
        except (ValueError, TypeError):
            return default

    def get_float(self, key, default=0.0):
        """Retrieve a floating point value using unformat_num logic."""
        formatted_str = self._get_raw(key)
        if formatted_str == '':
            return default
        return unformat_num(formatted_str)

    def get_bool(self, key, default=False):
        """Retrieve a boolean value from the request."""
        value_str = self._get_raw(key)
        if value_str == '':
            return default

        normalized = value_str.lower().strip()
        if normalized in ('true', '1', 't', 'y', 'yes', 'on'):
            return True
        if normalized in ('false', '0', 'f', 'n', 'no', 'off'):
            return False
            
        return default

    def get_coords(self, key):
        val = self._get_raw(key)
        return parse_coords(val)

    def get_list(self, key_text):
        """
        Extracts nested data and returns a list of BaseFieldMap objects.
        This allows for recursive calls: row.get_list('sub_items')
        """
        target_data = self.data.get(key_text, [])

        if isinstance(target_data, dict):
            target_data = _inflate_lists(target_data)

        if not isinstance(target_data, list):
            return []

        return [_wrap_request_data(item) for item in target_data]

class RequestHelper(BaseFieldMap):
    """Extracts values from request.form or request.args."""
    def __init__(self, source_type):
        if source_type not in ['form', 'args']:
            raise ValueError("Source type must be 'form' or 'args'")
        source = getattr(request, source_type)
        data = source.to_dict() 
        super().__init__(data)

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

        for key, value in self.data.items():
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
        raw_list = _inflate_lists(target_data)
        if isinstance(raw_list, list):
            return [_wrap_request_data(row) for row in raw_list]
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

def _wrap_request_data(node):
    if isinstance(node, dict):
        return BaseFieldMap({k: _wrap_request_data(v) for k, v in node.items()})
    if isinstance(node, list):
        return [_wrap_request_data(v) for v in node]
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
# Number Formatting
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
        while abs_val >= 1000 and chunk < len(BIGNUM_SUFFIXES) - 1:
            abs_val /= 1000.0
            chunk += 1
        # Use 2 decimal places for abbreviated chunks
        return f"{abs_val if value >=0 else -abs_val:.2f}{BIGNUM_SUFFIXES[chunk]}"

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
        for chunk, suffix in enumerate(BIGNUM_SUFFIXES[1:])}
    match = re.match(
        rf'^(\d+(\.\d+)?)([{ "".join(BIGNUM_SUFFIXES[1:]) }])$', value_str)
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

def mask_string(s):
    """Replaces letters and numbers with bullets."""
    if not s:
        return ""
    allowed = set(" -_.,:;()[]{}!@#$%^&*+=<>?/ \t\n\r")
    return ''.join(c if c in allowed else '•' for c in s)

def maskable_name(entity):
    if getattr(entity, 'masked', None):
        return mask_string(entity.name)
    return entity.name


# ------------------------------------------------------------------------
# IDs Logic
# ------------------------------------------------------------------------

class ContextIds:
    def __init__(self, owner_id=None, char_id=None, loc_id=None, host_id=None):
        self.owner_id = owner_id
        self.char_id = char_id
        self.loc_id = loc_id
        self.host_id = host_id

    def clone(self, **overrides):
        params = {
            'owner_id': self.owner_id,
            'char_id': self.char_id,
            'loc_id': self.loc_id,
            'host_id': self.host_id
        }
        params.update(overrides)
        return ContextIds(**params)

    @property
    def addl_char_id(self):
        """Is the char id additional info besides owner id."""
        return self.char_id and self.char_id != self.owner_id

    @property
    def addl_loc_id(self):
        """Is the loc id additional info besides owner id."""
        return self.loc_id and self.loc_id != self.owner_id

    @property
    def best_char_id(self):
        """
        Priority order for character identity:
        1. Specific character provided (char_id)
        2. The host of the current action (host_id)
        3. The owner of the context (owner_id)
        This is only a guess because we don't check if host or owner are chars.
        """
        return self.char_id or self.host_id or self.owner_id

    def get_params(self):
        """Returns a dict of non-redundant IDs useful for unpacking
        in url_for: **ctx_ids.get_params()
        """
        params = {}
        if self.addl_char_id:
            params['char_id'] = self.char_id
        if self.addl_loc_id:
            params['loc_id'] = self.loc_id
        return params

    @staticmethod
    def not_general(id):
        return id if id and id != GENERAL_ID else None

    @staticmethod
    def unique_ids(*values):
        """Return list of unique, non-falsy IDs, preserving order."""
        seen = set()
        result = []
        for v in values:
            if v and v not in seen:
                seen.add(v)
                result.append(v)
        return result

# ------------------------------------------------------------------------
# HTML Sanitization Filter
# ------------------------------------------------------------------------

ALLOWED_DESC_VARS = {'char_id', 'loc_id', 'subject_id', 'owner_id', 'host_id'}

def htmlify_filter(text):
    """
    Converts newline to <br> and handles custom <c=color> tags safely.
    Uses Bleach for security.
    """
    if not text: return ""

    # 1. Variable Substitution: ${subject_id} -> value from current URL
    def sub_vars(match):
        var_name = match.group(1)
        if var_name in ALLOWED_DESC_VARS:
            return request.args.get(var_name, "")
        return match.group(0)

    text = re.sub(r'\$\{([^}]+)\}', sub_vars, text)

    # 2. Convert Markdown (Standard Links: [Text](/url))
    html = markdown.markdown(text, extensions=['extra', 'nl2br'])

    # 3. Modern Color Syntax: {color|content}
    def color_replacer(match):
        color = match.group(1)
        content = match.group(2)
        if '<code>' in content or '<pre>' in content or '\n' in content:
            return f'<div style="color:{color}">{content}</div>'
        return f'<span style="color:{color}">{content}</span>'

    # Keep replacing until no more {color|content} patterns are found.
    # This handles nesting.
    color_pattern = r'\{([\w#]+)\|([^{}]*)\}'
    while re.search(color_pattern, html):
        html = re.sub(color_pattern, color_replacer, html)

    # 4. Security: Force links to be internal-only
    # This prevents links with external protocols such as http://bad-site.com
    def sanitize_href(match):
        href = match.group(2).strip().rstrip('"').rstrip("'")
        if not href.startswith('/'):
            href = '/' + href
        if not re.match(r'^/[a-zA-Z0-9/=?&_.-]*$', href):
            logger.warning(f"Preventing link to '{href}'")
            return 'href="#"'
        return match.group(0)

    html = re.sub(r'href\s*=\s*(["\']?)([^>]+)', sanitize_href, html)

    # 5 Remove empty paragraphs generated by Markdown's block-ejection
    html = html.replace('<p></p>', '')
    html = re.sub(r'<p>\s*</p>', '', html)

    # 6. Sanitize with Bleach
    css_sanitizer = CSSSanitizer(allowed_css_properties=['color'])
    allowed_tags = {
        'a', 'b', 'i', 'span', 'div', 'pre', 'code', 'br', 'strong', 'em',
        'u', 'p', 'ul', 'ol', 'li', 'h1', 'h2', 'h3'}
    allowed_attrs = {
        'a': ['href', 'title'],
        'span': ['style', 'class'], 
        'div': ['style'], 
        'code': ['class']
    }
    clean_html = bleach.clean(
        html, 
        tags=allowed_tags, 
        attributes=allowed_attrs,
        css_sanitizer=css_sanitizer
    )

    return Markup(clean_html)

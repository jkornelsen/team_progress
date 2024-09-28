"""
Example commands:
npm install eslint-define-config --save-dev
npm install @eslint/js --save-dev

python eslint_prepare.py
npx eslint output_js/**/*.js
gvim (gci -Path "output_js" -filter "*.js" -recurse).fullname
"""

import os
import re
from bs4 import BeautifulSoup

def extract_js_from_html(input_dir, output_dir):
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Iterate over files in the input directory
    for root, dirs, files in os.walk(input_dir):
        for file in files:
            if file.endswith(".html"):
                input_file_path = os.path.join(root, file)
                relative_path = os.path.relpath(root, input_dir)

                # Output .js file with same name as the .html file
                js_file_name = os.path.splitext(file)[0] + ".js"
                output_file_dir = os.path.join(output_dir, relative_path)
                os.makedirs(output_file_dir, exist_ok=True)
                output_file_path = os.path.join(output_file_dir, js_file_name)

                # Read and process the HTML file
                with open(input_file_path, 'r', encoding='utf-8') as html_file:
                    html_content = html_file.read()

                # Parse HTML with BeautifulSoup
                soup = BeautifulSoup(html_content, 'html.parser')

                # Extract JavaScript code from <script> tags
                js_code = []
                for script_tag in soup.find_all('script'):
                    if script_tag.string:
                        js_code.append(script_tag.string)

                # Combine all extracted JS code
                combined_js_code = "\n".join(js_code)

                # Replace Jinja tags with safe defaults
                cleaned_js_code = replace_jinja_with_defaults(combined_js_code)

                # Write the cleaned JS code to the new .js file
                with open(output_file_path, 'w', encoding='utf-8') as js_file:
                    js_file.write(cleaned_js_code)

                print(f"Processed: {input_file_path} -> {output_file_path}")

# Function to replace Jinja tags with default valid JS values
def replace_jinja_with_defaults(js_code):
    # Replace {{ ... }} with appropriate JS values
    def inline_replacer(match):
        jinja_tag = match.group(0)
        # Check if the Jinja tag is inside quotes
        start_pos = match.start()
        end_pos = match.end()
        if (start_pos > 0 and js_code[start_pos - 1] == '"' and 
                end_pos < len(js_code) and js_code[end_pos] == '"'):
            return ''  # Keep empty string if already in quotes
        else:
            return '0'  # Replace with 0 if it's a number or variable

    # Replace variables inside {{ ... }} with appropriate defaults
    js_code = re.sub(r'{{.*?}}', inline_replacer, js_code)

    # Remove Jinja block for tags
    #js_code = re.sub(r'{%\s*for.*?%}.*?{%\s*endfor\s*%}', '', js_code, flags=re.DOTALL)
    js_code = re.sub(r'{%\s*for.*?%}', '', js_code, flags=re.DOTALL)
    js_code = re.sub(r'{%\s*endfor\s*%}', '', js_code, flags=re.DOTALL)

    # Replace other Jinja block tags {% ... %} with an empty statement ;
    js_code = re.sub(r'{%.*?%}', ';', js_code, flags=re.DOTALL)

    return js_code

if __name__ == "__main__":
    input_templates_dir = "../app/templates"  # Path to the templates directory
    output_js_dir = "./output_js"             # Directory to save extracted JS files
    extract_js_from_html(input_templates_dir, output_js_dir)

#
# Bundle code for importing into AI.
#
import os
import re
import sys

FILE_PATH_PATTERN = re.compile(r'[\w\-]+(?:[/\\][\w\-]+)*\.[\w]+')
ignored_folders = {
    'venv', '.venv', '.git', '.vscode', '__pycache__', 'postgres_data',
    'dev', 'history'}

def bundle_files(start_dir="..", output_file="bundled_code.md"):
    extensions = ('.py', '.html', '.js', '.css')
    
    abs_path = os.path.abspath(start_dir)
    root_folder_name = os.path.basename(abs_path.rstrip(os.sep))
    output_filename = os.path.basename(output_file)
    
    with open(output_file, 'w', encoding='utf-8') as f_out:
        f_out.write(f"# Project Context Bundled\n")
        f_out.write(f"Root Folder: {root_folder_name}\n\n")
        
        for root, dirs, files in os.walk(start_dir):
            dirs[:] = [d for d in dirs if d not in ignored_folders and not d.startswith('.')]
            
            for file in files:
                if file == output_filename:
                    continue
                    
                if file.endswith(extensions):
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, start_dir)
                    full_display_path = os.path.join(root_folder_name, rel_path).replace("\\", "/")
                    
                    print(f"Adding: {full_display_path}")
                    write_file_block(f_out, file_path, full_display_path)

    print(f"\nDone! Bundle created for: {root_folder_name}")


def bundle_json(start_dir="..", output_file="bundled_json.md"):
    extensions = ('.json',)
    
    abs_path = os.path.abspath(start_dir)
    root_folder_name = os.path.basename(abs_path.rstrip(os.sep))
    output_filename = os.path.basename(output_file)
    
    with open(output_file, 'w', encoding='utf-8') as f_out:
        f_out.write(f"# Project Context Bundled\n")
        f_out.write(f"Root Folder: {root_folder_name}\n\n")
        
        for root, dirs, files in os.walk(start_dir):
            dirs[:] = [d for d in dirs if d not in ignored_folders and not d.startswith('.')]
            
            for file in files:
                if file == output_filename:
                    continue
                    
                if file.endswith(extensions):
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, start_dir)
                    full_display_path = os.path.join(root_folder_name, rel_path).replace("\\", "/")
                    
                    print(f"Adding: {full_display_path}")
                    write_file_block(f_out, file_path, full_display_path)

    print(f"\nDone! Bundle created for: {root_folder_name}")


def bundle_file_list(start_dir="..", output_file="bundled_selected.md"):
    abs_path = os.path.abspath(start_dir)
    root_folder_name = os.path.basename(abs_path.rstrip(os.sep))

    print("Paste your file list (relative paths from the project root),")
    print("then press Ctrl+Z and Enter (Windows) or Ctrl+D (Mac/Linux) to finish:\n")
    raw_input = sys.stdin.read()

    # Match partial file paths: segments of word chars/hyphens separated by slashes, with an extension
    matches = FILE_PATH_PATTERN.findall(raw_input)

    if not matches:
        print("No valid file paths found in input.")
        return

    print(f"\nFound {len(matches)} file(s):")
    for m in matches:
        print(f"  {m}")

    with open(output_file, 'w', encoding='utf-8') as f_out:
        f_out.write(f"# Project Context Bundled\n")
        f_out.write(f"Root Folder: {root_folder_name}\n\n")

        for rel_path in matches:
            # Normalize slashes for the OS
            norm = rel_path.replace("\\", os.sep).replace("/", os.sep)

            # If the path starts with the root folder name, strip it so we
            # don't double-up (e.g. "myproject/src/foo.py" -> "src/foo.py")
            parts = norm.split(os.sep)
            if parts[0] == root_folder_name:
                norm = os.sep.join(parts[1:])

            file_path = os.path.join(start_dir, norm)
            full_display_path = root_folder_name + "/" + norm.replace(os.sep, "/")

            if not os.path.isfile(file_path):
                print(f"  WARNING: Not found, skipping: {file_path}")
                continue

            print(f"Adding: {full_display_path}")
            write_file_block(f_out, file_path, full_display_path)

    print(f"\nDone! Bundle created for: {root_folder_name}")


def write_file_block(f_out, file_path, display_path):
    ext = os.path.splitext(file_path)[1]
    lang_map = {'.py': 'python', '.js': 'javascript', '.css': 'css',
                '.html': 'html', '.json': 'json'}
    lang = lang_map.get(ext, '')

    f_out.write(f"## File: {display_path}\n")
    f_out.write(f"```{lang}\n")
    try:
        with open(file_path, 'r', encoding='utf-8') as f_in:
            f_out.write(f_in.read().strip())
    except Exception as e:
        f_out.write(f"# Error reading file: {e}")
    f_out.write("\n```\n\n---\n\n")


if __name__ == "__main__":
    print("What would you like to bundle?")
    print("  a) Code files (.py, .html, .js, .css)")
    print("  b) JSON files (.json)")
    print("  c) Specific file list")
    choice = input("\nChoice (a/b/c): ").strip().lower()

    if choice == 'a':
        bundle_files()
    elif choice == 'b':
        bundle_json()
    elif choice == 'c':
        bundle_file_list()
    else:
        print("Invalid choice.")

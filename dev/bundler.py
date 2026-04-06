#
# Bundle code for importing into AI.
#
import os

def bundle_files(start_dir="..", output_file="bundled.md"):
    extensions = ('.py', '.html')
    #extensions = ('.json')  # data files
    ignored_folders = {
        'venv', '.venv', '.git', '.vscode', '__pycache__', 'postgres_data',
        'dev', 'history'}
    
    # Get the clean name of the root folder
    abs_path = os.path.abspath(start_dir)
    root_folder_name = os.path.basename(abs_path.rstrip(os.sep))
    
    output_filename = os.path.basename(output_file)
    
    with open(output_file, 'w', encoding='utf-8') as f_out:
        f_out.write(f"# Project Context Bundled\n")
        f_out.write(f"Root Folder: {root_folder_name}\n\n")
        
        for root, dirs, files in os.walk(start_dir):
            # Skip ignored folders in-place
            dirs[:] = [d for d in dirs if d not in ignored_folders and not d.startswith('.')]
            
            for file in files:
                if file == output_filename:
                    continue
                    
                if file.endswith(extensions):
                    file_path = os.path.join(root, file)
                    
                    # Create the full path starting from the root folder name
                    # e.g., "MyProject/src/main.py"
                    rel_path = os.path.relpath(file_path, start_dir)
                    full_display_path = os.path.join(root_folder_name, rel_path).replace("\\", "/")
                    
                    print(f"Adding: {full_display_path}")
                    
                    lang = "python" if file.endswith(".py") else "html"
                    
                    f_out.write(f"## File: {full_display_path}\n")
                    f_out.write(f"```{lang}\n")
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f_in:
                            f_out.write(f_in.read().strip())
                    except Exception as e:
                        f_out.write(f"# Error reading file: {e}")
                        
                    f_out.write("\n```\n\n---\n\n")

    print(f"\nDone! Bundle created for: {root_folder_name}")

if __name__ == "__main__":
    bundle_files()

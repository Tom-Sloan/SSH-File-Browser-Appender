# SSH File Browser & Appender

A GUI tool to browse remote files via SSH/SFTP, select multiple files, and append their contents into a single text block. Ideal for situations where you need to combine file contents for AI analysis or documentation.

This tool was created to help with the process of combining multiple files into something I could paste into an AI chat when they won't let me attach the files.

## Features

-   📁 Connect to SSH servers with password authentication
-   🌳 Browse remote directories with expandable tree view
-   🔍 Search files with auto-complete suggestions
-   📝 Select multiple files to append their contents
-   📋 Copy combined text to clipboard
-   ⏱️ Toast-style notifications for user feedback
-   📌 Save/load recently used paths
-   🖼️ Custom folder/file icons with colorization
-   🖱️ Drag-friendly window with resizable components

## Installation

1. Clone this repository
2. Ensure you have [Miniconda](https://docs.conda.io/en/latest/miniconda.html) installed
3. Create and activate the environment:

```bash
conda env create -f enviroment.yml
conda activate append_file_gui
```

4. Run the application:

```bash
python append_file_gui.py
```

## Default Settings

The default settings are stored in the `.env` file. If you want to change the default settings, you can edit the `.env` file.

DEFAULT_HOST=123.456.789.101
DEFAULT_USER=user
DEFAULT_BASE_DIR=/home/user/project/

## Usage

1. **Connect to Server**

    - Enter host, username, password
    - Set base directory (e.g., `/home/user/project/`)
    - Click "Connect & Load"

2. **Browse Files**

    - Expand folders in the tree view
    - Click files to add them to selection
    - Use search bar for quick file finding (supports partial matches)

3. **Manage Selection**

    - Add files: Double-click or press Enter
    - Remove files: Select in listbox → "Remove Selected"
    - Add all files in directory: "Add All" button

4. **Append Files**
    - Click "Fetch & Append" to combine selected files
    - Results appear in right panel
    - Use "Copy to Clipboard" for easy pasting

**Keyboard Shortcuts**

-   `Enter` in search: Add current text to selection
-   `↑/↓` arrows: Navigate suggestions
-   `Enter` on suggestion: Add selected file

## Notes

-   Recent paths are automatically saved in `recents.json`
-   The tool preserves original file formatting and newlines
-   Toast notifications appear in top-right corner
-   Tested on macOS - may need adjustments for Windows/Linux icons
-   Requires SFTP access on the remote server

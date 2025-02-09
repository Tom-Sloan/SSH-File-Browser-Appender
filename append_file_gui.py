#!/usr/bin/env python3
import os
import io
import json
import base64
import paramiko
import PySimpleGUI as sg
from PIL import Image
import time
import stat  # for local file mode operations
import tiktoken  # NEW: for token counting

# ----------------------------------------------------------------
# Constants / Config
# ----------------------------------------------------------------
RECENTS_FILE      = "recents.json"            # local file to store recently used paths
FOLDER_ICON_PATH  = "./icons/folder.png"      # path to your blackish folder icon
FILE_ICON_PATH    = "./icons/file.png"        # path to your file icon
TOAST_DURATION    = 5                         # how many seconds toast stays up
TOAST_WIDTH       = 250
TOAST_HEIGHT      = 80

# Default values (will be overridden by .env if it exists)
DEFAULT_HOST = ""
DEFAULT_USER = ""
# We now support separate default base directories for remote and local modes.
DEFAULT_REMOTE_BASE_DIR = ""
DEFAULT_LOCAL_BASE_DIR  = ""
try:
    from dotenv import load_dotenv
    load_dotenv()
    DEFAULT_HOST = os.getenv('DEFAULT_HOST', DEFAULT_HOST)
    DEFAULT_USER = os.getenv('DEFAULT_USER', DEFAULT_USER)
    DEFAULT_REMOTE_BASE_DIR = os.getenv('DEFAULT_REMOTE_BASE_DIR', "")
    DEFAULT_LOCAL_BASE_DIR  = os.getenv('DEFAULT_LOCAL_BASE_DIR', "")
except ImportError:
    pass  # python-dotenv not installed, will use empty defaults

# For startup, if remote mode is default then use remote base, else local.
# We set Remote as the default connection mode.
DEFAULT_BASE_DIR = DEFAULT_REMOTE_BASE_DIR

# A "folder yellow" color (e.g. Windows folder style)
FOLDER_YELLOW = (255, 201, 14)

# ----------------------------------------------------------------
# Icons: load & transform
# ----------------------------------------------------------------
def load_and_scale_folder_icon(path, size=(16,16)):
    """
    Load folder.png, resize, and colorize blackish pixels => folder yellow.
    """
    with Image.open(path).convert('RGBA') as img:
        img = img.resize(size, Image.LANCZOS)
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = px[x,y]
                # If pixel is blackish
                if r < 50 and g < 50 and b < 50 and a > 0:
                    px[x,y] = (FOLDER_YELLOW[0], FOLDER_YELLOW[1], FOLDER_YELLOW[2], a)
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        data = bio.getvalue()
    return base64.b64encode(data)

def load_and_scale_file_icon(path, size=(16,16)):
    """Load file.png, resize, and return base64-encoded PNG (no color transform)."""
    with Image.open(path).convert('RGBA') as img:
        img = img.resize(size, Image.LANCZOS)
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        data = bio.getvalue()
    return base64.b64encode(data)

SMALL_FOLDER_ICON = load_and_scale_folder_icon(FOLDER_ICON_PATH, size=(16,16))
SMALL_FILE_ICON   = load_and_scale_file_icon(FILE_ICON_PATH,     size=(16,16))

# ----------------------------------------------------------------
# Helpers for recents, toast messages, Paramiko
# ----------------------------------------------------------------
def load_recents():
    if not os.path.exists(RECENTS_FILE):
        return []
    try:
        with open(RECENTS_FILE, 'r') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_recents(recent_list):
    try:
        with open(RECENTS_FILE, 'w') as f:
            json.dump(recent_list, f, indent=2)
    except Exception as e:
        print(f"[!] Could not save recents: {e}")

def show_toast(message, keep_on_top=True, duration=TOAST_DURATION):
    """
    Show a small non-blocking toast in the top-right corner that auto-closes
    after `duration` seconds or when user clicks 'X'.
    """
    screen_w, screen_h = sg.Window.get_screen_size()
    x = screen_w - TOAST_WIDTH - 10
    y = 10

    layout = [
        [sg.Text(message, key='-MSG-', pad=(10,5), auto_size_text=True)],
        [sg.Push(), sg.Button("X", key='-CLOSE-', size=(2,1))]
    ]
    toast = sg.Window("", layout,
                      no_titlebar=True,
                      keep_on_top=keep_on_top,
                      location=(x,y),
                      finalize=True,
                      modal=False,
                      element_padding=(0,0),
                      size=(TOAST_WIDTH, TOAST_HEIGHT))

    start = time.time()
    while True:
        ev, vals = toast.read(timeout=100)
        if ev in (sg.WIN_CLOSED, '-CLOSE-'):
            break
        if (time.time() - start) > duration:
            break
    toast.close()

def get_sftp_connection(host, user, pwd):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=host, username=user, password=pwd)
    return ssh, ssh.open_sftp()

# ----------------------------------------------------------------
# Directory / Tree
# ----------------------------------------------------------------
def is_dir_attr(st_mode):
    return bool(st_mode & 0o040000)  # 0o040000 is the directory bit

def join_sftp_path(parent, child):
    if parent == "/":
        return f"/{child}"
    else:
        return parent.rstrip('/') + '/' + child

def has_subitems(sftp, path):
    """Check if path is a directory with at least 1 item."""
    try:
        entries = sftp.listdir_attr(path)
        return len(entries) > 0
    except:
        return False

def remove_node(tree_data, key):
    """Remove a node and its children from the TreeData if it exists."""
    if key not in tree_data.tree_dict:
        return
    node = tree_data.tree_dict[key]
    parent_id = node.parent
    if parent_id in tree_data.tree_dict:
        parent_node = tree_data.tree_dict[parent_id]
        if key in parent_node.children:
            parent_node.children.remove(key)
    for child_key in list(node.children):
        remove_node(tree_data, child_key)
    del tree_data.tree_dict[key]

def add_folder_node(tree_data, parent, key, text, is_expanded=False):
    tree_data.Insert(parent, key, text, values=[is_expanded], icon=SMALL_FOLDER_ICON)

def add_file_node(tree_data, parent, key, text):
    tree_data.Insert(parent, key, text, values=[], icon=SMALL_FILE_ICON)

def expand_ancestors_recursively(tree_element, node_key):
    try:
        tree_element.Widget.item(node_key, open=True)
        parent = tree_element.TreeData.tree_dict[node_key].parent
        if parent:
            expand_ancestors_recursively(tree_element, parent)
    except:
        pass

# --- NEW: local filesystem directory listing helper ---
def local_listdir_attr(path):
    """
    List directory contents on the local filesystem.
    Returns a list of simple objects with attributes:
      - filename: the entry’s name
      - st_mode: the result of os.lstat(...).st_mode
    """
    entries = []
    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        st = os.lstat(full_path)
        entry_obj = type('LocalEntry', (object,), {})()
        entry_obj.filename = entry
        entry_obj.st_mode = st.st_mode
        entries.append(entry_obj)
    return entries

# --- MODIFIED: populate_tree_level now takes an extra parameter "local_mode" ---
def populate_tree_level(file_client, tree_element, folder_key, all_files, local_mode):
    """
    Expand a directory if not expanded yet. Remove dummy, list contents, add discovered files to `all_files`.
    The parameter `file_client` is used for remote (sftp) mode.
    """
    tree_data = tree_element.TreeData
    if folder_key not in tree_data.tree_dict:
        return

    node_obj = tree_data.tree_dict[folder_key]
    if node_obj.values and node_obj.values[0] is True:
        return  # Already expanded

    # Mark expanded
    node_obj.values[0] = True

    # Remove the dummy child node if it exists
    dummy_key = f"_DUMMY_{folder_key}"
    remove_node(tree_data, dummy_key)

    # List directory contents using remote or local methods
    try:
        if local_mode:
            entries = local_listdir_attr(folder_key)
        else:
            entries = file_client.listdir_attr(folder_key)
    except Exception as e:
        show_toast(f"Cannot list {folder_key}: {e}")
        return

    # Sort directories first, then files.
    entries_sorted = sorted(
        entries,
        key=lambda e: (not (os.path.isdir(os.path.join(folder_key, e.filename)) if local_mode else is_dir_attr(e.st_mode)), e.filename.lower())
    )
    for entry in entries_sorted:
        name = entry.filename
        if local_mode:
            full_path = os.path.join(folder_key, name)
        else:
            full_path = join_sftp_path(folder_key, name)
        if full_path in tree_data.tree_dict:
            continue

        if local_mode:
            if os.path.isdir(full_path):
                add_folder_node(tree_data, folder_key, full_path, name, is_expanded=False)
            else:
                add_file_node(tree_data, folder_key, full_path, name)
                all_files.add(full_path)
        else:
            if is_dir_attr(entry.st_mode):
                add_folder_node(tree_data, folder_key, full_path, name, is_expanded=False)
            else:
                add_file_node(tree_data, folder_key, full_path, name)
                all_files.add(full_path)

    tree_element.update(tree_data)

def get_file_content_sftp(sftp, remote_path):
    """
    Retrieve the text content (with all newlines intact) from remote_path via sftp.
    """
    with sftp.open(remote_path, 'r') as f:
        return f.read()  # keep original formatting

# ----------------------------------------------------------------
# Path Shortening
# ----------------------------------------------------------------
def short_path(base_dir, full_path):
    """
    Return the portion of `full_path` after `base_dir`.
    e.g. base_dir=/home/sam3/abc, full_path=/home/sam3/abc/hello => /hello
    If full_path doesn't start with base_dir, return full_path as-is.
    """
    bd = base_dir.rstrip('/')
    if full_path.startswith(bd):
        remainder = full_path[len(bd):]
        if not remainder.startswith('/'):
            remainder = '/' + remainder
        return remainder
    else:
        return full_path

# ----------------------------------------------------------------
# Main GUI
# ----------------------------------------------------------------
def main():
    sg.theme('SystemDefault')
    sg.set_options(font=("Helvetica", 11), element_padding=(5,5))

    recents = load_recents()
    all_files = set()     # discovered file paths for search suggestions
    selected_files = []   # store full paths internally
    current_selected_dir = None
    local_mode = False  # Flag: True if using local file system

    # ---------------------------
    # Left Column: Connection Options
    # ---------------------------
    left_col = [
        # NEW: Radio buttons for connection mode.
        [sg.Text("Connection Mode:"), 
         sg.Radio('Remote', "MODE", key='-MODE_REMOTE-', default=True, enable_events=True),
         sg.Radio('Local', "MODE", key='-MODE_LOCAL-', enable_events=True)],
        [sg.Text("Host"), sg.Input(DEFAULT_HOST, key='-HOST-')],
        [sg.Text("User"), sg.Input(DEFAULT_USER, key='-USERNAME-')],
        [sg.Text("Pass"), sg.Input("", password_char='*', key='-PASSWORD-')],
        [sg.Text("Base Dir")],
        # Set default base dir based on Remote mode (default).
        [sg.Input(DEFAULT_REMOTE_BASE_DIR, key='-BASE_DIR-', size=(35,1))],
        [sg.Button("Connect & Load", key='-CONNECT-')],
        [sg.Text("Recents:")],
        [sg.Combo(values=[], size=(30,1), key='-RECENTS-'),
         sg.Button("Add Recents", key='-ADD_RECENT-')],
        [sg.Text("Selected Files:")],
        [sg.Listbox(values=[], size=(40,5), key='-SELECTED-', select_mode='extended')],
        [sg.Button("Remove Selected"), sg.Button("Clear All")],
        [sg.Button("Fetch & Append"), sg.Button("Exit", button_color=('white','firebrick4'))],
        [sg.Button("Add All (in Folder)", key='-ADD_ALL-', disabled=True)]
    ]

    # ---------------------------
    # Middle Column: Directory Tree & Search
    # ---------------------------
    tree_data = sg.TreeData()
    dir_tree = sg.Tree(
        data=tree_data,
        headings=[],
        auto_size_columns=True,
        row_height=25,
        col0_width=40,
        num_rows=20,
        key='-TREE-',
        show_expanded=True,
        enable_events=True
    )
    search_bar = sg.Input(key='-SEARCH-', enable_events=True, size=(40,1))
    suggestion_box = sg.Listbox(
        [],
        key='-SUGGESTIONS-',
        size=(40,4),
        visible=False,
        select_mode='single',
        enable_events=True,
        no_scrollbar=True
    )
    middle_col = [
        [sg.Text("Directory", font=("Helvetica", 12, "bold"))],
        [dir_tree],
        [sg.Text("Search:")],
        [search_bar],
        [suggestion_box]
    ]

    # ---------------------------
    # Right Column: Appended Text & Token Count
    # ---------------------------
    right_col = [
        [sg.Text("Appended Text:", font=("Helvetica", 12, "bold"))],
        [sg.Multiline("", size=(60, 20), key='-APPENDED-', autoscroll=True, horizontal_scroll=True)],
        # NEW: Token count display below the appended text.
        [sg.Text("Tokens: 0", key='-TOKEN_COUNT-')],
        [sg.Button("Copy to Clipboard", key='-COPY-'), sg.Button("Clear Text", key='-CLEAR_TEXT-')]
    ]

    layout = [
        [sg.Column(left_col, vertical_alignment='top'),
         sg.VerticalSeparator(color='grey'),
         sg.Column(middle_col, vertical_alignment='top'),
         sg.VerticalSeparator(color='grey'),
         sg.Column(right_col, vertical_alignment='top')]
    ]

    window = sg.Window("SSH/Local File Browser & Appender", layout, size=(1450, 700), resizable=True, return_keyboard_events=True)

    ssh = None
    sftp = None
    current_suggestions = []
    suggestion_index = -1

    # NEW: Helper to update token count using tiktoken.
    def update_token_count():
        try:
            text = window['-APPENDED-'].get()
            # Change "gpt-3.5-turbo" if you need a different model's tokenizer.
            encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
            token_count = len(encoding.encode(text))
            window['-TOKEN_COUNT-'].update(f"Tokens: {token_count}")
        except Exception as e:
            window['-TOKEN_COUNT-'].update("Tokens: Error")

    def update_recents_combo(base_dir):
        short_list = [short_path(base_dir, r) for r in recents]
        window['-RECENTS-'].update(values=short_list)

    def update_selected_listbox(base_dir):
        short_list = [short_path(base_dir, f) for f in selected_files]
        window['-SELECTED-'].update(values=short_list)

    def update_suggestions_box(base_dir, query):
        nonlocal current_suggestions, suggestion_index
        query = query.strip()
        if not query:
            current_suggestions = []
            window['-SUGGESTIONS-'].update(values=[], visible=False)
            suggestion_index = -1
            return

        ql = query.lower()
        matches_full = [f for f in all_files if ql in f.lower()]
        matches_full = matches_full[:8]
        current_suggestions = [(fp, short_path(base_dir, fp)) for fp in matches_full]
        suggestion_index = -1

        if current_suggestions:
            short_vals = [item[1] for item in current_suggestions]
            window['-SUGGESTIONS-'].update(values=short_vals, visible=True)
        else:
            window['-SUGGESTIONS-'].update(values=[], visible=False)

    def do_connect():
        nonlocal ssh, sftp, local_mode
        # Determine connection mode from radio buttons.
        if values['-MODE_LOCAL-']:
            local_mode = True
        else:
            local_mode = False

        base_dir = values['-BASE_DIR-'].strip()
        if local_mode:
            if not os.path.isdir(base_dir):
                show_toast("Invalid local directory!", duration=3)
                return
            ssh = None
            sftp = None
            new_tree = sg.TreeData()
            new_tree.Insert("", base_dir, base_dir, values=[False], icon=SMALL_FOLDER_ICON)
            all_files.clear()
            update_recents_combo(base_dir)
            window['-TREE-'].update(new_tree)
            window.refresh()
            show_toast("Loaded local directory successfully!", duration=3)
        else:
            host = values['-HOST-'].strip()
            user = values['-USERNAME-'].strip()
            pwd  = values['-PASSWORD-'].strip()
            if not host or not user or not pwd:
                show_toast("Please fill in Host, Username, and Password!", duration=3)
                return
            try:
                ssh_, sftp_ = get_sftp_connection(host, user, pwd)
                ssh, sftp = ssh_, sftp_
                new_tree = sg.TreeData()
                new_tree.Insert("", base_dir, base_dir, values=[False], icon=SMALL_FOLDER_ICON)
                all_files.clear()
                update_recents_combo(base_dir)
                window['-TREE-'].update(new_tree)
                window.refresh()
                show_toast("Connected Successfully!", duration=3)
            except Exception as e:
                show_toast(f"Error connecting: {e}", duration=5)

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, 'Exit'):
            break

        base_dir = values['-BASE_DIR-'].strip()

        # NEW: If the user switches connection mode, update the base dir accordingly.
        if event in ('-MODE_REMOTE-', '-MODE_LOCAL-'):
            if values['-MODE_REMOTE-']:
                window['-BASE_DIR-'].update(DEFAULT_REMOTE_BASE_DIR)
            else:
                window['-BASE_DIR-'].update(DEFAULT_LOCAL_BASE_DIR)

        if event == '-CONNECT-':
            do_connect()

        elif event == '-TREE-':
            if (not local_mode and not sftp):
                continue
            sel = values['-TREE-']
            if sel:
                node_key = sel[0]
                try:
                    if local_mode:
                        st_mode = os.lstat(node_key).st_mode
                        if os.path.isdir(node_key):
                            populate_tree_level(None, window['-TREE-'], node_key, all_files, local_mode)
                            expand_ancestors_recursively(window['-TREE-'], node_key)
                            window['-ADD_ALL-'].update(disabled=False)
                            current_selected_dir = node_key
                        else:
                            if node_key not in selected_files:
                                selected_files.append(node_key)
                            current_selected_dir = None
                            window['-ADD_ALL-'].update(disabled=True)
                    else:
                        st_mode = sftp.lstat(node_key).st_mode
                        if is_dir_attr(st_mode):
                            populate_tree_level(sftp, window['-TREE-'], node_key, all_files, local_mode)
                            expand_ancestors_recursively(window['-TREE-'], node_key)
                            window['-ADD_ALL-'].update(disabled=False)
                            current_selected_dir = node_key
                        else:
                            if node_key not in selected_files:
                                selected_files.append(node_key)
                            current_selected_dir = None
                            window['-ADD_ALL-'].update(disabled=True)
                    update_selected_listbox(base_dir)
                except Exception as ex:
                    show_toast(f"lstat error: {ex}")

        elif event == '-SEARCH-':
            query = values['-SEARCH-']
            update_suggestions_box(base_dir, query)

        elif event == '-SUGGESTIONS-':
            chosen_idx = window['-SUGGESTIONS-'].get_indexes()
            if chosen_idx:
                idx = chosen_idx[0]
                if 0 <= idx < len(current_suggestions):
                    full_p, short_p = current_suggestions[idx]
                    window['-SEARCH-'].update(short_p)
                    suggestion_index = idx

        elif event.startswith("Up") or event.startswith("Down") or event.startswith("special"):
            is_search_focused = (window.find_element_with_focus() == window['-SEARCH-'])
            if is_search_focused and current_suggestions:
                if "Up" in event or "16777235" in event:
                    suggestion_index = max(suggestion_index - 1, 0)
                    window['-SUGGESTIONS-'].update(set_to_index=[suggestion_index])
                elif "Down" in event or "16777237" in event:
                    suggestion_index = min(suggestion_index + 1, len(current_suggestions)-1)
                    window['-SUGGESTIONS-'].update(set_to_index=[suggestion_index])
                if "Return" in event or "16777220" in event:
                    if 0 <= suggestion_index < len(current_suggestions):
                        full_p, short_p = current_suggestions[suggestion_index]
                        if full_p not in selected_files:
                            selected_files.append(full_p)
                        update_selected_listbox(base_dir)
                    else:
                        typed = values['-SEARCH-'].strip()
                        if typed:
                            selected_files.append(typed)
                            update_selected_listbox(base_dir)
                    window['-SUGGESTIONS-'].update(values=[], visible=False)
                    current_suggestions.clear()
                    suggestion_index = -1
                    window['-SEARCH-'].update("")

        elif event.startswith("Return") or event.startswith("special 16777220"):
            is_search_focused = (window.find_element_with_focus() == window['-SEARCH-'])
            if is_search_focused and not current_suggestions:
                typed = values['-SEARCH-'].strip()
                if typed:
                    if typed not in selected_files:
                        selected_files.append(typed)
                    update_selected_listbox(base_dir)
                window['-SEARCH-'].update("")
                window['-SUGGESTIONS-'].update(values=[], visible=False)

        elif event == '-ADD_RECENT-':
            short_value = values['-RECENTS-']
            if short_value:
                try:
                    idx = [short_path(base_dir, r) for r in recents].index(short_value)
                    fullp = recents[idx]
                    if fullp not in selected_files:
                        selected_files.append(fullp)
                    update_selected_listbox(base_dir)
                except ValueError:
                    pass

        elif event == 'Remove Selected':
            to_remove = values['-SELECTED-']
            all_short = [short_path(base_dir, f) for f in selected_files]
            for shorty in to_remove:
                if shorty in all_short:
                    idx = all_short.index(shorty)
                    realp = selected_files[idx]
                    selected_files.remove(realp)
            update_selected_listbox(base_dir)

        elif event == 'Clear All':
            selected_files.clear()
            update_selected_listbox(base_dir)

        elif event == 'Fetch & Append':
            if (not local_mode) and (not sftp):
                show_toast("Not connected!")
                continue

            tree_sel = values['-TREE-']
            if tree_sel:
                for item in tree_sel:
                    try:
                        if local_mode:
                            st_mode = os.lstat(item).st_mode
                            if not os.path.isdir(item) and item not in selected_files:
                                selected_files.append(item)
                        else:
                            st_mode = sftp.lstat(item).st_mode
                            if not is_dir_attr(st_mode) and item not in selected_files:
                                selected_files.append(item)
                    except:
                        pass
                update_selected_listbox(base_dir)

            if not selected_files:
                show_toast("No files selected!", duration=2)
                continue

            new_chunks = []
            for fpath in selected_files:
                try:
                    if local_mode:
                        if os.path.isdir(fpath):
                            new_chunks.append(f"=== {fpath} ===\n[Directory, skipping]\n")
                        else:
                            with open(fpath, 'r') as f:
                                content = f.read()
                            new_chunks.append(f"=== {fpath} ===\n{content}\n")
                    else:
                        st_mode = sftp.lstat(fpath).st_mode
                        if is_dir_attr(st_mode):
                            new_chunks.append(f"=== {fpath} ===\n[Directory, skipping]\n")
                        else:
                            content = get_file_content_sftp(sftp, fpath)
                            new_chunks.append(f"=== {fpath} ===\n{content}\n")
                except Exception as e:
                    new_chunks.append(f"=== {fpath} ===\n[Error reading file: {e}]\n")

            current_text = window['-APPENDED-'].get()
            appended_text = current_text + "\n".join(new_chunks) + "\n"
            window['-APPENDED-'].update(appended_text, visible=True)
            window.refresh()

        elif event == '-COPY-':
            text_to_copy = window['-APPENDED-'].get()
            if text_to_copy.strip():
                os.system(f'echo \"{text_to_copy}\" | pbcopy')
                show_toast("Copied to clipboard!", duration=2)
            else:
                show_toast("No text to copy!", duration=2)

        elif event == '-CLEAR_TEXT-':
            window['-APPENDED-'].update("")
            
        elif event == '-ADD_ALL-':
            if current_selected_dir:
                try:
                    if local_mode:
                        entries = local_listdir_attr(current_selected_dir)
                        for e in entries:
                            full_path = os.path.join(current_selected_dir, e.filename)
                            if not os.path.isdir(full_path) and full_path not in selected_files:
                                selected_files.append(full_path)
                    else:
                        entries = sftp.listdir_attr(current_selected_dir)
                        for e in entries:
                            full_path = join_sftp_path(current_selected_dir, e.filename)
                            if not is_dir_attr(e.st_mode) and full_path not in selected_files:
                                selected_files.append(full_path)
                    update_selected_listbox(base_dir)
                except Exception as e:
                    show_toast(f"Cannot read folder: {e}")

        # NEW: Update token count on every event iteration.
        update_token_count()

    # Cleanup
    if sftp:
        sftp.close()
    if ssh:
        ssh.close()
    window.close()

if __name__ == "__main__":
    main()

#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Change to the script directory
cd "$SCRIPT_DIR"

# Initialize conda for the shell session
eval "$(conda shell.bash hook)"

# Activate the environment
conda activate append_file_gui
    
# Run the application
python append_file_gui.py 
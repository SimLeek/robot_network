#!/bin/bash

# Check if we're in a virtual environment
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "Already inside a virtual environment: $VIRTUAL_ENV"
else
    echo "Not in a virtual environment."

    # Define the name/path of the virtual environment
    VENV_PATH="./venv"

    # Check if the virtual environment already exists
    if [[ ! -d "$VENV_PATH" ]]; then
        echo "Creating a new virtual environment at $VENV_PATH with system packages..."

        # Create a virtual environment with access to system packages
        python3 -m venv --system-site-packages "$VENV_PATH"

        # Activate the virtual environment
        source "$VENV_PATH/bin/activate"

        echo "Virtual environment created and activated at $VENV_PATH"
    else
        echo "Virtual environment already exists at $VENV_PATH"

        # Activate the virtual environment
        source "$VENV_PATH/bin/activate"

        echo "Virtual environment activated at $VENV_PATH"
    fi
fi

export BUILT_FROM_SH=1
pip install -e .

#!/usr/bin/env bash
# exit on error
set -o errexit

# Force Python 3.9
echo "Installing Python 3.9..."
apt-get update
apt-get install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.9 python3.9-venv python3.9-dev

# Create and activate virtual environment
echo "Creating virtual environment..."
python3.9 -m venv .venv
source .venv/bin/activate

# Verify Python version
echo "Python version:"
python --version

# Install dependencies
echo "Installing dependencies..."
python -m pip install --upgrade pip
python -m pip install typing-extensions==4.8.0
python -m pip install keyring==9.3.1
python -m pip install -r requirements.txt

# Start the application
echo "Starting application..."
export PYTHONPATH=$PYTHONPATH:$(pwd)
python -m uvicorn main:app --host 0.0.0.0 --port $PORT 
python -m pip install -r engine/requirements.txt pyinstaller
pyinstaller --noconfirm --name engine --onefile engine/run_engine.py

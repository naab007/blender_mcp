import shutil
from pathlib import Path

SRC = Path(__file__).parent / "addon.py"
DST = Path(r"C:\Users\Naabin\AppData\Roaming\Blender Foundation\Blender\4.3\scripts\addons\addon.py")

shutil.copy2(SRC, DST)
print(f"Deployed: {SRC} -> {DST}")

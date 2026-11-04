import sys
import traceback
try:
    import tkinter as tk
    from Company_Kicad_DB import App
    root = tk.Tk()
    app = App(root)
    # perform a single update cycle
    root.update_idletasks()
    root.update()
    print('GUI smoke test OK')
    root.destroy()
except Exception:
    traceback.print_exc()
    print('GUI smoke test FAILED')
    sys.exit(1)

import _sandbox  # first: a manual preview must not write real state
import argparse
import customtkinter as ctk
from studio import StudioApp
ctk.set_appearance_mode('light')
app = StudioApp(argparse.Namespace(smoke_url=None, smoke_output=None))
app.smoke_mode = True
app.title('YT-DLP 下载器 1.4.0 · 集成验证')
app.show_page('collect')
app.mainloop()

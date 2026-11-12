// The two small programs in the Budget Tool folder. build.py compiles this file twice with the C# compiler that
// comes with Windows:  BudgetTool.exe (starts the app)  and  "Update Budget Tool.exe" (built with /define:UPDATER,
// starts the updater). Each one starts the private Python in runtime\ with a script from app\, then exits.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
#if UPDATER
        string script = Path.Combine(root, "app", "update_gui.py");
#else
        string script = Path.Combine(root, "app", "budget_tool.py");
#endif
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        if (!File.Exists(python) || !File.Exists(script))
        {
            MessageBox.Show("Some of Budget Tool's files are missing from:\n\n" + root +
                            "\n\nInstall Budget Tool into this folder again (your data is kept).",
                            "Budget Tool", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        string extra = "";
        foreach (string a in args)
            extra += " \"" + a.Replace("\"", "") + "\"";
        ProcessStartInfo start = new ProcessStartInfo(python, "-X utf8 \"" + script + "\"" + extra);
        start.WorkingDirectory = root;
        start.UseShellExecute = false;
        Process.Start(start);
        return 0;
    }
}

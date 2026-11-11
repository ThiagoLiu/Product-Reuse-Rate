using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyProduct("物料复用率工具")]
[assembly: AssemblyFileVersion("1.0.0.0")]

internal static class Launcher {
    [STAThread]
    private static void Main() {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string runtime = Path.Combine(root, "runtime");
            var start = new ProcessStartInfo(Path.Combine(runtime, "python.exe"), "-I -B \"" + Path.Combine(root, "app.py") + "\"");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardError = true;
            using (var child = Process.Start(start)) {
                string error = child.StandardError.ReadToEnd();
                child.WaitForExit();
                if (child.ExitCode != 0) throw new Exception(error.Trim());
            }
        } catch (Exception e) {
            MessageBox.Show("无法启动物料复用率工具。请确认完整解压工具文件夹，并且该目录可以写入。\n\n" + e.Message,
                "物料复用率工具", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}

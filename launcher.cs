// Native console launcher: .cmd drops embedded CR in `screen -X stuff`.
// This executable forwards Windows arguments losslessly without a cmd hop.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

public static class WinScreenLauncher
{
    static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { backslashes++; continue; }
            if (ch == '"') result.Append('\\', backslashes * 2 + 1);
            else result.Append('\\', backslashes);
            result.Append(ch);
            backslashes = 0;
        }
        result.Append('\\', backslashes * 2);
        result.Append('"');
        return result.ToString();
    }

    public static int Main(string[] args)
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string interpreter = Path.Combine(root, ".runtime", "Scripts", "python.exe");
            if (!File.Exists(interpreter)) interpreter = "python.exe";
            var arguments = new StringBuilder(Quote(Path.Combine(root, "screen.py")));
            foreach (string arg in args) arguments.Append(" ").Append(Quote(arg));
            var config = new ProcessStartInfo(interpreter, arguments.ToString());
            config.UseShellExecute = false;
            Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e) { e.Cancel = true; };
            using (var process = Process.Start(config))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("WinScreen: " + e.Message);
            return 1;
        }
    }
}

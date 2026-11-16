# Скриншот окна конкретного процесса (не путать с другими окнами BeamHub).
#   powershell -File tools\shot.ps1 -ProcessId 1234 -Out data\shot.png
param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][string]$Out
)
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System; using System.Drawing; using System.Drawing.Imaging; using System.Text; using System.Runtime.InteropServices;
public class ShotByPid {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static IntPtr Get(int target) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid != target || !IsWindowVisible(h)) return true;
      RECT r; GetWindowRect(h, out r);
      if ((r.Right - r.Left) > 500) { found = h; return false; }
      return true; }, IntPtr.Zero);
    return found;
  }
  public static string Shot(IntPtr h, string path) {
    SetForegroundWindow(h); System.Threading.Thread.Sleep(600);
    RECT r; GetWindowRect(h, out r); int w = r.Right - r.Left, ht = r.Bottom - r.Top;
    using (var bmp = new Bitmap(w, ht)) using (var g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc(); bool ok = PrintWindow(h, dc, 2); g.ReleaseHdc(dc);
      bmp.Save(path, ImageFormat.Png); return "ok=" + ok + " " + w + "x" + ht;
    }
  }
}
"@
$hwnd = [ShotByPid]::Get($ProcessId)
if ($hwnd -eq [IntPtr]::Zero) { Write-Host "окно процесса $ProcessId не найдено"; exit 1 }
Write-Host ([ShotByPid]::Shot($hwnd, $Out))

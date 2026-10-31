import subprocess
import sys
from pathlib import Path

TASK_NAME = "Clevo EC Fan Control"


def is_enabled():
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def set_enabled(enabled):
    if enabled:
        if getattr(sys, "frozen", False):
            executable = sys.executable
            arguments = "--background"
            working_directory = str(Path(executable).parent)
        else:
            executable = str(Path(sys.executable).with_name("pythonw.exe"))
            arguments = f'"{Path(__file__).with_name("clevo_fan_gui.py")}" --background'
            working_directory = str(Path(__file__).parent)
        script = r'''
$action = New-ScheduledTaskAction -Execute $args[0] -Argument $args[1] -WorkingDirectory $args[2]
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $args[3] -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Clevo EC fan control tray and saved curves" -Force | Out-Null
'''
        command = ["powershell.exe", "-NoProfile", "-Command", script,
                   executable, arguments, working_directory, TASK_NAME]
    else:
        command = ["powershell.exe", "-NoProfile", "-Command",
                   f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:`$false -ErrorAction SilentlyContinue"]
    result = subprocess.run(command, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "无法更新开机启动任务")

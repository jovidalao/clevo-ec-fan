import winreg

SUPPORTED_MODELS = {("HASEE Computer", "CNP6S")}


def system_identity():
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
        manufacturer = winreg.QueryValueEx(key, "SystemManufacturer")[0]
        product = winreg.QueryValueEx(key, "SystemProductName")[0]
    return manufacturer.strip(), product.strip()


def compatibility_error(allow_unsupported=False):
    if allow_unsupported:
        return None
    try:
        manufacturer, product = system_identity()
    except OSError as error:
        return f"无法确认电脑型号：{error}"
    if (manufacturer, product) not in SUPPORTED_MODELS:
        return (f"未验证的机型：{manufacturer} {product}\n\n"
                "为避免写入不兼容的 EC，程序已停止。\n"
                "开发者可在充分验证协议后使用 --allow-unsupported。")
    return None

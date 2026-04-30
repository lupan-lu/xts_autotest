#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GTV Skip Setup Tool - GTV 设备初始化工具

用于 ATS 测试服务器的设备准备阶段，支持三种工作模式：
  1. --gsi           : GSI 环境模式 — 连接 WiFi + 屏幕常亮
  2. --skip-setup    : 跳过开机向导 — 连接 WiFi + 屏幕常亮 + 禁用屏保
  3. --skip-setup-wifi: 跳过开机向导（无 WiFi）— 屏幕常亮 + 禁用屏保

使用方法：
    python gtv_skip_setup.py -s <设备序列号> --skip-setup
    python gtv_skip_setup.py -s <设备序列号> --gsi
    python gtv_skip_setup.py -s <设备序列号> --skip-setup-wifi

注意：
  - 需要设备已通过 ADB 连接
  - user 版本也支持（通过 settings put 命令）
"""

import subprocess
import sys
import time
import textwrap
from typing import Tuple


# ================= 配置区域 =================
# 默认 WiFi 配置（与 gtvFullSetup.py 保持一致）
DEFAULT_WIFI_SSID = "XR-APD-XTS"
DEFAULT_WIFI_PASS = "1234567890"

# 设备掉线检测配置
DEVICE_OFFLINE_TIMEOUT = 300  # 设备掉线最大等待时间（秒），5分钟
DEVICE_RECONNECT_CHECK_INTERVAL = 10  # 重连检测间隔（秒）
# ===========================================


# 全局掉线状态
_device_offline = False
_offline_start_time = None


class Colors:
    """终端颜色定义"""
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"


# ================= 日志工具 =================

def log(msg: str, color: str = Colors.RESET):
    """打印带颜色和时间戳的日志"""
    timestamp = time.strftime('%H:%M:%S')
    print(f"{color}[{timestamp}] {msg}{Colors.RESET}", flush=True)


def log_step(step: str):
    """打印步骤信息"""
    log(f">>> {step}", Colors.CYAN)


def log_success(msg: str):
    """打印成功信息"""
    log(f"✓ {msg}", Colors.GREEN)


def log_error(msg: str):
    """打印错误信息"""
    log(f"✗ {msg}", Colors.RED)


def log_warning(msg: str):
    """打印警告信息"""
    log(f"⚠ {msg}", Colors.YELLOW)


# ================= 设备连接管理 =================

def is_device_offline_error(output: str) -> bool:
    """判断是否为设备掉线错误"""
    offline_indicators = [
        "error: device",
        "not found",
        "device offline",
        "no devices/emulators found",
        "cannot connect",
        "closed",
        "connection refused",
    ]
    output_lower = output.lower()
    return any(indicator in output_lower for indicator in offline_indicators)


def check_device_connection(serial: str) -> bool:
    """
    检查设备连接状态
    Returns:
        True: 设备在线
        False: 设备掉线
    """
    global _device_offline, _offline_start_time

    try:
        cmd = ["adb", "-s", serial, "get-state"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = result.stdout.strip() + result.stderr.strip()

        if result.returncode == 0 and "device" in output:
            # 设备恢复连接
            if _device_offline:
                log_success("设备重新连接!")
                _device_offline = False
                _offline_start_time = None
            return True
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    return False


def wait_for_device_reconnect(serial: str) -> bool:
    """
    等待设备重连
    Returns:
        True: 设备重连成功或仍在等待中
        False: 等待超时，应中止
    """
    global _device_offline, _offline_start_time

    if not _device_offline:
        _device_offline = True
        _offline_start_time = time.time()
        log_warning(f"检测到设备掉线! 将等待最多 {DEVICE_OFFLINE_TIMEOUT} 秒...")

    elapsed = time.time() - _offline_start_time

    if elapsed >= DEVICE_OFFLINE_TIMEOUT:
        log_error(f"设备掉线超时! ({int(elapsed)}s 已等待, 最大 {DEVICE_OFFLINE_TIMEOUT}s)")
        return False

    # 检测是否重连成功
    if check_device_connection(serial):
        log_success(f"设备在 {int(elapsed)}s 后重新连接")
        return True

    remaining = int(DEVICE_OFFLINE_TIMEOUT - elapsed)
    log(f"设备仍然离线... ({int(elapsed)}s 已等待, 剩余 {remaining}s)", Colors.YELLOW)
    time.sleep(DEVICE_RECONNECT_CHECK_INTERVAL)
    return True  # 继续等待


def check_should_abort() -> bool:
    """检查是否应该因掉线超时而中止"""
    global _device_offline, _offline_start_time

    if _device_offline and _offline_start_time:
        elapsed = time.time() - _offline_start_time
        if elapsed >= DEVICE_OFFLINE_TIMEOUT:
            return True
    return False


# ================= ADB 命令封装 =================

def run_adb(serial: str, args: list, timeout: int = 30) -> Tuple[bool, str]:
    """
    执行 ADB 命令（带掉线检测和重连机制）
    返回: (是否成功, 输出信息)
    """
    global _device_offline, _offline_start_time

    cmd = ["adb", "-s", serial] + args

    # 如果设备已掉线，先等待重连
    while _device_offline:
        if not wait_for_device_reconnect(serial):
            return False, "Device offline timeout"
        if not _device_offline:
            break  # 设备已重连

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output = result.stdout.strip() + result.stderr.strip()

        # 检测是否为掉线错误
        if is_device_offline_error(output):
            _device_offline = True
            if _offline_start_time is None:
                _offline_start_time = time.time()

            # 尝试等待重连
            while _device_offline:
                if not wait_for_device_reconnect(serial):
                    return False, "Device offline timeout"
                if not _device_offline:
                    # 设备重连成功，重试命令
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                    output = result.stdout.strip() + result.stderr.strip()
                    return result.returncode == 0, output

        return result.returncode == 0, output

    except subprocess.TimeoutExpired:
        # 超时可能是设备掉线
        if not check_device_connection(serial):
            _device_offline = True
            if _offline_start_time is None:
                _offline_start_time = time.time()
            return False, "Command timeout (device may be offline)"
        return False, "Command timeout"
    except Exception as e:
        error_msg = str(e)
        if is_device_offline_error(error_msg):
            _device_offline = True
            if _offline_start_time is None:
                _offline_start_time = time.time()
        return False, error_msg


def adb_shell(serial: str, cmd: str, timeout: int = 30) -> Tuple[bool, str]:
    """执行 adb shell 命令"""
    return run_adb(serial, ["shell", cmd], timeout)


# ================= 设备操作 =================

def wait_for_device(serial: str, timeout: int = 60) -> bool:
    """等待设备 ADB 可用（集成掉线检测）"""
    global _device_offline, _offline_start_time

    log_step(f"等待设备 ADB 连接 (最长 {timeout}s)...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        # 如果已检测到掉线超时，直接返回失败
        if _device_offline and _offline_start_time:
            elapsed = time.time() - _offline_start_time
            if elapsed >= DEVICE_OFFLINE_TIMEOUT:
                log_error(f"设备掉线超时 ({int(elapsed)}s)")
                return False

        success, output = run_adb(serial, ["get-state"], timeout=5)
        if success and "device" in output:
            log_success(f"设备 {serial} 已连接")
            # 重置掉线状态
            _device_offline = False
            _offline_start_time = None
            return True
        time.sleep(2)

    log_error(f"等待设备超时 ({timeout}s)")
    return False


def wait_for_boot_complete(serial: str, timeout: int = 120) -> bool:
    """等待设备完全启动"""
    log_step(f"等待设备启动完成 (最长 {timeout}s)...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        success, output = adb_shell(serial, "getprop sys.boot_completed")
        if success and output.strip() == "1":
            log_success("设备启动完成")
            return True
        time.sleep(3)

    log_warning("等待启动超时，继续执行...")
    return False


def set_setup_complete(serial: str) -> bool:
    """设置 setup_complete 属性跳过开机向导"""
    log_step("设置 Setup Complete 属性...")

    settings_to_set = [
        ("secure", "user_setup_complete", "1"),
        ("secure", "tv_user_setup_complete", "1"),
        # 注意: 不设置 device_provisioned，会导致设备重启
    ]

    all_success = True
    for namespace, key, value in settings_to_set:
        cmd = f"settings put {namespace} {key} {value}"
        success, output = adb_shell(serial, cmd)

        if success:
            log_success(f"设置 {namespace}/{key} = {value}")
        else:
            log_error(f"设置 {namespace}/{key} 失败: {output}")
            all_success = False

    return all_success


def verify_setup_complete(serial: str) -> bool:
    """验证 setup_complete 设置是否生效"""
    log_step("验证 Setup Complete 设置...")

    checks = [
        ("secure", "user_setup_complete"),
        ("secure", "tv_user_setup_complete"),
    ]

    all_ok = True
    for namespace, key in checks:
        cmd = f"settings get {namespace} {key}"
        success, output = adb_shell(serial, cmd)
        value = output.strip()

        if value == "1":
            log_success(f"{namespace}/{key} = {value} ✓")
        else:
            log_warning(f"{namespace}/{key} = {value} (期望值: 1)")
            all_ok = False

    return all_ok


def force_stop_setup_wizard(serial: str):
    """强制停止 Setup Wizard 应用"""
    log_step("强制停止 Setup Wizard...")

    # GTV Setup Wizard 包名
    packages = [
        "com.google.android.tungsten.setupwraith",  # GTV Setup Wizard
        "com.google.android.setupwizard",           # 标准 Setup Wizard
        "com.android.provision",                     # Provision app
    ]

    for pkg in packages:
        adb_shell(serial, f"am force-stop {pkg}")
        adb_shell(serial, f"pm disable-user --user 0 {pkg}")  # 禁用组件

    log_success("Setup Wizard 已停止")


def connect_wifi(serial: str, ssid: str = DEFAULT_WIFI_SSID,
                 password: str = DEFAULT_WIFI_PASS) -> bool:
    """连接 WiFi（使用内置默认凭据）"""
    log_step(f"连接 WiFi: {ssid}...")

    # 先确保 WiFi 开关已打开
    log("确保 WiFi 已启用...")
    adb_shell(serial, "svc wifi enable")
    time.sleep(2)

    # 使用 cmd wifi connect-network 命令（Android 11+）
    cmd = f"cmd wifi connect-network {ssid} wpa2 {password}"
    success, output = adb_shell(serial, cmd, timeout=15)

    if not success:
        log_warning(f"WiFi 连接命令返回: {output}")
        # 尝试备用方法
        log("尝试备用 WiFi 连接方法...")
        cmd = f"cmd wifi add-network {ssid} wpa2 {password}"
        adb_shell(serial, cmd, timeout=10)

    # 等待连接
    log("等待 WiFi 连接...")
    time.sleep(5)

    # 多次检查连接状态（最多等待 15 秒）
    for i in range(3):
        # 检查方法1: 使用 ip 命令查看 wlan0 是否有 IP
        success, output = adb_shell(serial, "ip addr show wlan0")
        if "inet " in output and "inet6" not in output.split("inet ")[1].split()[0]:
            try:
                ip_line = [l for l in output.split('\n') if 'inet ' in l and 'inet6' not in l][0]
                ip_addr = ip_line.strip().split()[1].split('/')[0]
                log_success(f"WiFi 已连接，IP: {ip_addr}")
                return True
            except (IndexError, ValueError):
                pass

        if "inet " in output:
            log_success("WiFi 已获取 IP 地址")
            return True

        # 检查方法2: dumpsys wifi
        success, output = adb_shell(serial, "dumpsys wifi | grep 'mNetworkInfo'")
        if "CONNECTED" in output.upper():
            log_success(f"WiFi 已连接: {ssid}")
            return True

        if i < 2:
            log(f"WiFi 连接中，等待... ({i + 1}/3)")
            time.sleep(5)

    log_warning("WiFi 连接状态未确认，请手动验证")
    return False


def set_stay_awake(serial: str) -> bool:
    """设置 Stay Awake（屏幕常亮）"""
    log_step("设置 Stay Awake (屏幕常亮)...")

    # 方法1: 使用 svc power stayon
    # 参数: true (充电时常亮), usb (USB充电时常亮), ac (AC充电时常亮), wireless (无线充电时常亮)
    adb_shell(serial, "svc power stayon true")

    # 方法2: 设置 Settings.Global.stay_on_while_plugged_in
    # 值: 0=关闭, 1=AC, 2=USB, 4=Wireless, 7=All
    adb_shell(serial, "settings put global stay_on_while_plugged_in 7")

    # 验证
    success, output = adb_shell(serial, "settings get global stay_on_while_plugged_in")
    if output.strip() in ["7", "3"]:
        log_success("Stay Awake 已启用")
        return True
    else:
        log_warning(f"Stay Awake 设置值: {output.strip()}")
        return False


def disable_screensaver(serial: str) -> bool:
    """禁用屏保"""
    log_step("禁用屏保...")

    # Android TV 屏保相关设置
    settings_to_set = [
        ("secure", "screensaver_enabled", "0"),
        ("secure", "screensaver_activate_on_dock", "0"),
        ("secure", "screensaver_activate_on_sleep", "0"),
        ("system", "screen_off_timeout", "2147483647"),  # 最大值，约68年
    ]

    for namespace, key, value in settings_to_set:
        cmd = f"settings put {namespace} {key} {value}"
        success, output = adb_shell(serial, cmd)
        if success:
            log(f"  {namespace}/{key} = {value}")

    # 停止 DreamService (屏保服务)
    adb_shell(serial, "am force-stop com.android.dreams.basic")
    adb_shell(serial, "am force-stop com.google.android.backdrop")

    log_success("屏保已禁用")
    return True


def unlock_screen(serial: str):
    """唤醒并解锁屏幕"""
    log_step("唤醒屏幕...")

    # 唤醒屏幕
    adb_shell(serial, "input keyevent KEYCODE_WAKEUP")
    time.sleep(1)

    # 按 Home 键
    adb_shell(serial, "input keyevent KEYCODE_HOME")
    time.sleep(1)

    log_success("屏幕已唤醒")


def print_device_info(serial: str):
    """打印设备信息"""
    log_step("设备信息:")

    props = [
        ("Android 版本", "ro.build.version.release"),
        ("SDK 版本", "ro.build.version.sdk"),
        ("设备型号", "ro.product.model"),
        ("Build 类型", "ro.build.type"),
        ("Build ID", "ro.build.display.id"),
    ]

    for name, prop in props:
        success, output = adb_shell(serial, f"getprop {prop}")
        if success:
            log(f"  {name}: {output.strip()}")


def print_summary(serial: str):
    """打印最终状态汇总"""
    log("\n" + "=" * 50, Colors.BOLD)
    log("设备状态汇总", Colors.BOLD)
    log("=" * 50, Colors.BOLD)

    # Setup Complete
    success, val = adb_shell(serial, "settings get secure user_setup_complete")
    status = "✓" if val.strip() == "1" else "✗"
    log(f"  Setup Complete: {status}")

    # WiFi
    success, output = adb_shell(serial, "ip addr show wlan0 | grep 'inet '")
    if "inet " in output:
        ip = output.strip().split()[1].split('/')[0]
        log(f"  WiFi IP: {ip}")
    else:
        log("  WiFi: 未连接")

    # Stay Awake
    success, val = adb_shell(serial, "settings get global stay_on_while_plugged_in")
    status = "✓" if val.strip() in ["7", "3"] else "✗"
    log(f"  Stay Awake: {status}")

    # Screensaver
    success, val = adb_shell(serial, "settings get secure screensaver_enabled")
    status = "✓ (已禁用)" if val.strip() == "0" else "✗ (启用中)"
    log(f"  屏保: {status}")

    log("=" * 50, Colors.BOLD)


# ================= 参数解析 =================

def build_help_text() -> str:
    """构建中文详细帮助文本"""
    return textwrap.dedent("""\
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
          GTV Skip Setup Tool v2.0 — GTV 设备初始化工具
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        用于 ATS 测试服务器的设备准备阶段，根据不同场景自动完成
        设备初始化配置。支持三种互斥的工作模式，请根据实际需求选择。

        必填参数:
        ─────────────────────────────────────────────────────
          -s <序列号>          设备序列号，即 `adb devices` 中显示
                               的设备标识符。支持 USB 直连序列号
                               或网络地址（如 192.168.1.100:5555）。

        工作模式（三选一，必须指定一个）:
        ─────────────────────────────────────────────────────
          --gsi                GSI 刷机环境模式。
                               适用于刷入 GSI 镜像后的设备，该模式
                               没有开机向导，仅需基础网络和屏幕配置。
                               执行操作: ✓连接WiFi  ✓屏幕常亮

          --skip-setup         跳过开机向导模式（完整版）。
                               适用于恢复出厂设置后的设备，跳过 GTV
                               开机向导（无需登录 Google 账号），并
                               完成全部初始化配置。
                               执行操作: ✓跳过向导  ✓连接WiFi
                                         ✓屏幕常亮  ✓禁用屏保

          --skip-setup-wifi    跳过开机向导模式（无 WiFi）。
                               与 --skip-setup 相同，但不连接 WiFi。
                               适用于设备已有网络或需手动配置网络的
                               场景。
                               执行操作: ✓跳过向导  ✓屏幕常亮
                                         ✓禁用屏保

        其他参数:
        ─────────────────────────────────────────────────────
          -h, --help           显示此帮助信息并退出。

        使用示例:
        ─────────────────────────────────────────────────────
          # GSI 环境 — 连接 WiFi 并保持屏幕常亮
          python gtv_skip_setup.py -s 192.168.1.100:5555 --gsi

          # 跳过开机向导 — 完整初始化（含 WiFi）
          python gtv_skip_setup.py -s DEVICE_SERIAL --skip-setup

          # 跳过开机向导 — 不连 WiFi
          python gtv_skip_setup.py -s DEVICE_SERIAL --skip-setup-wifi

        注意事项:
        ─────────────────────────────────────────────────────
          • 设备需已通过 ADB 连接（USB 或网络均可）
          • user 版本和 userdebug 版本均支持
          • WiFi 默认连接 SSID: {ssid}
          • 设备掉线后自动等待重连（最长 {timeout}s）
        """.format(ssid=DEFAULT_WIFI_SSID, timeout=DEVICE_OFFLINE_TIMEOUT))


def parse_args():
    """解析命令行参数"""
    # 无参数时打印帮助
    if len(sys.argv) == 1:
        print(build_help_text())
        sys.exit(0)

    # 检查 -h / --help
    if '-h' in sys.argv or '--help' in sys.argv:
        print(build_help_text())
        sys.exit(0)

    serial = None
    mode = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg == '-s':
            # -s 后必须跟序列号
            if i + 1 >= len(sys.argv):
                log_error("错误: -s 参数后必须指定设备序列号")
                print("\n提示: python gtv_skip_setup.py -s <设备序列号> --skip-setup")
                sys.exit(1)
            serial = sys.argv[i + 1]
            # 检查序列号是否像一个参数而非值
            if serial.startswith('-'):
                log_error(f"错误: -s 参数后的值 '{serial}' 无效，请提供正确的设备序列号")
                sys.exit(1)
            i += 2
        elif arg == '--gsi':
            if mode is not None:
                log_error(f"错误: --gsi 与 --{mode} 不能同时使用，请只选择一种工作模式")
                sys.exit(1)
            mode = 'gsi'
            i += 1
        elif arg == '--skip-setup':
            if mode is not None:
                log_error(f"错误: --skip-setup 与 --{mode} 不能同时使用，请只选择一种工作模式")
                sys.exit(1)
            mode = 'skip-setup'
            i += 1
        elif arg == '--skip-setup-wifi':
            if mode is not None:
                log_error(f"错误: --skip-setup-wifi 与 --{mode} 不能同时使用，请只选择一种工作模式")
                sys.exit(1)
            mode = 'skip-setup-wifi'
            i += 1
        else:
            log_error(f"错误: 未知参数 '{arg}'")
            print("\n使用 -h 或 --help 查看帮助信息")
            sys.exit(1)

    # 校验必填参数
    if serial is None:
        log_error("错误: 必须使用 -s 指定设备序列号")
        print("\n提示: python gtv_skip_setup.py -s <设备序列号> --skip-setup")
        sys.exit(1)

    if mode is None:
        log_error("错误: 必须指定一种工作模式 (--gsi / --skip-setup / --skip-setup-wifi)")
        print("\n使用 -h 或 --help 查看帮助信息")
        sys.exit(1)

    return serial, mode


# ================= 主入口 =================

# 模式 → 显示名称 映射
MODE_DISPLAY_NAMES = {
    'gsi': 'GSI 模式 | WiFi + 屏幕常亮',
    'skip-setup': '跳过开机向导 | WiFi + 屏幕常亮 + 禁用屏保',
    'skip-setup-wifi': '跳过开机向导（无WiFi）| 屏幕常亮 + 禁用屏保',
}


def main():
    """主函数"""
    global _device_offline, _offline_start_time

    serial, mode = parse_args()

    # 打印 Banner
    print()
    log("=" * 50, Colors.BOLD)
    log(" GTV Skip Setup Tool v2.0", Colors.BOLD)
    log(f" {MODE_DISPLAY_NAMES[mode]}", Colors.BOLD)
    log(f" 掉线超时: {DEVICE_OFFLINE_TIMEOUT}s", Colors.BOLD)
    log("=" * 50, Colors.BOLD)
    print()

    # Step 1: 等待设备连接
    if not wait_for_device(serial):
        log_error("无法连接设备，退出")
        sys.exit(1)

    # 打印设备信息
    print_device_info(serial)
    print()

    if check_should_abort():
        log_error("设备掉线超时，中止执行")
        sys.exit(1)

    # Step 2: 等待启动完成
    wait_for_boot_complete(serial)
    if check_should_abort():
        log_error("设备掉线超时，中止执行")
        sys.exit(1)

    # Step 3: 跳过开机向导（--skip-setup 和 --skip-setup-wifi）
    if mode in ('skip-setup', 'skip-setup-wifi'):
        set_setup_complete(serial)
        time.sleep(1)

        if check_should_abort():
            log_error("设备掉线超时，中止执行")
            sys.exit(1)

        force_stop_setup_wizard(serial)
        time.sleep(2)

        if check_should_abort():
            log_error("设备掉线超时，中止执行")
            sys.exit(1)

        verify_setup_complete(serial)
        print()

    # Step 4: 连接 WiFi（--gsi 和 --skip-setup）
    if mode in ('gsi', 'skip-setup'):
        connect_wifi(serial)
        print()
        if check_should_abort():
            log_error("设备掉线超时，中止执行")
            sys.exit(1)

    # Step 5: 设置屏幕常亮（所有模式）
    set_stay_awake(serial)

    if check_should_abort():
        log_error("设备掉线超时，中止执行")
        sys.exit(1)

    # Step 6: 禁用屏保（--skip-setup 和 --skip-setup-wifi）
    if mode in ('skip-setup', 'skip-setup-wifi'):
        disable_screensaver(serial)
    print()

    # Step 7: 唤醒屏幕
    unlock_screen(serial)

    # 打印汇总
    print_summary(serial)

    log_success("\n完成! 设备已准备就绪，可用于 xTS 测试。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log_warning("操作已被用户中断")
        sys.exit(1)
    except Exception as e:
        log_error(f"发生错误: {e}")
        sys.exit(1)
